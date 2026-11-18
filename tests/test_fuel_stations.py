"""Checks the fuel stations' export (scripts/fuel_stations.py) on synthetic rows: OpenStreetMap and Overture merged
without doubles, the brand as the forecourt writes it, a site or a canopy kept with its station, the canopies left
out of the buildings, the street in front.

    python3 tests/test_fuel_stations.py
"""
import os
import sys

import shapely
from shapely.geometry import LineString, Point, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import fuel_stations as fs  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# Local metres stand in for lon and lat: to_local and local below are the identity.
def osm(fid, x, z, name="", outline=None, **tags):
    return {"source": "osm", "id": fid, "name": name, "lon": x, "lat": z, "fuel": True, "tags": tags, "brand": bool(tags.get("brand")),
            "outline": shapely.to_wkb(outline) if outline is not None else None}


def canopy(fid, poly):
    return {"source": "osm", "id": fid, "name": "", "lon": poly.centroid.x, "lat": poly.centroid.y, "canopy": True,
            "tags": {"building": "roof"}, "outline": shapely.to_wkb(poly)}


def place(name, x, z, confidence=0.9, brand=None, status=None):
    return {"source": "places", "category": "gas_station", "name": name, "lon": x, "lat": z, "confidence": confidence,
            "brand": brand, "status": status}


# A street along x at z = 0, 10 m wide.
lines = [("Test Road", 10.0, LineString([(-500, 0), (900, 0)]))]
osm_rows = [
    # A Shell mapped as a point 20 m back from the street, its canopy mapped beside it.
    osm("node/1", 0, 20, "Shell Osu", brand="Shell"),
    canopy("way/10", box(-8, 12, 8, 24)),
    # A station mapped with its site, the street side at z = 6; a node of the same station inside its site.
    osm("way/2", 200, 25, "Goil Labone", box(185, 6, 215, 40), brand="Goil", operator="GOIL PLC"),
    osm("node/3", 205, 20, "GOIL"),
    canopy("way/11", box(192, 14, 208, 26)),
    # "Total" as tagged before the brand's new name; a station tagged on its shop's outline.
    osm("way/4", 400, -30, "", box(395, -35, 405, -25), brand="Total", building="retail"),
    # A canopy far from any station (a market's shed roof): not a station's.
    canopy("way/12", box(600, 30, 620, 50)),
]
place_rows = [
    # Overture's pin of the Shell, 25 m off and named differently: the same station.
    place("Shell", 10, 40, brand="Shell"),
    # A station only Overture knows.
    place("Star Oil Labadi", 800, -20, confidence=0.8),
    # Too unsure, or closed: left out.
    place("Maybe Fuel", 700, 20, confidence=0.3),
    place("Old Fuel", 720, 20, status="permanently_closed"),
]
out = fs.find_fuel_stations(osm_rows, place_rows, lines, lambda x, z: -500 < x < 900, lambda lon, lat: (lon, lat), lambda g: g)
by = {(s.get("osm") or s["name"]): s for s in out}

check("four stations: OpenStreetMap's three, Overture's own one", len(out) == 4, str([s.get("osm") or s["name"] for s in out]))
shell = by.get("node/1", {})
check("Overture's pin of a mapped station merges into it", "Shell" not in by and shell.get("brand") == "Shell")
check("a point station's canopy is its own, and the station stands under it",
      len(shell.get("canopies", [])) == 1 and abs(shell["x"]) < 0.5 and abs(shell["z"] - 18) < 0.5, f"{shell.get('x')}, {shell.get('z')}")
goil = by.get("way/2", {})
check("a mapped site keeps its outline and its canopy; the node inside it is the same station",
      goil.get("site") and len(goil.get("canopies", [])) == 1 and "node/3" not in by)
check("the brand as the forecourt writes it (Goil is GOIL, Total is TotalEnergies)",
      goil.get("brand") == "GOIL" and by.get("way/4", {}).get("brand") == "TotalEnergies")
check("the operator is kept", goil.get("operator") == "GOIL PLC")
check("a station tagged on its shop's outline exports it as the shop, not the site",
      by.get("way/4", {}).get("shop") and not by.get("way/4", {}).get("site"))
star = by.get("Star Oil Labadi", {})
check("a station only Overture knows is kept, from Overture", star.get("source") == "overture")
check("unsure and closed Overture pins are left out", not {"Maybe Fuel", "Old Fuel"} & {s["name"] for s in out})
check("no station has the market's roof", all(len(s.get("canopies", [])) <= 1 for s in out))
check("the street in front: the door on the asphalt's edge, facing the station's side",
      shell.get("road", {}).get("name") == "Test Road" and abs(shell["door"][1] - 5) < 0.01 and shell["toward"][1] < -0.99,
      str(shell.get("door")))
check("a site's door is on the asphalt in front of the site", abs(goil["door"][1] - 5) < 0.01 and 185 <= goil["door"][0] <= 215,
      str(goil.get("door")))

# The canopies left out of the buildings: the same outline through Overture, or a footprint mostly under it; a shop
# beside it stays.
shapes = fs.canopy_shapes(out)
check("the canopy's own footprint is a canopy", fs.is_canopy(box(-8, 12, 8, 24), shapes))
check("a footprint mostly under it is too", fs.is_canopy(box(-6, 14, 6, 26), shapes))
check("the shop beside it is not", not fs.is_canopy(box(10, 20, 20, 30), shapes))
check("the market's shed roof is not", not fs.is_canopy(box(600, 30, 620, 50), shapes))

# A detector's footprint drawn over a canopy and its shop as one building is the canopy too; OpenStreetMap's is not.
big = box(-12, 10, 14, 36)
check("a machine-learned footprint over the canopy and the shop is taken for the canopy",
      fs.is_canopy(big, shapes, "Google Open Buildings") and not fs.is_canopy(big, shapes, "OpenStreetMap"))
# On a point station's lot, a detector's footprint is the station's own; OpenStreetMap's buildings stay, and so does a
# detector's footprint off the lot or too large to be a station's.
lots = fs.lot_shapes(out)
check("a machine-learned footprint on a station's lot is cleared for its forecourt", fs.on_lot(box(795, -20, 805, -12), lots, "Microsoft ML Buildings"))
check("an OpenStreetMap building there stays", not fs.on_lot(box(795, -20, 805, -12), lots, "OpenStreetMap"))
check("a footprint off the lot, or a warehouse, stays", not fs.on_lot(box(795, -80, 805, -60), lots, "Google Open Buildings")
      and not fs.on_lot(box(760, -60, 840, -6), lots, "Google Open Buildings"))
check("a station with its site mapped has no lot: its ground is the site", all(not p.intersects(box(185, 6, 215, 40)) for p in lots[0]))

# The ground kept clear of compound walls and parked cars: a mapped site, or a lot in front of a point station.
clear = fs.keep_clear(out)
check("each station keeps its ground clear", len(clear) == 4)
lot = next(c for c in clear if c.contains(Point(800, -15)))
check("a point station's lot runs from the asphalt's edge into its plot",
      lot.bounds[3] <= -5 + 0.01 and lot.bounds[1] >= -5 - fs.LOT_D - 0.01, str(lot.bounds))

print(f"\n{failures} failed" if failures else "\nall fuel station checks passed")
sys.exit(1 if failures else 0)
