"""Accra's fuel stations for the game: where each one is, what OpenStreetMap knows of its ground, its brand or name as
tagged, and the street in front. build_district.py writes them as the district's "fuelStations"; export_scene.py
passes them to the game, which builds each station's forecourt (circle-game src/world/fuel.ts, docs/fuel-stations.md).

    python3 tests/test_fuel_stations.py

Sources, as the garages merge theirs (build_district.py find_garages):
  - OpenStreetMap's amenity=fuel (osm_extract.py): a node, or a closed way round the station's site. A closed way that
    is also a building (building=yes, retail...) is the shop's outline, not the site.
  - Overture's places of category gas_station at the safety floor of the maps (places.MIN_CONFIDENCE_SAFETY), not
    closed: the stations OpenStreetMap does not map.
  One station where both map the same one: any two within TWIN_M, or the same brand or name within MERGE_M (the
  OpenStreetMap feature kept, an area before a node), or a node inside a mapped site.

Canopies: OpenStreetMap maps a station's canopy as a closed way tagged building=roof (sometimes building=canopy or
man_made=canopy). Overture carries those ways as buildings (class "roof", source OpenStreetMap), and the district
extruded them as solid blocks down to the ground. Each canopy inside a station's site, or within CANOPY_M of a station
mapped as a point (and nearer it than any other station), is the station's: exported with it, and the district leaves
out the buildings it covers (is_canopy()), since the game builds the canopy on columns in their place. A point
station pinned in or beside a canopy-sized footprint takes it for its canopy (adopt_pin_footprints()), and a detector's
footprints on a point station's lot are cleared for its forecourt (on_lot()).

Data (c) OpenStreetMap contributors (ODbL), Overture Maps Foundation (CDLA Permissive 2.0 and ODbL).
"""
import math

import numpy as np
import shapely
from shapely.geometry import Point, Polygon

import places as named_places

# Two stations nearer than this (m) are one; the same brand or name, within MERGE_M.
TWIN_M, MERGE_M = 35.0, 80.0
# A canopy belongs to a station mapped as a point when its centre is within this (m), and it is a canopy's size.
CANOPY_M = 45.0
CANOPY_AREA_M2 = (25.0, 1600.0)
# The ground kept free of compound walls and parked cars in front of a station mapped as a point, until the game lays
# its forecourt: this wide along the street (m) and this deep from the asphalt's edge (room for circle-game's medium
# forecourt, src/world/fuel.ts STANDARD).
LOT_W, LOT_D = 30.0, 24.0
# Buildings the district leaves out as canopies: those whose footprint lies this much (share of its area) under a
# station's canopy.
CANOPY_SHARE = 0.5

# Brand names as they are written on the forecourts (circle-reference texts.csv), for tags that spell them otherwise.
BRAND_SPELLING = {"total": "TotalEnergies", "totalenergies": "TotalEnergies", "goil": "GOIL", "staroil": "Star Oil",
                  "star oil": "Star Oil", "shell": "Shell"}


def brand_name(tag):
    """A brand tag as the forecourt writes it ("Total" became TotalEnergies in 2022; "Goil" is written GOIL)."""
    if not tag:
        return ""
    t = " ".join(str(tag).split())
    return BRAND_SPELLING.get(t.lower(), t)


def _poly(g):
    """The largest polygon of a geometry, or None."""
    if g is None or g.is_empty:
        return None
    if isinstance(g, Polygon):
        return g
    polys = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]
    return max(polys, key=lambda p: p.area) if polys else None


def _ring(poly, tol=0.3):
    return [[round(float(x), 1), round(float(z), 1)] for x, z in poly.simplify(tol).exterior.coords[:-1]]


