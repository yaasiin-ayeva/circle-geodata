"""Accra's electric-vehicle chargers for the game: where each one is, who runs it, how many cars it takes and what it
plugs into as tagged, the street in front, and the fuel station or car park it stands in. build_district.py writes them
as the district's "evChargers"; export_scene.py passes them to the game, which builds each one's bays (circle-game
src/world/ev.ts, docs/ev-charging.md).

    python3 tests/test_ev_chargers.py

Sources, merged as the fuel stations are (fuel_stations.py):
  - OpenStreetMap's amenity=charging_station (osm_extract.py): a node, or a closed way round its site; its operator,
    brand, network, capacity, socket:* and output tags as mapped. The chargers for bicycles, scooters or motorcycles
    only (motorcar=no), and the private ones, are left out. A lone man_made=charge_point joins the station within
    POINT_M of it (a car more, when capacity is not tagged), or is a station of its own.
  - Overture's places of category ev_charging_station (or charging_station) at the maps' safety floor, not closed.
  - The chargers documented by their operators or the press but mapped by neither (aoi/ev-chargers.json): each only
    at a host OpenStreetMap maps (the fuel station or the car park its sources name, by its id), so no position is
    guessed; a mapped charger within DOC_M of the host replaces it.
  One charger where two map the same one: any two within TWIN_M, the same name or operator within MERGE_M, or a point
  inside a mapped site; the OpenStreetMap feature is kept, an area before a node.

Where it stands: a charger inside a fuel station's site or canopy, or within FUEL_M of a station's point (and nearer
it than any other), is that station's ("fuel": the station's point, id and brand): the game puts its bays on that
station's forecourt rather than building a second one. One inside or beside (HOST_M) a car park or a mall OpenStreetMap
maps takes that ground for its site when its own is not mapped ("host").

In Accra (the Ghana extract of 17 Nov 2026, Overture 2026-10-21.0): OpenStreetMap maps one amenity=charging_station,
in north-west Accra (west of Achimota), outside the game's areas; Overture none. The two documented ones are the city's chargers (README).

Data (c) OpenStreetMap contributors (ODbL), Overture Maps Foundation (CDLA Permissive 2.0 and ODbL).
"""
import json
import math
import re

import shapely
from shapely.geometry import Point, Polygon

import places as named_places
from fuel_stations import _poly, _ring, street_front

# Two chargers nearer than this (m) are one; the same name or operator, within MERGE_M.
TWIN_M, MERGE_M = 30.0, 80.0
# A lone charge point joins a station this near (m).
POINT_M = 30.0
# A charger this near a fuel station's point (m), or inside its site or canopy, is that station's.
FUEL_M = 40.0
# A charger this near a car park's or a mall's outline (m) stands on it.
HOST_M = 15.0
# A documented charger gives way to a mapped one this near its host (m).
DOC_M = 60.0
# The ground kept free of compound walls and parked cars in front of a charger mapped as a point, until the game lays
# its bays: this wide along the street and this deep from the asphalt's edge (m; room for circle-game's four bays,
# src/world/ev.ts).
LOT_W, LOT_D = 16.0, 10.0

# The plugs, by OpenStreetMap's socket:* keys: direct current (a fast charger's cabinet) or alternating (a post or a
# wallbox).
DC = {"type2_combo", "chademo", "type1_combo", "gb_dc", "tesla_supercharger", "tesla_supercharger_ccs", "nacs"}
AC = {"type2", "type2_cable", "type1", "gb_ac", "type3c", "schuko", "cee_blue", "cee_red_16a", "cee_red_32a", "bs1363"}
# Overture's categories for a charger.
CATEGORIES = {"ev_charging_station", "charging_station"}
# The kinds of host a charger stands in.
HOST_TAGS = {("amenity", "parking"): "parking", ("shop", "mall"): "mall"}

NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _int(v):
    """A tag's count ("2", "yes" is 1), or None."""
    if v is None:
        return None
    v = str(v).strip().lower()
    if v in ("yes", "true"):
        return 1
    m = NUMBER.match(v)
    return int(float(m.group(1))) if m else None


def _kw(v):
    """A socket's output tag ("50 kW", "22", "7.4 kW;22 kW", "11000 W") in kW, the largest, or None."""
    if not v:
        return None
    best = None
    for part in str(v).split(";"):
        m = NUMBER.search(part)
        if not m:
            continue
        x = float(m.group(1))
        unit = part.lower()
        if "mw" in unit:
            x *= 1000
        elif "kw" not in unit and re.search(r"\bw\b|\dw\b", unit):
            x /= 1000
        best = x if best is None else max(best, x)
    return best


