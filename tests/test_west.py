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

sys.exit(1 if failures else 0)
