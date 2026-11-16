"""Checks the pieces that take the map east to Tema, on synthetic data: a corridor that takes a road class and whole
districts as well as its named roads (build_district.py corridor_region), OpenStreetMap's tags to the maps' categories
and the features kept (places.py osm_category, osm_extract.py first_key), garage names without private people
(places.garage_name), the garages with their door on the road (build_district.find_garages), and the tile data files
(export_scene.py write_chunks).

    python3 tests/test_east.py
"""
import json
import os
import sys
import tempfile

import shapely
from shapely.geometry import LineString, Point, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402
import export_scene  # noqa: E402
import osm_extract  # noqa: E402
import places as pl  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


ident = lambda g: g   # local metres stand in for lon and lat

# A corridor: a named road, an unnamed motorway piece of its class, a road of another class, a district box.
rows = [("a", "trunk", "Main Road", LineString([(0, 0), (1000, 0)])),
        ("b", "motorway", "", LineString([(0, 500), (1000, 500)])),
        ("c", "secondary", "", LineString([(0, 900), (1000, 900)]))]
spec = {"roads": ("Main Road",), "classes": ("motorway",), "half_width_m": 50.0,
        "within": {"west": -100, "south": -100, "east": 1100, "north": 2000},
        "boxes": ({"west": 400, "south": 1500, "east": 600, "north": 1700},)}
region = bd.corridor_region(spec, rows, ident)
check("a corridor holds its named road", region.contains(Point(500, 0)))
check("and the unnamed pieces of its classes", region.contains(Point(500, 500)))
check("but not the roads of other classes", not region.contains(Point(500, 900)))
check("and its districts whole", region.contains(Point(500, 1600)))
check("inside its box", not region.contains(Point(1080, 0)))

# OpenStreetMap's tags.
check("a mechanic is a garage", pl.osm_category("craft", "mechanic") == "garage")
check("a tyre shop is a garage", pl.osm_category("shop", "tyres") == "garage")
check("a shop of any kind shows", pl.osm_category("shop", "hairdresser") == "shop")
check("an office shows", pl.osm_category("office", "company") == "office")
check("a parking lot does not", pl.osm_category("amenity", "parking") is None)
check("healthcare of any kind is care", pl.osm_category("healthcare", "laboratory") == "hospital")
check("every OpenStreetMap category is one the game draws",
      all(c in pl.CATEGORIES for c in list(pl.OSM_BY_VALUE.values()) + list(pl.OSM_BY_KEY.values()) if c))
check("a bench is no place", osm_extract.first_key({"amenity": "bench", "name": "x"}) is None)
check("a shop=yes is a place", osm_extract.first_key({"shop": "yes"}) == ("shop", "yes"))
check("amenity=yes is not", osm_extract.first_key({"amenity": "yes"}) is None)
check("a lighthouse is a place, a pipeline is not",
      osm_extract.first_key({"man_made": "lighthouse"}) == ("man_made", "lighthouse")
      and osm_extract.first_key({"man_made": "pipeline"}) is None)

# Garage names that name a private person: kept, marked, in the proof of concept; dropped by a public build
# (CHALE_PUBLIC=1), where the game names the garage generically (circle-bible DECISIONS.md, 16 Nov 2026).
PEOPLE = ("Paa Joe", "Master Kofi shop", "Mr K Automobile", "Kofi's Fitting Shop", "Auntie Ama Spraying", "Bra Nii Vulcanizer")
for n in PEOPLE:
    check(f"'{n}' keeps its name, marked a person's", pl.garage_name(n) == n and pl.is_person(n), repr(pl.garage_name(n)))
pl.PUBLIC = True
for n in PEOPLE:
    check(f"'{n}' loses it in a public build", pl.garage_name(n) == "", repr(pl.garage_name(n)))
pl.PUBLIC = False
for n in ("Kaya Auto", "Pixxles Auto", "DrivePrime Auto Parts"):
    check(f"'{n}' keeps it", pl.garage_name(n) == n and not pl.is_person(n), repr(pl.garage_name(n)))

# Garages: OpenStreetMap's and Overture's, one where both map the same, the door on the road's edge.
osm_rows = [{"source": "osm", "cat": "garage", "garage": "repair", "id": "node/1", "name": "Kaya Auto", "brand": False,
             "lon": 100.0, "lat": 20.0, "outline": None},
            {"source": "osm", "cat": "garage", "garage": "tyres", "id": "way/2", "name": "", "brand": False,
             "lon": 300.0, "lat": -25.0, "outline": shapely.to_wkb(box(290, -35, 310, -15))},
            {"source": "osm", "cat": "shop", "garage": None, "id": "node/3", "name": "Bakery", "brand": False,
             "lon": 500.0, "lat": 20.0, "outline": None}]
