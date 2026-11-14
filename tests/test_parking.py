"""Checks the parked places along the streets (build_district.place_parked, DISTRICT_RULES "parking") on a synthetic
street plan.

    python3 tests/test_parking.py
"""
import math
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


# Metres, x east and y north. A residential main street along y = 0 in two segments: the first runs on through the
# T-junction with a side street at x = 0, the second continues it from x = 80 (no junction there). A tertiary road
# crosses at x = -120, High Street (primary) runs along y = -80, another primary road along y = 150, a service lane
# alongside the main street, and a residential road 600 m north, beyond 250 m of the lap.
pieces = [
    ("main-a", "residential", "Main Street", LineString([(-150, 0), (80, 0)])),
    ("main-b", "residential", "Main Street", LineString([(80, 0), (150, 0)])),
    ("side", "residential", "Side Street", LineString([(0, 0), (0, 120)])),
    ("cross", "tertiary", "Cross Road", LineString([(-120, -100), (-120, 100)])),
    ("high", "primary", "High Street", LineString([(-150, -80), (150, -80)])),
    ("other", "primary", "Other Road", LineString([(-150, 150), (150, 150)])),
    ("lane", "service", "", LineString([(-90, -4.5), (-60, -4.5)])),
    ("far", "residential", "Far Street", LineString([(-150, 600), (150, 600)])),
]
JUNCTIONS = [(0, 0), (-120, 0), (-120, -80)]
# A row of houses on the street line north of the main street, and one 0.5 m back from its south edge.
buildings = [box(20, 3, 60, 12), box(-40, -12, -30, -3.5)]
lap = {"path": [-150, 0, 150, 0], "spawn": {"x": 100.0, "y": 0.5, "z": 0.0, "yaw": 90.0}}

rule = bd.DISTRICT_RULES["jamestown"]["parking"]
every = dict(rule, take=1.0, streets={"High Street": 1.0})   # every place taken: only the clearances stop a car

found = bd.junctions([l for _, _, _, l in pieces])
check("junctions: the T, the crossings, not the continued street",
      sorted((round(p.x), round(p.y)) for p in found) == sorted(JUNCTIONS), [(round(p.x), round(p.y)) for p in found])


def road_of(c):
    return min(pieces, key=lambda p: p[3].distance(Point(c["x"], c["z"])))


def body(c):
    a = math.radians(c["yaw"])
    f, l = (math.sin(a), math.cos(a)), (-math.cos(a), math.sin(a))
    hw, hl = bd.CAR_BODY_M[0] / 2, bd.CAR_BODY_M[1] / 2
    return Polygon([(c["x"] + sf * f[0] * hl + sl * l[0] * hw, c["z"] + sf * f[1] * hl + sl * l[1] * hw)
                    for sf, sl in ((1, 1), (-1, 1), (-1, -1), (1, -1))])


def axis(c):
    a = math.radians(c["yaw"])
    hl = bd.CAR_BODY_M[1] / 2
    return LineString([(c["x"] - math.sin(a) * hl, c["z"] - math.cos(a) * hl), (c["x"] + math.sin(a) * hl, c["z"] + math.cos(a) * hl)])


for label, r in (("rule", rule), ("every place", every)):
    cars = bd.place_parked(r, pieces, buildings, lap)
    check(f"{label}: places found", len(cars) >= (5 if r is rule else 40), f"{len(cars)} places")
    check(f"{label}: the same places on a second run", cars == bd.place_parked(r, pieces, buildings, lap))
    roads = [road_of(c) for c in cars]
    # Inside the road band: the centre at the rule's inset from the edge, the whole body on the asphalt.
    offsets = []
    for c, (_, cls, _, line) in zip(cars, roads):
        w = bd.ROAD_WIDTH[cls]
        want = w / 2 - (r["inset_m"] if w < r["narrow_m"] else r["wide_inset_m"])
        offsets.append(abs(line.distance(Point(c["x"], c["z"])) - want))
    check(f"{label}: centres at the inset from the road edge", max(offsets) < 0.01, f"off by {max(offsets):.3f} m at most")
    check(f"{label}: bodies inside the road band",
          all(body(c).buffer(-0.01).within(line.buffer(bd.ROAD_WIDTH[cls] / 2)) for c, (_, cls, _, line) in zip(cars, roads)))
    gaps = [math.hypot(a["x"] - b["x"], a["z"] - b["z"]) for i, a in enumerate(cars) for b in cars[i + 1:]]
    check(f"{label}: cars at least {r['gap_m']} m apart", min(gaps) >= r["gap_m"], f"closest {min(gaps):.2f} m")
    near_j = min(math.hypot(c["x"] - x, c["z"] - y) for c in cars for x, y in JUNCTIONS)
    check(f"{label}: none within {r['junction_m']} m of a junction", near_j >= r["junction_m"], f"closest {near_j:.1f} m")
    near_b = min(axis(c).distance(b) for c in cars for b in buildings)
    check(f"{label}: none within {r['building_m']} m of a building", near_b >= r["building_m"] - 1e-6, f"closest {near_b:.2f} m")
    check(f"{label}: no body touching a building", not any(body(c).intersects(b) for c in cars for b in buildings))
    near_s = min(math.hypot(c["x"] - 100, c["z"]) for c in cars)
    check(f"{label}: none within {r['spawn_m']} m of the spawn", near_s >= r["spawn_m"], f"closest {near_s:.1f} m")
    names = {n for _, _, n, _ in roads}
    check(f"{label}: no car on Other Road (primary) or Far Street (beyond the lap's 250 m)",
          not names & {"Other Road", "Far Street"}, sorted(names))
    # Along the road, either way: the yaw (rounded to 0.1 degree) parallel to the straight road's bearing.
    bearing = lambda line: math.atan2(line.coords[-1][0] - line.coords[0][0], line.coords[-1][1] - line.coords[0][1])
    bad = [c for c, (_, _, _, line) in zip(cars, roads) if abs(math.sin(bearing(line) - math.radians(c["yaw"]))) > 2e-3]
    check(f"{label}: headings along the road", not bad, f"{len(bad)} across")
    check(f"{label}: paints from the game's names", {c["colour"] for c in cars} <= {"white", "silver", "grey", "black", "red", "blue", "taxi"})

# With every place taken, the clearances are what thin the cars out.
cars = bd.place_parked(every, pieces, buildings, lap)
roads = [road_of(c) for c in cars]
check("every place: High Street parked on by name", any(n == "High Street" for _, _, n, _ in roads))
check("every place: the continued street is no junction", any(math.hypot(c["x"] - 80, c["z"]) < 12 for c in cars))
check("every place: no car on the main street's south side beside the service lane",
      not any(-90 <= c["x"] <= -60 and -3 < c["z"] < 0 for c in cars))
check("every place: none in front of the houses on the street line", not any(20 <= c["x"] <= 60 and c["z"] > 0 for c in cars))
check("every place: both directions", len({round(c["yaw"]) for c, (_, _, n, _) in zip(cars, roads) if n == "Main Street"}) == 2)
taxis = sum(c["colour"] == "taxi" for c in cars) / len(cars)
check("every place: some taxis, not most", 0.03 < taxis < 0.25, f"{taxis:.0%} of {len(cars)}")

print("parking: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
