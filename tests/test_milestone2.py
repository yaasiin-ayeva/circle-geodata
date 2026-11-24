"""Checks the area that fills the city between Circle and the vertical slice (build_district.py AREAS "adabraka",
milestone 2): it lies inside the slice it is built from (aoi/slices.geojson "milestone-2"), its whole district inside
its box, its lap's points inside its box, the areas it leaves out defined, and its building rules present.

    python3 tests/test_milestone2.py
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
area = bd.AREAS["adabraka"]
spec = area["corridor"]
check("adabraka: slice milestone-2 is defined", "milestone-2" in slices)
if "milestone-2" in slices:
    box = slices["milestone-2"]
    check("adabraka: its box lies in slice milestone-2", inside(spec["within"], box))
    check("adabraka: its whole districts lie in its box", all(inside(b, spec["within"]) for b in spec.get("boxes", ())))
    w = spec["within"]
    pts = area["lap"]["route"]
    check("adabraka: its lap's points lie in its box", all(w["south"] <= lat <= w["north"] and w["west"] <= lon <= w["east"] for lat, lon in pts))
check("adabraka: the areas it leaves out are defined", all(m in bd.AREAS for m in area.get("minus", [])))
check("adabraka: in Osu's frame, streamed with the world", area["origin"] == bd.OSU_ORIGIN)
check("adabraka: its building rules exist", area.get("district", "osu") in bd.DISTRICT_RULES)
# It covers the whole slice's box, so no pocket is left between the corridors.
check("adabraka: takes the whole slice as a district", spec["boxes"] and all(spec["within"][k] == b[k] for b in spec["boxes"] for k in ("south", "north", "west", "east")))
# Its lap's roads are in the Ring Road's one-way set where they are dual carriageways.
check("adabraka: the Ring Road stays one way on the lap", set(bd.RING_ROADS) <= set(area["lap"].get("one_way", ())))
# The rules: walled yards, like Osu and the Ring Road corridor.
rules = bd.DISTRICT_RULES["adabraka"]
check("adabraka: houses in walled yards", rules["yards"] is True)
check("adabraka: cars parked along the side streets", bool(rules.get("parking")))

sys.exit(1 if failures else 0)