place_rows = [{"source": "places", "category": "automotive_service", "name": "Kaya Auto Ltd", "confidence": 0.9,
               "lon": 130.0, "lat": 20.0},
              {"source": "places", "category": "automotive_service", "name": "Ghost Garage", "confidence": 0.3,
               "lon": 700.0, "lat": 20.0},
              {"source": "places", "category": "vehicle_parts_store", "name": "Paa Kwesi Parts", "confidence": 0.9,
               "lon": 900.0, "lat": 15.0}]
roads = [("r", "primary", "Main Road", LineString([(0, 0), (1000, 0)]))]
clip = box(-100, -100, 1100, 100)
garages = bd.find_garages(osm_rows, place_rows, roads, clip, lambda x, z: clip.contains(Point(x, z)),
                          lambda lon, lat: (lon, lat), ident)
names = [g["name"] for g in garages]
check("three garages: OpenStreetMap's two and Overture's parts shop", len(garages) == 3, str(names))
check("one Kaya Auto, OpenStreetMap's", names.count("Kaya Auto") == 1 and garages[0].get("osm") == "node/1")
check("an unsure Overture garage is left out", "Ghost Garage" not in names)
check("a garage with no name is kept, named \"\"", any(g["name"] == "" and g.get("osm") == "way/2" for g in garages))
check("a person's garage keeps its name, marked a person's",
      any(g.get("source") == "overture" and g["name"] == "Paa Kwesi Parts" and g.get("person") is True for g in garages))
check("no other garage is marked a person's", sum(bool(g.get("person")) for g in garages) == 1)
pl.PUBLIC = True
public = bd.find_garages(osm_rows, place_rows, roads, clip, lambda x, z: clip.contains(Point(x, z)),
                         lambda lon, lat: (lon, lat), ident)
pl.PUBLIC = False
check("a public build keeps its place, not its name",
      any(g.get("source") == "overture" and g["name"] == "" and not g.get("person") for g in public))
w = bd.road_width("primary", "Main Road")
g0 = garages[0]
check("the door is on the asphalt's edge, on the garage's side", abs(g0["door"][0] - 100) < 0.2 and abs(g0["door"][1] - w / 2) < 0.2,
      str(g0["door"]))
check("toward points from the garage to the road", g0["toward"][1] < -0.99, str(g0["toward"]))
check("the road there is named, with its direction", g0["road"]["name"] == "Main Road" and abs(abs(g0["road"]["dir"][0]) - 1) < 1e-3)
g1 = next(g for g in garages if g.get("osm") == "way/2")
check("a mapped building gives its centre and outline", abs(g1["x"] - 300) < 0.1 and len(g1.get("outline", [])) == 4)

# The scene's garages in glTF axes, and the tile data files.
with tempfile.TemporaryDirectory() as tmp:
    tiles = [{"file": "t/a_0_0.glb", "bounds": [0, -500, 500, 0]}, {"file": "t/a_1_0.glb", "bounds": [500, -500, 1000, 0]}]
    os.makedirs(os.path.join(tmp, "t"))
    scene = {"files": {"tiles": [dict(t) for t in tiles]},
             "streetLights": [{"foot": [100, 0, -50]}, {"foot": [700, 0, -50]}, {"foot": [1200, 0, -50]}],
             "parked": [{"position": [600, 0, -10]}],
             "solid": {"buildings": [[10, -10, 20, -10, 20, -20]], "walls": [[900, -400, 950, -400]]},
             "hiding": [{"position": [50, 0, -50]}]}
    export_scene.write_chunks(scene, tmp)
    a = json.load(open(os.path.join(tmp, "t/a_0_0.data.json")))
    b = json.load(open(os.path.join(tmp, "t/a_1_0.data.json")))
    check("each item goes to the tile that holds it", len(a["streetLights"]) == 1 and len(a["solid"]["buildings"]) == 1
          and len(a["hiding"]) == 1 and len(b["parked"]) == 1 and len(b["solid"]["walls"]) == 1)
    check("an item outside every tile goes to the nearest", len(b["streetLights"]) == 2)
    check("the scene keeps empty lists and names the data", scene["streetLights"] == [] and "solid" not in scene
          and scene["files"]["tiles"][0]["data"] == "t/a_0_0.data.json" and "streetLights" in scene["chunked"])

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
