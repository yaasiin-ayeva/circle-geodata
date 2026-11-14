"""Checks the hiding places for the game's heat chase (scripts/build_district.py hiding_places) on synthetic streets.

    python3 tests/test_hiding.py
"""
import math
import os
import sys

from shapely.geometry import LineString, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# A residential street (6 m wide) 600 m long along x at z = 0: the north side is built up end to end, the south side
# is open. A primary road runs parallel 40 m south.
street = (1, "residential", LineString([(0, 0), (600, 0)]))
main = (2, "primary", LineString([(0, -40), (600, -40)]))
houses = [box(x, 4, x + 18, 20) for x in range(0, 600, 20)]
places = bd.hiding_places([street, main], houses, [])
check("places on the open side of the street", len(places) >= 2, f"{len(places)} places")
check("none on the built-up side", all(p["z"] < 0 for p in places), str([p["z"] for p in places]))
check("kept apart", all(math.hypot(a["x"] - b["x"], a["z"] - b["z"]) >= bd.HIDING_SPACING_M
                        for i, a in enumerate(places) for b in places[i + 1:]))
w, d = bd.HIDING_SIZE_M
check("the plot stands back from the carriageway and clear of the primary road",
      all(abs(p["z"]) >= 3 + 1.2 + d / 2 - 0.01 and abs(p["z"] + 40) >= 6.5 + 1 + d / 2 - 0.01 for p in places),
      str([p["z"] for p in places]))
check("the open front faces the street (north)", all(abs(((p["yaw"] + 180) % 360) - 180) < 1 for p in places),
      str([p["yaw"] for p in places]))
check("kinds take turns", [p["kind"] for p in places][:3] == list(bd.HIDING_KINDS[:min(3, len(places))]))
check("the same places on a rerun", bd.hiding_places([street, main], houses, []) == places)

# A wall along the open side, between the street and the plots, closes every way in; a street of the primary class
# takes none; a parked car or a pole in front of a plot moves it.
wall = LineString([(0, -4), (600, -4)])
check("a wall along the kerb leaves no way in", bd.hiding_places([street, main], houses, [wall]) == [])
check("no places beside a main road", bd.hiding_places([main], [], []) == [])
first = places[0]
car = {"x": first["x"], "z": -3.6}
moved = bd.hiding_places([street, main], houses, [], parked=[car])
check("a parked car in the way moves the place", all(math.hypot(p["x"] - first["x"], p["z"] - first["z"]) > 1 for p in moved))
check("nothing at all gives no places", bd.hiding_places([], [], []) == [])

print("hiding places: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
