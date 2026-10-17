import time

import pytest

from constructos_engine import store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    """Every test gets its own local file store."""
    store.use_backend(store.FileBackend(tmp_path))
    yield
    store.use_backend(None)


def wait_run(client, pid: str, run_id: str, timeout_s: float = 240) -> dict:
    """Poll a background layout run until it leaves queued/running."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = client.get(f"/projects/{pid}/layouts/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish")
