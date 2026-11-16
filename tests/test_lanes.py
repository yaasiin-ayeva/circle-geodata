"""Checks the lane counts from OpenStreetMap (scripts/build_district.py match_lanes, road_width, lane_lines) on
synthetic roads and ways, and that the checked-in aoi/osm-lanes.geojson gives Liberation Road its three lanes.

    python3 tests/test_lanes.py
"""
import json
import os
import sys

from shapely.geometry import LineString

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


def way(coords, lanes, name="Liberation Road", oneway="yes"):
    return {"properties": {"name": name, "lanes": str(lanes), "oneway": oneway},
            "geometry": {"type": "LineString", "coordinates": coords}}


ident = lambda g: g  # noqa: E731  (the synthetic ways are already in metres)

# A boulevard along x, 30 m centre to centre (Ghana drives on the right): eastbound south, westbound north.
east = ("e", "trunk", "Liberation Road", LineString([(0, 0), (400, 0)]))
west = ("w", "trunk", "Liberation Road", LineString([(400, 30), (0, 30)]))
bd.CARRIAGEWAY_W.clear(); bd.CARRIAGEWAY_GAP.clear()
bd.CARRIAGEWAY_W.update(bd.find_carriageways([east, west]))
ways = [way([[0, 1.5], [400, 1.5]], 3), way([[400, 31], [0, 31]], 3),
        # A slip road alongside, one lane, the same way: it must not pull the carriageway to one lane.
        way([[300, -6], [400, -6]], 1, name="")]
bd.LANES.clear(); bd.LANES.update(bd.match_lanes([east, west], ways, ident))
check("both carriageways take OSM's 3 lanes", bd.LANES == {"e": 3, "w": 3}, str(bd.LANES))
w = bd.road_width("trunk", "Liberation Road", rid="e")
check("3 lanes of 3.5 m and the edge strips: 11 m", abs(w - 11.0) < 0.05, str(w))
check("a median of at least 1.5 m stays between them", 30 - w >= bd.MEDIAN_MIN_M)

# A way running the other way, or under another name, does not count.
bd.LANES.clear(); bd.LANES.update(bd.match_lanes([east], [way([[400, 1], [0, 1]], 3)], ident))
check("a one-way way against the carriageway's direction is ignored", "e" not in bd.LANES, str(bd.LANES))
bd.LANES.clear(); bd.LANES.update(bd.match_lanes([east], [way([[0, 1], [400, 1]], 3, name="Other Road")], ident))
check("a way of another name is ignored", "e" not in bd.LANES)

# Narrow median: the lanes' width is capped so the median keeps 1.5 m.
bd.CARRIAGEWAY_W.clear(); bd.CARRIAGEWAY_GAP.clear()
e2 = ("e2", "trunk", "X", LineString([(0, 0), (400, 0)]))
w2 = ("w2", "trunk", "X", LineString([(400, 10), (0, 10)]))
bd.CARRIAGEWAY_W.update(bd.find_carriageways([e2, w2]))
bd.LANES.clear(); bd.LANES.update({"e2": 3, "w2": 3})
check("capped to leave the median", bd.road_width("trunk", "X", rid="e2") <= 10 - bd.MEDIAN_MIN_M + 1e-9,
      str(bd.road_width("trunk", "X", rid="e2")))
# A carriageway matched to a slip road's single lane keeps two.
bd.LANES.update({"e2": 1})
check("a carriageway never drops below two lanes", bd.lanes_of("e2", "trunk", "X") == 2)

# A two-way road is never drawn narrower than its class.
bd.LANES.clear(); bd.LANES.update({"t": 2})
check("a two-way road keeps its class width", bd.road_width("primary", "La Road", rid="t") == bd.ROAD_WIDTH["primary"])
# The surveyed Ring Road East keeps its 7 m.
bd.LANES.update({"r": 3})
check("Ring Road East keeps its surveyed 7 m", bd.road_width("primary", "Ring Road East", rid="r") == bd.CARRIAGEWAY_M)

# Lane lines: n - 1, symmetric, inside the asphalt.
for n, wd in ((2, 7.5), (3, 11.0), (4, 14.5)):
    offs = bd.lane_lines(wd, n)
    check(f"{n} lanes: {n - 1} lines, symmetric", len(offs) == n - 1 and abs(sum(offs)) < 1e-6
          and all(abs(o) < wd / 2 for o in offs), str(offs))
bd.LANES.clear(); bd.CARRIAGEWAY_W.clear(); bd.CARRIAGEWAY_GAP.clear()

# The checked-in file: Liberation Road is mostly three lanes each way.
with open(bd.OSM_LANES) as f:
    feats = json.load(f)["features"]
lib = [int(x["properties"]["lanes"]) for x in feats if x["properties"].get("name") == "Liberation Road"
       and x["properties"].get("highway") == "trunk" and x["properties"].get("lanes", "").isdigit()]
check("aoi/osm-lanes.geojson: Liberation Road mostly 3 lanes", lib and sum(n == 3 for n in lib) > len(lib) / 2, str(lib[:10]))

print(f"{failures} failed" if failures else "all passed")
sys.exit(1 if failures else 0)
