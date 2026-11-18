"""Checks the electric-vehicle chargers' export (scripts/ev_chargers.py) on synthetic rows: OpenStreetMap, Overture and
the documented chargers merged without doubles, the plugs and the power as tagged, a charger in a fuel station's site
or by its point joined to that station, one in a car park on that ground, the street in front, the ground kept clear.

    python3 tests/test_ev_chargers.py
"""
import json
import os
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import LineString, Point, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import ev_chargers as ev  # noqa: E402
import places as pl  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# Local metres stand in for lon and lat: to_local and local below are the identity.
def charger(fid, x, z, name="", outline=None, **tags):
    return {"source": "osm", "id": fid, "name": name, "lon": x, "lat": z, "charging": True, "tags": {"amenity": "charging_station", **tags},
            "outline": shapely.to_wkb(outline) if outline is not None else None}


def point(fid, x, z, **tags):
    return {"source": "osm", "id": fid, "name": "", "lon": x, "lat": z, "charge_point": True, "cat": None,
            "tags": {"man_made": "charge_point", **tags}, "outline": None}


def place(name, x, z, confidence=0.9, category="ev_charging_station", status=None):
    return {"source": "places", "category": category, "name": name, "lon": x, "lat": z, "confidence": confidence, "status": status}


# The plugs and the power.
check("sockets and output as tagged", ev.sockets_of({"socket:type2_combo": "2", "socket:type2_combo:output": "60 kW",
                                                      "socket:type2": "yes", "socket:type2:output": "22 kW;11 kW"}) == ({"type2_combo": 2, "type2": 1}, 60.0))
check("an output in watts", ev._kw("7400 W") == 7.4 and ev._kw("50") == 50.0 and ev._kw("") is None)
check("the kind by the plugs and the power", ev.kind_of({"chademo": 1}, None) == "dc" and ev.kind_of({"type2": 2}, 22) == "ac"
      and ev.kind_of({"type2": 1, "type2_combo": 1}, None) == "mixed" and ev.kind_of({}, 120) == "dc" and ev.kind_of({}, None) == "")
check("for cars: not a bicycles' or a private one", ev.for_cars({}) and ev.for_cars({"motorcar": "yes", "bicycle": "yes"})
      and not ev.for_cars({"bicycle": "yes"}) and not ev.for_cars({"motorcar": "no"}) and not ev.for_cars({"access": "private"}))

# A street along x at z = 0, 10 m wide; a fuel station mapped with its site, another as a point with its canopy.
lines = [("Test Road", 10.0, LineString([(-500, 0), (1500, 0)]))]
fuel = [{"name": "", "brand": "TotalEnergies", "x": 200.0, "z": 25.0, "osm": "way/2", "site": [[185, 6], [215, 6], [215, 40], [185, 40]]},
        {"name": "Shell Osu", "brand": "Shell", "x": 600.0, "z": 20.0, "osm": "node/7", "canopies": [[[592, 14], [608, 14], [608, 26], [592, 26]]]}]
osm_rows = [
    # A DC charger mapped as a point by the street, with its operator and plugs; Overture pins it 20 m off.
    charger("node/1", 0, 18, "Xcharge Test", operator="Xcharge EV", capacity="2", **{"socket:type2_combo": "2", "socket:type2_combo:output": "120 kW"}),
    # A charger inside the TotalEnergies' site: that station's.
    charger("node/2", 210, 30, operator="TotalEnergies", **{"socket:type2": "1", "socket:type2:output": "22 kW"}),
    # A charger 30 m from the Shell's point: that station's.
    charger("node/3", 625, 30),
    # One for bicycles only, and a private one: left out.
    charger("node/4", 300, 20, bicycle="yes"),
    charger("node/5", 350, 20, access="private"),
    # Two lone charge points: one by the first charger (a car more), one alone at 1000.
    point("node/6", 10, 20, **{"socket:type2_combo": "1"}),
    point("node/8", 1000, 20, **{"socket:type2": "2"}),
    # A charger mapped with its site.
    charger("way/9", 1200, 25, "Mall Chargers", box(1190, 10, 1210, 40), capacity="4"),
]
place_rows = [
    place("Xcharge EV", 15, 30),
    place("Plug Point Labadi", 800, 20, confidence=0.8),
    place("Maybe Charger", 700, 20, confidence=0.3),
    place("Old Charger", 720, 20, status="permanently_closed"),
    place("A Fuel Station", 760, 20, category="gas_station"),
]
# The documented chargers: one at the Shell (already mapped there: replaced), one in a car park, one whose host is
# not in the extract.
hosts = {"node/7": {"kind": "fuel", "name": "Shell Osu", "pt": (600.0, 20.0), "outline": None},
         "way/20": {"kind": "parking", "name": "Test Mall Car Park", "pt": (-300.0, 30.0), "outline": box(-320, 8, -280, 50)},
         "way/21": {"kind": "mall", "name": "Test Mall", "pt": (-300.0, 70.0), "outline": box(-320, 52, -280, 90)}}
documented = [{"host": "node/7", "operator": "Shell Recharge", "kind": "dc", "sources": ["https://example.org/a"]},
              {"host": "way/20", "host_name": "Test Mall", "operator": "POBAD", "kind": "dc", "kw": 60, "sources": ["https://example.org/b"]},
              {"host": "way/99", "operator": "Nowhere", "sources": []}]
out = ev.find_ev_chargers(osm_rows, place_rows, lines, lambda x, z: -500 < x < 1500, lambda lon, lat: (lon, lat), lambda g: g,
                          fuel=fuel, hosts=hosts, documented=documented)
