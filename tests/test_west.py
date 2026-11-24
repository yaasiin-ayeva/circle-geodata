"""Checks the areas that take the map west to Circle, Makola, Jamestown and Kaneshie, and east along the coast to
Teshie (build_district.py AREAS central, kaneshie and labadi): each lies inside the slice it is built from
(aoi/slices.geojson), its whole districts inside its box, its lap's points inside its box, the areas it leaves out
defined, and none of them drawn in the standalone Jamestown district's frame.

    python3 tests/test_west.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402
import numpy as np  # noqa: E402
import shapely  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


def inside(b, outer):
    return outer["south"] <= b["south"] and b["north"] <= outer["north"] and outer["west"] <= b["west"] and b["east"] <= outer["east"]


slices = {f["properties"]["name"]: f["properties"] for f in json.load(open(os.path.join(HERE, "..", "aoi", "slices.geojson")))["features"]}
FROM = {"central": "accra-central", "kaneshie": "accra-central", "labadi": "coast-east"}
for name, sl in FROM.items():
    area = bd.AREAS[name]
    spec = area["corridor"]
    check(f"{name}: slice {sl} is defined", sl in slices)
    if sl not in slices:
        continue
    box = slices[sl]
    check(f"{name}: its box lies in slice {sl}", inside(spec["within"], box))
    check(f"{name}: its whole districts lie in its box", all(inside(b, spec["within"]) for b in spec.get("boxes", ())))
    w = spec["within"]
    pts = area["lap"]["route"]
    check(f"{name}: its lap's points lie in its box", all(w["south"] <= lat <= w["north"] and w["west"] <= lon <= w["east"] for lat, lon in pts))
    check(f"{name}: the areas it leaves out are defined", all(m in bd.AREAS for m in area.get("minus", [])))
    check(f"{name}: in Osu's frame, streamed with the world", area["origin"] == bd.OSU_ORIGIN)
    check(f"{name}: its building rules exist", area.get("district", "osu") in bd.DISTRICT_RULES)

# The old centre's rules: no compound walls, Jamestown's paints and signs, the lighthouse inside its box.
rules = bd.DISTRICT_RULES["central"]
check("central: houses on the street line", rules["yards"] is False)
check("central: Jamestown's paints and signs", rules["paints"] == bd.DISTRICT_RULES["jamestown"]["paints"] and rules["signs"] == bd.DISTRICT_RULES["jamestown"]["signs"])
lh = [(lat, lon) for k, lat, lon in rules["landmarks"] if k == "lighthouse"]
j = bd.JAMESTOWN_BOX
check("central: the lighthouse lies in Jamestown's box", lh and all(j["south"] <= a <= j["north"] and j["west"] <= o <= j["east"] for a, o in lh))
# The west and coast corridors meet along the vertical slice's edge, without a gap.
check("central and labadi meet at -0.19", bd.CENTRAL["east"] == bd.LABADI["west"] == -0.19)
# Starlets '91 Road's east end (circle-game docs/starlets-91-plan.md, stage 1b): the blocks east of its northbound
# stretch to Castle Road and Lokko Street, between central's east edge and Labadi's roads, are Labadi's, in a box that
# meets central along its edge and sits on the Osu Castle box.
east = bd.STARLETS_EAST_BOX
check("labadi takes Starlets '91 Road's east end", east in bd.AREAS["labadi"]["corridor"]["boxes"])
check("the east end's box meets central's edge", east["west"] == bd.CENTRAL["east"])
check("and sits on the Osu Castle box", east["south"] == bd.OSU_CASTLE_BOX["north"]
      and east["west"] == bd.OSU_CASTLE_BOX["west"])
check("it holds Castle Road and Lokko Street's corner", east["south"] < 5.5530 < east["north"])
# Access Bank's head office (OSM way 889488318): its centroid (Overture 2026-10-21.0) lies 3 m inside the edge.
check("and Access Bank's head office", east["south"] < 5.55242 < east["north"] and east["west"] < -0.18997 < east["east"])
# The box is a whole district of Labadi's corridor: a point in it far from Labadi's roads is in its region.
line = [("r", "primary", "La Road", LineString([(-0.170, 5.556), (-0.150, 5.560)]))]
to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
utm = lambda g: shapely.transform(g, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))
region = bd.corridor_region(bd.AREAS["labadi"]["corridor"], line, utm)
check("a house on Lokko Street is in Labadi's region", region.contains(utm(Point(-0.1893, 5.5533))))
check("but nothing west of central's edge", not region.contains(utm(Point(-0.1902, 5.5520))))

sys.exit(1 if failures else 0)