def find_fuel_stations(osm_rows, place_rows, lines, inside, to_local, local, roofs=()):
    """The fuel stations of an area.

    osm_rows: places.load_osm() rows (the stations carry "fuel", the canopies "canopy"); place_rows:
    places.load_places() rows; lines: (name, width, LineString in local metres) of the streets a car drives;
    inside(x, z): whether a point (local metres, z north) belongs to the area; to_local(lon, lat) -> (x, z);
    local(geometry in lon/lat) -> geometry in local metres; roofs: footprints (local metres) of the slice's Overture
    buildings of class "roof", the canopies of a slice whose OpenStreetMap extract predates the canopies.

    Returns dicts: name (as the maps show it, "" when none), brand (as tagged, BRAND_SPELLING), operator, x and z (the
    site's or canopy's centre, else the pin), door (the nearest street's asphalt edge on the station's side), toward
    (unit vector from the station to the door), road (name, width, unit direction along it), site (the station's ground
    when OpenStreetMap maps it), shop (the shop's outline when the station is tagged on it), canopies (outlines), osm id
    or "source": "overture", person when the name names a private person (shown in the proof of concept, dropped by a
    public build: places.PUBLIC)."""
    cands = []
    for r in osm_rows:
        if not r.get("fuel"):
            continue
        tags = r.get("tags") or {}
        g = _poly(local(shapely.from_wkb(r["outline"]))) if r.get("outline") else None
        on_building = bool(g is not None and tags.get("building") not in (None, "no", "roof", "canopy"))
        cands.append({"name": named_places.garage_name(r.get("name")), "raw": r.get("name") or "",
                      "brand": brand_name(tags.get("brand")), "operator": tags.get("operator") or "",
                      "pt": to_local(r["lon"], r["lat"]), "site": None if on_building else g, "shop": g if on_building else None,
                      "osm": r["id"], "rank": 0 if g is not None and not on_building else 1})
    for r in place_rows:
        if r.get("source") != "places" or r.get("category") != "gas_station":
            continue
        if float(r.get("confidence") or 0) < named_places.MIN_CONFIDENCE_SAFETY or (r.get("status") or "").lower() in named_places.CLOSED:
            continue
        cands.append({"name": named_places.garage_name(r.get("name")), "raw": r.get("name") or "",
                      "brand": brand_name(r.get("brand")), "operator": "", "pt": to_local(r["lon"], r["lat"]),
                      "site": None, "shop": None, "osm": None, "rank": 2})
    # An area first, then the nodes, then Overture's: the better mapped one is kept.
    cands.sort(key=lambda c: (c["rank"], c["osm"] or c["raw"]))
    kept = []
    for c in cands:
        x, z = c["pt"]
        if c["site"] is not None:
            x, z = c["site"].centroid.x, c["site"].centroid.y
        if not inside(x, z):
            continue
        label = c["brand"] or c["raw"]
        twin = None
        for k in kept:
            d = math.hypot(k["x"] - x, k["z"] - z)
            klabel = k["brand"] or k["raw"]
            if (d <= TWIN_M or (k["site"] is not None and k["site"].buffer(5).contains(Point(x, z)))
                    or (label and klabel and d <= MERGE_M and named_places.same_name(klabel, label))):
                twin = k
                break
        if twin:
            # What the twin knows adds to the station: a name, a brand, a shop's outline.
            for key in ("name", "raw", "brand", "operator", "shop"):
                if not twin[key] and c[key]:
                    twin[key] = c[key]
            continue
        kept.append({**c, "x": x, "z": z})
    if not kept:
        return []

    # The canopies: OpenStreetMap's, and Overture's roofs that none of them covers.
    canopies = []
    for r in osm_rows:
        if r.get("canopy") and r.get("outline"):
            g = _poly(local(shapely.from_wkb(r["outline"])))
            if g is not None and g.area >= CANOPY_AREA_M2[0]:
                canopies.append(g)
    for g in roofs:
        g = _poly(g)
        if g is not None and g.area >= CANOPY_AREA_M2[0] and not any(g.intersection(c).area > 0.5 * g.area for c in canopies):
            canopies.append(g)
    taken = set()
    for k in kept:
        k["canopies"] = []
    for i, c in enumerate(canopies):
        cx, cz = c.centroid.x, c.centroid.y
        best, bd = None, math.inf
        for k in kept:
            if k["site"] is not None:
                if c.intersection(k["site"].buffer(3)).area >= 0.5 * c.area:
                    best, bd = k, 0.0
                    break
                continue
            d = math.hypot(k["x"] - cx, k["z"] - cz)
            if d < bd and d <= CANOPY_M and CANOPY_AREA_M2[0] <= c.area <= CANOPY_AREA_M2[1]:
                best, bd = k, d
        if best is not None and i not in taken:
            taken.add(i)
            best["canopies"].append(c)
    # A station mapped as a point stands where its canopy is.
    for k in kept:
        if k["site"] is None and k["canopies"]:
            u = shapely.union_all(k["canopies"]).centroid
            k["x"], k["z"] = u.x, u.y

    out = []
    tree = shapely.STRtree([l for _, _, l in lines]) if lines else None
    for k in kept:
        e = {"name": k["name"], "brand": k["brand"], "x": round(float(k["x"]), 1), "z": round(float(k["z"]), 1)}
        if k["operator"]:
            e["operator"] = k["operator"]
        if tree is not None:
            e.update(street_front(k["x"], k["z"], k["site"] if k["site"] is not None else Point(k["x"], k["z"]), lines, tree))
        if k["site"] is not None:
            e["site"] = _ring(k["site"])
        if k["shop"] is not None:
            e["shop"] = _ring(k["shop"])
        if k["canopies"]:
            e["canopies"] = [_ring(c) for c in k["canopies"]]
        e.update({"osm": k["osm"]} if k["osm"] else {"source": "overture"})
        if named_places.is_person(k["name"]) and not k["brand"]:
            e["person"] = True
        out.append(e)
    return out