by = {(c.get("osm") or c.get("operator") or c["name"]): c for c in out}

check("seven chargers: OpenStreetMap's four, a lone charge point, Overture's own one, a documented one",
      len(out) == 7 and set(by) == {"node/1", "node/2", "node/3", "node/8", "way/9", "Plug Point Labadi", "POBAD"},
      str(sorted(by)))
x1 = by.get("node/1", {})
check("Overture's pin of a mapped charger merges into it", "Xcharge EV" not in {c["name"] for c in out if not c.get("osm")})
check("its operator, capacity, plugs and power as tagged; the point beside it adds its plug",
      x1.get("operator") == "Xcharge EV" and x1.get("capacity") == 2 and x1.get("sockets") == {"type2_combo": 3}
      and x1.get("kw") == 120.0 and x1.get("kind") == "dc", str(x1))
check("the street in front: the door on the asphalt's edge", x1.get("road", {}).get("name") == "Test Road" and abs(x1["door"][1] - 5) < 0.01)
check("bicycles' and private chargers are left out", "node/4" not in by and "node/5" not in by)
check("unsure, closed and fuel-only Overture pins are left out",
      not {"Maybe Charger", "Old Charger", "A Fuel Station"} & {c["name"] for c in out})
t2 = by.get("node/2", {})
check("a charger in a fuel station's site is that station's", t2.get("fuel", {}).get("osm") == "way/2" and t2.get("kind") == "ac"
      and t2["fuel"]["x"] == 200.0 and t2["fuel"].get("brand") == "TotalEnergies", str(t2.get("fuel")))
check("one by a station's point (within 40 m) is that station's", by.get("node/3", {}).get("fuel", {}).get("osm") == "node/7")
check("the documented charger at a station OpenStreetMap already charges at gives way", "Shell Recharge" not in by)
lone = by.get("node/8", {})
check("a lone charge point far from any charger is one of its own", lone.get("sockets") == {"type2": 2} and lone.get("kind") == "ac")
check("a charger mapped with its site keeps it", by.get("way/9", {}).get("site") and by["way/9"].get("capacity") == 4)
check("a charger only Overture knows is kept, from Overture", by.get("Plug Point Labadi", {}).get("source") == "overture")
pobad = by.get("POBAD", {})
check("a documented charger stands in its host car park: its ground the site, the mall named, its sources kept",
      pobad.get("source") == "documented" and pobad.get("host") == {"kind": "parking", "name": "Test Mall"} and pobad.get("site")
      and abs(pobad["x"] + 300) < 0.5 and pobad.get("kw") == 60 and pobad.get("kind") == "dc" and pobad.get("sources") == ["https://example.org/b"],
      str(pobad))
check("a documented charger whose host is not mapped is left out", "Nowhere" not in by)
check("no charger names a station it does not stand in", not by["node/1"].get("fuel") and not by["way/9"].get("fuel"))

# The ground kept clear of compound walls and parked cars: not a forecourt's charger (the station keeps its own).
clear = ev.keep_clear(out)
check("each charger off a forecourt keeps its ground clear", len(clear) == len([c for c in out if not c.get("fuel")]))
check("a point charger's lot runs from the asphalt into its plot",
      any(c.contains(Point(0, 10)) and c.bounds[1] >= 5 - 0.01 and c.bounds[3] <= 5 + ev.LOT_D + 0.01 for c in clear))

# A charger's category on the maps: its own icon now, not fuel's.
check("OpenStreetMap's charging_station is the maps' 'charging'", pl.osm_category("amenity", "charging_station") == "charging"
      and pl.CATEGORY_OF.get("ev_charging_station") == "charging")

# The hosts read from osm.parquet: car parks, malls and the documented ids.
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "osm.parquet")
    rows = [{"id": "way/20", "name": "A & C Mall", "key": "amenity", "value": "parking", "lon": 0.0, "lat": 0.0, "outline": shapely.to_wkb(box(-5, -5, 5, 5))},
            {"id": "node/7", "name": "Shell", "key": "amenity", "value": "fuel", "lon": 50.0, "lat": 0.0, "outline": None},
            {"id": "node/8", "name": "Kiosk", "key": "shop", "value": "kiosk", "lon": 60.0, "lat": 0.0, "outline": None}]
    pq.write_table(pa.Table.from_pylist(rows), path)
    h = ev.read_hosts(path, lambda g: g, ["node/7"])
    check("the hosts: car parks and malls, and the documented ids", set(h) == {"way/20", "node/7"} and h["way/20"]["kind"] == "parking"
          and h["node/7"]["kind"] == "fuel" and h["way/20"]["outline"].area == 100)
    doc = os.path.join(tmp, "ev.json")
    json.dump({"chargers": [{"host": "way/20"}]}, open(doc, "w"))
    check("the documented list is read, and none when its file is missing", ev.load_documented(doc) == [{"host": "way/20"}]
          and ev.load_documented(os.path.join(tmp, "none.json")) == [])

# The repository's documented list: each entry names its host by an OpenStreetMap id and cites a source.
docs = ev.load_documented(os.path.join(HERE, "..", "aoi", "ev-chargers.json"))
check("aoi/ev-chargers.json: each charger at a host by its id, with a source",
      docs and all(d["host"].split("/")[0] in ("node", "way", "relation") and d.get("sources") for d in docs))

print(f"\n{failures} failed" if failures else "\nall ev charger checks passed")
sys.exit(1 if failures else 0)
