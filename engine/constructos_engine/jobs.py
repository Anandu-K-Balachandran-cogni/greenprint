"""Background layout jobs with a persisted record, a lease and a heartbeat.

The run document is written (status ``queued``) before the work is dispatched,
so a job is never lost silently. The worker holds a random lease and every
write it makes checks that lease inside the store's read-modify-write, so:

- a run the reconciler marked ``interrupted`` (no heartbeat for STALE_S, e.g.
  the instance was stopped) can never be overwritten by a late worker;
- the result is published once, by the lease holder.

Workers run in this process (a thread pool). On Cloud Run the engine runs with
CPU always allocated so work continues after the response is sent.
"""

import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from . import store

log = logging.getLogger(__name__)
HEARTBEAT_S = float(os.getenv("CONSTRUCTOS_JOB_HEARTBEAT_S", "10"))
STALE_S = float(os.getenv("CONSTRUCTOS_JOB_STALE_S", "90"))
_executor = ThreadPoolExecutor(max_workers=int(os.getenv("CONSTRUCTOS_JOB_WORKERS", "2")), thread_name_prefix="job")
_owned: dict[tuple[str, str], str] = {}  # (project, run) → lease held by this process
_owned_lock = threading.Lock()
_heartbeat_started = threading.Event()
ACTIVE = ("queued", "running")


class LeaseLost(RuntimeError):
    """The run is no longer this worker's to write (interrupted or already finished)."""


def new_job(total: int) -> dict:
    now = store._now()
    return {"lease": uuid.uuid4().hex, "queued_at": now, "heartbeat_at": now, "started_at": None,
            "finished_at": None, "progress": {"done": 0, "total": total}, "error": None}


class Job:
    def __init__(self, project_id: str, run_id: str, lease: str):
        self.project_id, self.run_id, self.lease = project_id, run_id, lease

    def update(self, fn: Callable[[dict], None]) -> dict:
        def apply(run: dict) -> dict:
            if run.get("job", {}).get("lease") != self.lease or run.get("status") not in ACTIVE:
                raise LeaseLost(self.run_id)
            fn(run)
            run["job"]["heartbeat_at"] = store._now()
            return run
        return store.mutate_run(self.project_id, self.run_id, apply)


def start(project_id: str, run: dict, work: Callable[[Job], None]) -> None:
    """Dispatch `work` for a run that has already been saved with status 'queued' and a job record."""
    lease = run["job"]["lease"]
    with _owned_lock:
        _owned[(project_id, run["id"])] = lease
    _ensure_heartbeat()
    _executor.submit(_execute, Job(project_id, run["id"], lease), work)


def _execute(job: Job, work: Callable[[Job], None]) -> None:
    try:
        job.update(lambda r: (r.update(status="running"), r["job"].update(started_at=store._now())))
        work(job)

        def finish(r):
            r["status"] = "complete"
            r["job"].update(finished_at=store._now(), lease=None)
        job.update(finish)
    except LeaseLost:
        log.warning("job %s lost its lease; result discarded", job.run_id)
    except Exception as e:  # noqa: BLE001 - any failure is recorded on the run
        log.exception("job %s failed", job.run_id)

        def fail(r):
            r["status"] = "failed"
            r["job"].update(finished_at=store._now(), lease=None, error=f"{type(e).__name__}: {e}"[:500])
        try:
            job.update(fail)
        except LeaseLost:
            pass
    finally:
        with _owned_lock:
            _owned.pop((job.project_id, job.run_id), None)


def _ensure_heartbeat() -> None:
    if _heartbeat_started.is_set():
        return
    _heartbeat_started.set()
    threading.Thread(target=_heartbeat_loop, name="job-heartbeat", daemon=True).start()


def _heartbeat_loop() -> None:
    stop = threading.Event()
    while not stop.wait(HEARTBEAT_S):
        with _owned_lock:
            owned = list(_owned.items())
        for (project_id, run_id), lease in owned:
            try:
                Job(project_id, run_id, lease).update(lambda r: None)
            except LeaseLost:
                with _owned_lock:
                    _owned.pop((project_id, run_id), None)
            except Exception:  # noqa: BLE001 - a missed heartbeat is retried next tick
                log.exception("heartbeat failed for %s", run_id)


def _age_s(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


def reconcile(project_id: str, run: dict) -> dict:
    """Mark a run interrupted if its worker stopped heartbeating; its lease is revoked first."""
    job = run.get("job")
    if run.get("status") not in ACTIVE or not job or _age_s(job["heartbeat_at"]) < STALE_S:
        return run

    def interrupt(r: dict) -> dict:
        if r.get("status") in ACTIVE and _age_s(r["job"]["heartbeat_at"]) >= STALE_S:
            r["status"] = "interrupted"
            r["job"].update(lease=None, finished_at=store._now(),
                            error="The engine stopped before this finished. Generate again.")
        return r
    return store.mutate_run(project_id, run["id"], interrupt)


def wait_idle(timeout_s: float = 300) -> bool:
    """Block until this process holds no jobs (tests)."""
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with _owned_lock:
            if not _owned:
                return True
        time.sleep(0.05)
    return False