def street_front(x, z, ground, lines, tree):
    """The street in front of a place at (x, z) (local metres) whose ground is `ground` (a polygon or a point): the
    nearest of `lines` (name, width, LineString) through `tree` (their STRtree), the point on its asphalt's edge on the
    place's side ("door"), the unit vector from the place to it ("toward") and the street (name, width, unit direction
    along it). The electric-vehicle chargers use it too (ev_chargers.py)."""
    name, w, line = lines[int(tree.nearest(ground))]
    # The point of the street in front of the place's centre, and the asphalt's edge on its side.
    at = line.project(Point(x, z))
    q = line.interpolate(at)
    a, b = line.interpolate(max(0.0, at - 2.0)), line.interpolate(min(line.length, at + 2.0))
    d = np.array([b.x - a.x, b.y - a.y]); d /= max(1e-6, np.hypot(*d))
    to = np.array([x - q.x, z - q.y])
    side = np.array([-d[1], d[0]])
    off = float(np.dot(to, side))
    if off < 0:
        side, off = -side, -off
    door = np.array([q.x, q.y]) + side * min(w / 2, off)
    toward = door - (x, z); toward = toward / max(1e-6, np.hypot(*toward))
    return {"door": [round(float(v), 1) for v in door], "toward": [round(float(v), 4) for v in toward],
            "road": {"name": name, "w": w, "dir": [round(float(v), 4) for v in d]}}


# A footprint round a point station's pin, of this size (m2), is taken for its canopy: mappers often pin the station
# inside the canopy they drew as a plain building (Oxford Street's Shell), and a detector draws the canopy too.
PIN_CANOPY_M2 = (60.0, 900.0)
# How far from its footprint a pin may stand (m): a pin dropped on the forecourt's edge (Oxford Street's Shell, 4 m out).
PIN_M = 4.5


def adopt_pin_footprints(stations, footprints):
    """For each station mapped as a point, with no site nor canopy: the building footprint (local metres) its pin
    stands in or beside (within PIN_M), when of a canopy's size (PIN_CANOPY_M2), becomes its canopy, and the station
    stands under it. Changes the stations in place; returns how many took one. footprints: (local polygon) of the
    buildings near a pin."""
    n = 0
    for st in stations:
        if st.get("site") or st.get("canopies"):
            continue
        pin = Point(st["x"], st["z"])
        near = [g for g in map(_poly, footprints) if g is not None and g.distance(pin) <= PIN_M and PIN_CANOPY_M2[0] <= g.area <= PIN_CANOPY_M2[1]]
        if not near:
            continue
        g = min(near, key=lambda q: q.distance(pin))
        st["canopies"] = [_ring(g)]
        st["x"], st["z"] = round(float(g.centroid.x), 1), round(float(g.centroid.y), 1)
        n += 1
    return n


