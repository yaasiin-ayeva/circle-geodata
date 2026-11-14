"""Checks the outlines the game's cars collide with (scripts/build_district.py solid_outlines) on synthetic shapes.

    python3 tests/test_solid.py
"""
import os
import sys

from shapely.geometry import LineString, Point, Polygon, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# A 10 x 8 m house with a 0.2 m notch the simplification removes, a kiosk too small to matter, a landmark's round
# keep-out, and a compound wall with a kink.
house = Polygon([(0, 0), (10, 0), (10, 4), (10.2, 4.1), (10, 4.2), (10, 8), (0, 8)])
kiosk = box(20, 0, 21.5, 2)
lighthouse = Point(40, 0).buffer(4.0)
wall = LineString([(0, -5), (6, -5.1), (12, -5), (12.2, -5.02)])
out = bd.solid_outlines([house, kiosk, lighthouse], [wall])

rings = out["buildings"]
check("one ring per footprint above 4 m2", len(rings) == 2, f"{len(rings)} rings")
first = rings[0]
check("a ring is a flat list of x, z pairs", len(first) % 2 == 0 and all(isinstance(v, float) for v in first))
check("the house loses its 0.2 m notch", len(first) // 2 == 4, f"{len(first) // 2} points")
check("the ring does not repeat its first point", first[:2] != first[-2:])
check("coordinates are rounded to 0.1 m", all(abs(v * 10 - round(v * 10)) < 1e-9 for r in rings for v in r))
check("the landmark's round keep-out stays solid", len(rings[1]) // 2 >= 8, f"{len(rings[1]) // 2} points")
walls = out["walls"]
check("the wall is one line", len(walls) == 1)
check("the wall keeps its ends and loses its 0.1 m wobble", walls[0][:2] == [0.0, -5.0] and len(walls[0]) // 2 == 2,
      str(walls[0]))
check("nothing at all gives empty lists", bd.solid_outlines([], []) == {"buildings": [], "walls": []})

print("solid outlines: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
