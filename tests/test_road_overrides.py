"""Checks the roads drawn otherwise than OpenStreetMap tags them (scripts/road_overrides.py): the list and its lines,
the match of Overture's segments to them (the drive itself, not the street it leaves and rejoins), and road_width().

    python3 tests/test_road_overrides.py
"""
import json
import os
import sys

import numpy as np
import shapely
import shapely.affinity
from pyproj import Transformer
from shapely.geometry import LineString

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402
import road_overrides as ro  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# The list: each way once, a class the build draws, a width narrower than that class's.
ways = [o["way"] for o in ro.OVERRIDES]
check("each way is listed once", len(ways) == len(set(ways)))
check("each override is a class the build draws, at a width under its class's",
      all(o["class"] in bd.ROAD_WIDTH and 2.5 <= o["width"] <= bd.ROAD_WIDTH[o["class"]] for o in ro.OVERRIDES))
gallery = next((o for o in ro.OVERRIDES if o["way"] == 1230501216), None)
check("The Gallery's drive (way 1230501216) is a 3 m service drive",
      gallery is not None and gallery["class"] == "service" and gallery["width"] == 3.0)

# The lines: every listed way in the checked-in file, as OpenStreetMap maps it.
lines = ro.load()
with open(ro.LINES) as f:
    feats = json.load(f)["features"]
check("aoi/road-overrides.geojson holds a line for every listed way", {l["way"] for l in lines} == set(ways),
      f"{len(lines)} of {len(ways)}")
check("each line is the way's (a LineString of 2 points or more)",
      all(isinstance(l["geom"], LineString) and len(l["geom"].coords) >= 2 for l in lines))
check("a way dropped from the list is dropped by load()", len(ro.load()) == len(ro.OVERRIDES) and len(feats) >= len(lines))

# The match, in the legon area's frame: the drive's own Overture segment (the same nodes) and a copy offset by a metre
# match; Nii Opey Street's segment between the drive's two ends (it shares both), a parallel street 8 m off and a
# short stub off the loop do not.
to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
oe, on = to_utm.transform(-0.1713, 5.6258)
local = lambda g: shapely.transform(g, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])) - (oe, on))
drive = local(next(l["geom"] for l in lines if l["way"] == 1230501216))
street = local(LineString([(-0.1717104, 5.6254718), (-0.1716515, 5.6254845), (-0.1713995, 5.625566), (-0.1710686, 5.6257007)]))
roads = [("drive", "residential", "", drive),
         ("drive-shifted", "residential", "", shapely.affinity.translate(drive, 1.0, 0.0)),
         ("nii-opey", "residential", "Nii Opey Street", street),
         ("parallel", "residential", "", shapely.affinity.translate(drive, 8.0, 0.0)),
         ("stub", "residential", "", LineString([drive.coords[2], (drive.coords[2][0], drive.coords[2][1] + 30.0)]))]
m = ro.match(roads, lines, local)
check("the drive's segment matches it, and a copy a metre off", set(m) == {"drive", "drive-shifted"}, ", ".join(sorted(m)))
check("the street between its two ends, a street 8 m off and a stub off it keep their class",
      not {"nii-opey", "parallel", "stub"} & set(m))
check("the match gives the way's class and width", m.get("drive") == ("service", 3.0, 1230501216))
check("no lines, no match", ro.match(roads, [], local) == {})

# road_width(): the override first, the class's otherwise.
bd.OVERRIDE_W.clear()
bd.OVERRIDE_W["drive"] = 3.0
check("road_width() draws an overridden segment at its width", bd.road_width("service", "", rid="drive") == 3.0)
check("road_width() draws the others by their class", bd.road_width("service", "", rid="other") == bd.ROAD_WIDTH["service"]
      and bd.road_width("residential", "", rid="nii-opey") == bd.ROAD_WIDTH["residential"])
bd.OVERRIDE_W.clear()

# The Gallery's corner (circle-game docs/mall-surroundings.md: OSM way 741876793, 1.36 m from the drive's line at its
# nearest) stands no more than 0.3 m in the drive's edge (the game's tolerance for any solid on a road's asphalt).
corner_m = 1.36
check("The Gallery's corner clears the drive's edge within the game's 0.3 m",
      gallery is not None and gallery["width"] / 2 - corner_m <= 0.3, f"{gallery['width'] / 2 - corner_m:+.2f} m")

print()
print("all passed" if failures == 0 else f"{failures} failed")
sys.exit(1 if failures else 0)