def canopy_shapes(stations):
    """The stations' canopies as polygons (local metres), with an STRtree over them, for is_canopy()."""
    polys = [Polygon(r) for s in stations for r in s.get("canopies", []) if len(r) >= 3]
    polys = [p if p.is_valid else p.buffer(0) for p in polys]
    return polys, (shapely.STRtree(polys) if polys else None)


# The machine-learned footprints (Overture's sources other than OpenStreetMap): a detector draws a forecourt's canopy,
# often merged with its shop, as one building.
ML_SOURCES = {"Google Open Buildings", "Microsoft ML Buildings"}
# The largest footprint taken for a station's own (m2): over its canopy, or (machine-learned) on its lot.
ML_STATION_M2 = 1500.0


def is_canopy(footprint, shapes, source="OpenStreetMap"):
    """Whether a building's footprint (local metres) is one of the stations' canopies: CANOPY_SHARE of it lies under
    one (the same OpenStreetMap way through Overture, or a footprint drawn over it); or, up to ML_STATION_M2, it covers
    CANOPY_SHARE of a canopy: a detector drew the canopy and the shop as one building, or a mapper drew the station's
    whole roof round its canopy (East Legon's Shell on Boundary Road). A building cannot stand round an open canopy."""
    polys, tree = shapes
    if tree is None or footprint.is_empty:
        return False
    ml = footprint.area <= ML_STATION_M2
    for i in tree.query(footprint, predicate="intersects"):
        inter = footprint.intersection(polys[i]).area
        if inter >= CANOPY_SHARE * footprint.area or (ml and inter >= CANOPY_SHARE * polys[i].area):
            return True
    return False


def lot_shapes(stations):
    """The ground each station mapped as a point stands on (keep_clear()'s lots, and a disc of 6 m round its pin), with
    an STRtree, for on_lot()."""
    polys = [p for s, p in zip(stations, keep_clear(stations)) if not s.get("site")]
    polys += [Point(s["x"], s["z"]).buffer(6.0) for s in stations if not s.get("site")]
    return polys, (shapely.STRtree(polys) if polys else None)


def on_lot(footprint, shapes, source):
    """Whether a machine-learned footprint (ML_SOURCES, up to ML_STATION_M2) lies on a station's lot: CANOPY_SHARE of it
    inside one. A real station stands there, so what the detector drew on its ground is the station's own canopy,
    shop or kiosk, which the game's forecourt replaces. OpenStreetMap's buildings are never taken."""
    polys, tree = shapes
    if tree is None or source not in ML_SOURCES or footprint.is_empty or footprint.area > ML_STATION_M2:
        return False
    for i in tree.query(footprint, predicate="intersects"):
        if footprint.intersection(polys[i]).area >= CANOPY_SHARE * footprint.area:
            return True
    return False


def keep_clear(stations):
    """The ground compound walls and parked cars keep off (local metres): each station's site when mapped, else a lot
    LOT_W along the street and LOT_D deep from the asphalt's edge, on the station's side, round its canopies too."""
    out = []
    for s in stations:
        parts = [Polygon(r) for r in s.get("canopies", []) if len(r) >= 3]
        if s.get("site"):
            parts.append(Polygon(s["site"]))
        elif s.get("door"):
            (dx, dz), (tx, tz) = s["road"]["dir"], s["toward"]
            # Into the plot: away from the street, the way from the door back to the station.
            nx, nz = -tx, -tz
            ox, oz = s["door"]
            corners = [(ox + dx * a + nx * b, oz + dz * a + nz * b) for a, b in
                       ((-LOT_W / 2, 0), (LOT_W / 2, 0), (LOT_W / 2, LOT_D), (-LOT_W / 2, LOT_D))]
            parts.append(Polygon(corners))
        parts = [p if p.is_valid else p.buffer(0) for p in parts]
        if parts:
            out.append(shapely.union_all(parts))
    return out
