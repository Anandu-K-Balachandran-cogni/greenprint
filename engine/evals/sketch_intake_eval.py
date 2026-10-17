"""Evaluate brief intake from hand sketches and plot drawings (Gemini vision) and from briefs typed in
JAPAC languages, against the values written on each input.

    uv run python evals/sketch_intake_eval.py [repeats]
"""

import json
import os
import tempfile
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("CONSTRUCTOS_DATA_DIR", tempfile.mkdtemp(prefix="intake-eval-"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from constructos_engine import brief, intake  # noqa: E402

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
SK = ROOT / "tests/fixtures/sketches"
BEDROOMS = {"bedroom", "master_bedroom", "guest_room"}

# name, images, typed text, expected: plot (w, d, unit), storeys, bedrooms, parking, extra room types, entrance
CASES = [
    ("plot survey drawing", [("plot_survey.png", "image/png")], None, ((30, 40, "ft"), 1, 2, 1, {"kitchen"}, "south")),
    ("house sketch", [("house_sketch.png", "image/png")], None, ((35, 50, "ft"), 2, 3, 1, {"kitchen", "living"}, None)),
    ("sketch photo + Hindi note", [("house_sketch_photo.jpg", "image/jpeg")], "मुझे एक पूजा कमरा भी चाहिए।",
     ((35, 50, "ft"), 2, 3, 1, {"prayer"}, None)),
    ("Hindi typed", [], "मुझे 30 x 50 फीट के प्लॉट पर दो मंज़िला घर चाहिए, तीन बेडरूम, एक कार पार्किंग और खुली रसोई।",
     ((30, 50, "ft"), 2, 3, 1, {"kitchen"}, None)),
    ("Tamil typed", [], "40 x 60 அடி நிலத்தில் இரண்டு மாடி வீடு வேண்டும், நான்கு படுக்கையறைகள், இரண்டு கார் நிறுத்தம்.",
     ((40, 60, "ft"), 2, 4, 2, set(), None)),
    ("Japanese typed", [], "12m×15mの土地に2階建ての家を建てたいです。寝室は3つ、駐車場は1台分。",
     ((12, 15, "m"), 2, 3, 1, set(), None)),
    ("Bahasa Indonesia typed", [], "Saya ingin rumah satu lantai di tanah 10 x 20 meter dengan dua kamar tidur dan satu tempat parkir mobil.",
     ((10, 20, "m"), 1, 2, 1, set(), None)),
    ("Romanised Hindi typed", [], "30x40 feet plot pe ek manzil ka ghar chahiye, do bedroom, ek car parking aur kitchen.",
     ((30, 40, "ft"), 1, 2, 1, {"kitchen"}, None)),
]


def run(images, text):
    started = time.monotonic()
    if images:
        reading = intake.read(text, [((SK / n).read_bytes(), m) for n, m in images])
        words = reading.english_brief
    else:
        words = text
    b = brief.extract(words)
    return b, round(time.monotonic() - started, 1)


def checks(b, expected):
    (w, d, unit), storeys, bedrooms, parking, extra, entrance = expected
    plot = sorted([b.plot.width or 0, b.plot.depth or 0]) == sorted([w, d]) and b.plot.unit == unit
    types = [r.type for r in b.rooms]
    out = {"plot size and unit": plot, "floors": b.storeys == storeys,
           "bedrooms": sum(t in BEDROOMS for t in types) == bedrooms, "parking": b.parking_spaces == parking,
           "named rooms": extra <= set(types)}
    if entrance:
        out["entrance side"] = b.entrance_facing == entrance
    return out


def main():
    rows, secs_all = [], []
    for name, images, text, expected in CASES:
        passed = total = 0
        secs = []
        for _ in range(REPEATS):
            b, s = run(images, text)
            c = checks(b, expected)
            secs.append(s); total += len(c); passed += sum(c.values())
            if not all(c.values()):
                print(f"  ✗ {name}: {[k for k, v in c.items() if not v]} · plot {b.plot.width}x{b.plot.depth} {b.plot.unit} · "
                      f"floors {b.storeys} · rooms {[r.type for r in b.rooms]} · parking {b.parking_spaces} · entrance {b.entrance_facing}")
        secs_all += secs
        rows.append((name, passed, total, statistics.median(secs)))
    print(f"\nSketch and multilingual intake · {REPEATS} runs per case\n")
    print(f"{'case':28} {'checks':>9} {'median s':>9}")
    for n, p, t, m in rows:
        print(f"{n:28} {p:>4}/{t:<4} {m:9.1f}")
    p, t = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print(f"{'TOTAL':28} {p:>4}/{t:<4} {statistics.median(secs_all):9.1f}   ({100 * p / t:.0f}% of checks)")
    Path(__file__).with_suffix(".results.json").write_text(json.dumps(
        {"repeats": REPEATS, "ai_usage": __import__("constructos_engine.usage", fromlist=["summary"]).summary(), "cases": [dict(zip(("name", "passed", "total", "median_s"), r)) for r in rows]}, indent=1))


if __name__ == "__main__":
    main()