def sockets_of(tags):
    """A charger's plugs as tagged: ({socket type: count}, the largest output in kW or None)."""
    out, kw = {}, None
    for k, v in (tags or {}).items():
        if not k.startswith("socket:"):
            continue
        parts = k.split(":")
        if len(parts) == 2:
            n = _int(v)
            if n:
                out[parts[1]] = n
        elif len(parts) == 3 and parts[2] == "output":
            x = _kw(v)
            if x is not None:
                kw = x if kw is None else max(kw, x)
    return out, kw


def kind_of(sockets, kw):
    """"dc" (a fast charger's cabinet), "ac" (a post), "mixed" (both) or "" (not tagged), by the plugs and the power."""
    dc = any(s in DC for s in sockets) or (kw is not None and kw >= 40)
    ac = any(s in AC for s in sockets) or (kw is not None and kw <= 22 and not any(s in DC for s in sockets))
    return "mixed" if dc and ac else "dc" if dc else "ac" if ac else ""


def for_cars(tags):
    """Whether a charger is for cars, and open to them: not tagged for bicycles, scooters or motorcycles only, and not
    private."""
    t = tags or {}
    if t.get("motorcar") == "no" or t.get("access") in ("private", "no"):
        return False
    two = any(t.get(k) in ("yes", "designated") for k in ("bicycle", "scooter", "motorcycle"))
    return not two or t.get("motorcar") in ("yes", "designated")


def read_hosts(osm_path, local, ids=()):
    """The car parks and malls OpenStreetMap maps in an area (osm.parquet, osm_extract.py), and the features named by
    `ids` (the documented chargers' hosts): {id: {"kind", "name", "pt" (local metres), "outline" (local polygon or
    None)}}. local: geometry in lon/lat -> local metres."""
    import pyarrow.parquet as pq

    out = {}
    want = set(ids)
    for r in pq.read_table(osm_path, columns=["id", "name", "key", "value", "lon", "lat", "outline"]).to_pylist():
        kind = HOST_TAGS.get((r["key"], r["value"]))
        if kind is None and r["id"] not in want:
            continue
        g = _poly(local(shapely.from_wkb(r["outline"]))) if r.get("outline") else None
        p = local(Point(r["lon"], r["lat"]))
        out[r["id"]] = {"kind": kind or ("fuel" if (r["key"], r["value"]) == ("amenity", "fuel") else r["value"]),
                        "name": named_places.clean_name(r["name"]) or "", "pt": (p.x, p.y), "outline": g}
    return out


def load_documented(path):
    """aoi/ev-chargers.json's chargers (the documented ones), or [] when there is none."""
    try:
        with open(path) as f:
            return json.load(f).get("chargers", [])
    except FileNotFoundError:
        return []


def find_ev_chargers(osm_rows, place_rows, lines, inside, to_local, local, fuel=(), hosts=None, documented=()):
    """The electric-vehicle chargers of an area.

    osm_rows: places.load_osm() rows (the chargers carry "charging", the lone charge points "charge_point");
    place_rows: places.load_places() rows; lines: (name, width, LineString in local metres) of the streets a car
    drives; inside(x, z): whether a point (local metres, z north) belongs to the area; to_local(lon, lat) -> (x, z);
    local(geometry in lon/lat) -> geometry in local metres; fuel: the area's fuel stations (fuel_stations.py, local
    metres); hosts: read_hosts()'s car parks, malls and documented hosts; documented: load_documented()'s entries.

    Returns dicts: name (as the maps show it, "" when none), operator, brand, network (as tagged), x and z (the site's
    centre, else the pin; a documented one's host's), door, toward and road (the street in front: street_front()),
    site (its ground when mapped, else its host's), capacity (cars at once, when tagged or counted), sockets ({type:
    count} as tagged), kw (the largest output tagged), kind ("dc", "ac", "mixed" or "" by the plugs), fuel (the fuel
    station it stands in: its x, z, id and brand), host (the car park or mall: kind and name), osm id or "source"
    ("overture", "documented" with its "sources" URLs), fee, person (a name that names a private person)."""
    hosts = hosts or {}
    cands = []
    for r in osm_rows:
        if not r.get("charging"):
            continue
        tags = r.get("tags") or {}
        if not for_cars(tags):
            continue
        g = _poly(local(shapely.from_wkb(r["outline"]))) if r.get("outline") else None
        socks, kw = sockets_of(tags)
        cands.append({"name": named_places.garage_name(r.get("name")), "raw": r.get("name") or "",
                      "operator": tags.get("operator") or "", "brand": tags.get("brand") or "", "network": tags.get("network") or "",
                      "pt": to_local(r["lon"], r["lat"]), "site": g, "capacity": _int(tags.get("capacity")),
                      "sockets": socks, "kw": kw, "fee": tags.get("fee") or "", "osm": r["id"], "source": None,
                      "rank": 0 if g is not None else 1, "points": 0})
    for r in place_rows:
        if r.get("source") != "places" or r.get("category") not in CATEGORIES:
            continue
        if float(r.get("confidence") or 0) < named_places.MIN_CONFIDENCE_SAFETY or (r.get("status") or "").lower() in named_places.CLOSED:
            continue
        cands.append({"name": named_places.garage_name(r.get("name")), "raw": r.get("name") or "", "operator": "",
                      "brand": r.get("brand") or "", "network": "", "pt": to_local(r["lon"], r["lat"]), "site": None,
                      "capacity": None, "sockets": {}, "kw": None, "fee": "", "osm": None, "source": "overture", "rank": 2,
                      "points": 0})
    cands.sort(key=lambda c: (c["rank"], c["osm"] or c["raw"]))
    kept = []
    for c in cands:
        x, z = c["pt"]
        if c["site"] is not None:
            x, z = c["site"].centroid.x, c["site"].centroid.y
        if not inside(x, z):
            continue
        label = c["operator"] or c["brand"] or c["raw"]
        twin = None
        for k in kept:
            d = math.hypot(k["x"] - x, k["z"] - z)
            klabel = k["operator"] or k["brand"] or k["raw"]
            if (d <= TWIN_M or (k["site"] is not None and k["site"].buffer(5).contains(Point(x, z)))
                    or (label and klabel and d <= MERGE_M and named_places.same_name(klabel, label))):
                twin = k
                break
        if twin:
            for key in ("name", "raw", "operator", "brand", "network", "capacity", "kw", "fee"):
                if not twin[key] and c[key]:
                    twin[key] = c[key]
            for s, n in c["sockets"].items():
                twin["sockets"].setdefault(s, n)
            continue
        kept.append({**c, "x": x, "z": z})

    # The lone charge points: a car more at the station they stand by, or a station of their own.
    for r in osm_rows:
        if not r.get("charge_point"):
            continue
        tags = r.get("tags") or {}
        if not for_cars(tags):
            continue
        x, z = to_local(r["lon"], r["lat"])
        if not inside(x, z):
            continue
        near = [k for k in kept if math.hypot(k["x"] - x, k["z"] - z) <= POINT_M]
        socks, kw = sockets_of(tags)
        if near:
            k = min(near, key=lambda k: math.hypot(k["x"] - x, k["z"] - z))
            k["points"] += 1
            for s, n in socks.items():
                k["sockets"][s] = k["sockets"].get(s, 0) + n
            k["kw"] = max(filter(None, (k["kw"], kw)), default=None)
            continue
        kept.append({"name": named_places.garage_name(r.get("name")), "raw": r.get("name") or "", "operator": tags.get("operator") or "",
                     "brand": tags.get("brand") or "", "network": tags.get("network") or "", "pt": (x, z), "site": None,
                     "capacity": _int(tags.get("capacity")), "sockets": socks, "kw": kw, "fee": tags.get("fee") or "",
                     "osm": r["id"], "source": None, "rank": 1, "points": 1, "x": x, "z": z})

    # The documented chargers, at their hosts, where nothing mapped stands near.
    for doc in documented:
        h = hosts.get(doc.get("host"))
        if h is None:
            continue
        ground = h["outline"]
        x, z = (ground.centroid.x, ground.centroid.y) if ground is not None and h["kind"] != "fuel" else h["pt"]
        if not inside(x, z) or any(math.hypot(k["x"] - x, k["z"] - z) <= DOC_M for k in kept):
            continue
        kept.append({"name": "", "raw": "", "operator": doc.get("operator") or "", "brand": doc.get("brand") or "",
                     "network": doc.get("network") or "", "pt": (x, z), "site": None, "capacity": doc.get("capacity"),
                     "sockets": dict(doc.get("sockets") or {}), "kw": doc.get("kw"), "fee": "", "osm": None,
                     "source": "documented", "sources": list(doc.get("sources") or []), "rank": 3, "points": 0,
                     "x": x, "z": z, "doc_host": doc.get("host"), "doc_kind": doc.get("kind") or "",
                     "host_name": doc.get("host_name") or ""})
    if not kept:
        return []

    # The fuel station each stands in, and the car park or mall.
    fuel_shapes = []
    for f in fuel:
        parts = [Polygon(r) for r in f.get("canopies", []) if len(r) >= 3]
        if f.get("site"):
            parts.append(Polygon(f["site"]))
        parts = [p if p.is_valid else p.buffer(0) for p in parts]
        fuel_shapes.append(shapely.union_all(parts).buffer(4.0) if parts else None)
    host_list = [(hid, h) for hid, h in hosts.items() if h["kind"] in ("parking", "mall") and h["outline"] is not None]
    tree = shapely.STRtree([l for _, _, l in lines]) if lines else None
    out = []
    for k in kept:
        p = Point(k["x"], k["z"])
        station, best = None, math.inf
        for f, shape in zip(fuel, fuel_shapes):
            d = math.hypot(f["x"] - k["x"], f["z"] - k["z"])
            if k.get("doc_host") and (f.get("osm") == k["doc_host"]):
                station, best = f, -1.0
                break
            if ((shape is not None and shape.contains(p)) or d <= FUEL_M) and d < best:
                station, best = f, d
        host = None
        if station is None:
            if k.get("doc_host") in hosts and hosts[k["doc_host"]]["kind"] in ("parking", "mall"):
                host = hosts[k["doc_host"]]
            else:
                near = [(h["outline"].distance(p), h) for _, h in host_list if h["outline"].distance(p) <= HOST_M]
                host = min(near, key=lambda t: t[0])[1] if near else None
        site = k["site"] if k["site"] is not None else (host["outline"] if host else None)
        e = {"name": k["name"], "x": round(float(k["x"]), 1), "z": round(float(k["z"]), 1)}
        for key in ("operator", "brand", "network", "fee"):
            if k[key]:
                e[key] = k[key]
        cap = k["capacity"] or (k["points"] if k["points"] > 1 else None) or (sum(k["sockets"].values()) if k["sockets"] else None)
        if cap:
            e["capacity"] = int(cap)
        if k["sockets"]:
            e["sockets"] = dict(sorted(k["sockets"].items()))
        if k["kw"]:
            e["kw"] = round(float(k["kw"]), 1)
        kind = kind_of(k["sockets"], k["kw"]) or k.get("doc_kind", "")
        if kind:
            e["kind"] = kind
        if tree is not None:
            e.update(street_front(k["x"], k["z"], site if site is not None else p, lines, tree))
        if site is not None:
            e["site"] = _ring(site)
        if station is not None:
            e["fuel"] = {"x": station["x"], "z": station["z"], **({"osm": station["osm"]} if station.get("osm") else {"source": station.get("source", "overture")}),
                         **({"brand": station["brand"]} if station.get("brand") else {})}
        if host is not None:
            e["host"] = {"kind": host["kind"], "name": k.get("host_name") or host["name"]}
        if k["osm"]:
            e["osm"] = k["osm"]
        else:
            e["source"] = k["source"]
            if k.get("sources"):
                e["sources"] = k["sources"]
        if named_places.is_person(k["name"]) and not (k["operator"] or k["brand"]):
            e["person"] = True
        out.append(e)
    return out


def keep_clear(chargers):
    """The ground compound walls and parked cars keep off (local metres), for a charger not on a fuel station's
    forecourt: its site when mapped (its own, or the car park or mall it stands in); else a lot LOT_W along the street
    and LOT_D deep from the asphalt's edge, on its side."""
    out = []
    for c in chargers:
        if c.get("fuel"):
            continue
        if c.get("site"):
            p = Polygon(c["site"])
            out.append(p if p.is_valid else p.buffer(0))
        elif c.get("door"):
            (dx, dz), (tx, tz) = c["road"]["dir"], c["toward"]
            nx, nz = -tx, -tz
            ox, oz = c["door"]
            out.append(Polygon([(ox + dx * a + nx * b, oz + dz * a + nz * b) for a, b in
                                ((-LOT_W / 2, 0), (LOT_W / 2, 0), (LOT_W / 2, LOT_D), (-LOT_W / 2, LOT_D))]))
    return out
