"""Writes aoi/starlets-91-kit.json, the kit's data for Starlets '91 Road's corridor (circle-game docs/starlets-91-plan.md,
stage 2), from the plan and OpenStreetMap, in the shape of aoi/independence-avenue-kit.json (scripts/avenue_kit.py).

    python3 scripts/starlets_kit_data.py --plan <circle-game docs/starlets-91-plan.md> --osm <dir of OSM map-call files>

--osm holds the answers of the OpenStreetMap API's map call over 5.542 to 5.560 N, -0.208 to -0.184 E (twelve tiles of
0.006 degrees, read on 23 Nov 2026), as circle-game's scripts/starlets_ground.py reads them.

What it writes:
- line: the road's reference line for the chainage, its eastbound carriageway from Independence Avenue to Castle Road
  (the plan averaged both carriageways; the two differ by some metres here, the segments' ends by tens at most);
- segments: the plan's, A to D by chainage; shop_segments: A and D, whose blocks the plan types T9 by segment (its
  Appendix C), as the avenue's F;
- corridor: the plan's, 150 m either side of either carriageway and 40 m either side of the cross streets' first
  250 m (the plan takes each to its next junction, at most 250 m: here the 250 m);
- lots: the plan's OSM lots outside the avenue's plan (Appendix A), with OpenStreetMap's tags and outline and the
  plan's treatment (Tn, KEEP, HERO, LAND, VOL);
- buildings: every OSM building the plan types itself (its "Every building" table, outside the avenue's plan's rows),
  at a point inside its outline, with its area and the plan's type: the plan's typology for each (T3, keep and the
  hero models leave the generic massing to their stages). The machine-learnt footprints are not named (their
  Overture ids are cut in the plan): the kit's rules type them, by their lot and segment.
"""
import argparse
import glob
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping
from shapely.ops import linemerge, unary_union

ROAD = "Starlets '91 Road"
# The eastbound carriageway, Independence Avenue to Castle Road, in order (OpenStreetMap ways, 23 Nov 2026).
EASTBOUND = [1437098156, 12211758, 11520784, 515102152, 514875714, 447600245, 521717608, 522486271, 1096884134,
             514454853, 352410323, 1191300279, 1096884135, 524510162, 515937840, 514454852]
SEGMENTS = [["A", 0, 540], ["B", 540, 1170], ["C", 1170, 1450], ["D", 1450, 1800]]
# The segments whose blocks the plan types T9 (Central's shop and office block) by segment: A, the old centre by
# Kinbu and Tudu, where it types no villa; D, the Osu blocks at the east end (its Appendix C).
SHOPS = ["A", "D"]
# The ministry blocks' brise-soleil on the walls facing the road within this reach (m) only: the plan's stage 2
# download ceiling (0.5 MB over the road's tiles) does not hold the avenue's fins on all of the Ministries' 70 blocks.
FIN_REACH = 80.0
BAND, CROSS_BAND, CROSS_REACH = 150.0, 40.0, 250.0
STREETS = ("primary", "secondary", "tertiary", "residential", "unclassified", "living_street", "service",
           "primary_link", "secondary_link", "tertiary_link", "trunk", "trunk_link")
TREAT = {"volume": "VOL", "keep": "KEEP", "hero": "HERO"}

fwd = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True).transform
back = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True).transform


def transform(f, g):
    """g with its coordinates through f (a pyproj transform of x and y arrays)."""
    return shapely.transform(g, lambda c: np.column_stack(f(c[:, 0], c[:, 1])))


def read_osm(folder):
    nodes, ways, rels = {}, {}, {}
    for f in sorted(glob.glob(os.path.join(folder, "*.osm"))):
        r = ET.parse(f).getroot()
        for n in r.iter("node"):
            nodes[int(n.get("id"))] = (float(n.get("lon")), float(n.get("lat")))
        for w in r.iter("way"):
            ways[int(w.get("id"))] = ([int(x.get("ref")) for x in w.iter("nd")], {t.get("k"): t.get("v") for t in w.iter("tag")})
        for e in r.iter("relation"):
            rels[int(e.get("id"))] = ([(m.get("type"), int(m.get("ref")), m.get("role")) for m in e.iter("member")],
                                      {t.get("k"): t.get("v") for t in e.iter("tag")})
    return nodes, ways, rels


def way_line(nodes, ways, wid):
    nd = ways[wid][0]
    pts = [nodes[i] for i in nd if i in nodes]
    return pts if len(pts) == len(nd) else None


def outline(kind, oid, nodes, ways, rels):
    """The lon/lat polygon of an OSM way or multipolygon relation (its outer rings), or None when not all in the files."""
    if kind == "way":
        if oid not in ways:
            return None
        pts = way_line(nodes, ways, oid)
        return Polygon(pts).buffer(0) if pts and len(pts) >= 4 else None
    if oid not in rels:
        return None
    lines = []
    for t, ref, role in rels[oid][0]:
        if t == "way" and role in ("outer", ""):
            pts = way_line(nodes, ways, ref) if ref in ways else None
            if not pts:
                return None
            lines.append(LineString(pts))
    merged = linemerge(lines)
    parts = [Polygon(g.coords) for g in getattr(merged, "geoms", [merged]) if g.is_ring]
    return unary_union([p.buffer(0) for p in parts]) if parts else None


def tags_of(kind, oid, ways, rels):
    return dict((ways if kind == "way" else rels).get(oid, ([], {}))[1])


def ring(g):
    return [[round(x, 7), round(y, 7)] for x, y in g.exterior.coords]


def parse_plan(path):
    text = open(path, encoding="utf-8").read()
    rows = []
    for line in text.splitlines():
        if not re.match(r"^\| [A-D]\d\d \| (\[|Overture)", line):
            continue
        c = [x.strip() for x in line.split("|")[1:-1]]
        m = re.match(r"\[(way|relation)/(\d+)\]", c[1])
        rows.append({"lot": c[0], "kind": m.group(1) if m else None, "osm": int(m.group(2)) if m else None,
                     "name": c[2], "levels": c[4], "area": c[5], "treat": c[9], "stage": c[11]})
    lots = []
    for m in re.finditer(r"^\*\*([A-D]\d\d)\. (.+?)\*\*(?: \(\[(way|relation)/(\d+)\])?(.*?)(?=^\*\*[A-D]\d\d\. |^## |^### )",
                         text, re.S | re.M):
        lot, name, kind, oid, body = m.groups()
        prop = re.search(r"^- Proposed: (.+)$", body, re.M)
        avenue = re.search(r"^- In the avenue plan: (.+)$", body, re.M)
        lots.append({"lot": lot, "name": name, "kind": kind, "osm": int(oid) if oid else None,
                     "proposed": prop.group(1) if prop else None, "avenue": avenue.group(1) if avenue else None})
    return rows, lots


def lot_treat(proposed):
    """The lot's treatment for the kit's rules: the plan's proposal, except that a lot whose buildings the plan types
    mostly T5 (besides its kiosks) is a ministry's grounds (T5), and a modelled lot whose other buildings take their
    types (the stadium's compound) takes the type of most of them (its model's buildings are kept by name)."""
    if not proposed:
        return None
    p = proposed.lower()
    inner = re.search(r"\(its buildings: ([^)]*)\)", p)
    if inner:
        n = Counter()
        for k, t in re.findall(r"(\d+) (t\d+|keep|hero|volume)", inner.group(1)):
            n[t] += int(k)
        others = {t: k for t, k in n.items() if t != "t10"}
        if others and max(others, key=others.get) == "t5":
            return "T5"
        if p.startswith("keep") and any(t != "keep" for t in n):
            rest = {t: k for t, k in n.items() if t != "keep"}
            return max(rest, key=rest.get).upper()
    m = re.match(r"typology (t\d+)", p)
    if m:
        return m.group(1).upper()
    for k, v in (("keep", "KEEP"), ("hero", "HERO"), ("landscape only", "LAND"), ("simple volume", "VOL")):
        if p.startswith(k):
            return v
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--osm", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aoi", "starlets-91-kit.json"))
    a = ap.parse_args()
    nodes, ways, rels = read_osm(a.osm)
    rows, plan_lots = parse_plan(a.plan)

    # The line and the corridor, in metres (UTM 30N).
    line = linemerge([LineString(way_line(nodes, ways, w)) for w in EASTBOUND])
    assert line.geom_type == "LineString", "the eastbound carriageway does not join up"
    road_ids = {w for w, (_, t) in ways.items() if t.get("name") == ROAD and t.get("highway")}
    road = unary_union([transform(fwd, LineString(way_line(nodes, ways, w))) for w in road_ids])
    road_nodes = {n for w in road_ids for n in ways[w][0]}
    cross = []
    for w, (nd, t) in ways.items():
        if w in road_ids or t.get("highway") not in STREETS or not t.get("name") or not road_nodes.intersection(nd):
            continue
        pts = way_line(nodes, ways, w)
        if pts:
            cross.append(transform(fwd, LineString(pts)).intersection(road.buffer(CROSS_REACH)))
    corridor_m = unary_union([road.buffer(BAND)] + [c.buffer(CROSS_BAND) for c in cross if not c.is_empty]).simplify(2.0)
    corridor = transform(back, corridor_m)
    polys = list(corridor.geoms) if isinstance(corridor, MultiPolygon) else [corridor]

    lots, missing = [], []
    for lt in plan_lots:
        if lt["osm"] is None or lt["avenue"]:
            continue
        g = outline(lt["kind"], lt["osm"], nodes, ways, rels)
        if g is None or g.is_empty:
            missing.append(f"{lt['lot']} {lt['kind']}/{lt['osm']}")
            continue
        g = max(getattr(g, "geoms", [g]), key=lambda p: p.area)
        tags = {k: v for k, v in tags_of(lt["kind"], lt["osm"], ways, rels).items()
                if k in ("amenity", "landuse", "leisure", "office", "government", "military", "diplomatic", "building", "name", "shop")}
        lots.append({"lot": lt["lot"], "osm": f"{lt['kind']}/{lt['osm']}", "name": lt["name"], "treat": lot_treat(lt["proposed"]),
                     "tags": tags, "outline": ring(g)})

    buildings = []
    for r in rows:
        if r["osm"] is None or r["stage"].startswith("avenue"):
            continue
        g = outline(r["kind"], r["osm"], nodes, ways, rels)
        if g is None or g.is_empty:
            missing.append(f"{r['lot']} {r['kind']}/{r['osm']}")
            continue
        m = transform(fwd, g)
        p = g.representative_point()
        lv = r["levels"]
        buildings.append({"lot": r["lot"], "osm": f"{r['kind']}/{r['osm']}", "name": r["name"] or None,
                          "treat": TREAT.get(r["treat"], r["treat"]), "levels": int(lv) if lv.isdigit() else None,
                          "at": [round(p.x, 7), round(p.y, 7)], "area_m2": round(m.area)})

    out = {
        "about": "Starlets '91 Road's kit (circle-game docs/starlets-91-plan.md, stage 2): the corridor, the road's reference "
                 "line and segments, the plan's OSM lots with their tags and the plan's typology for the OSM buildings it types "
                 "(lon, lat; WGS84). Read by scripts/avenue_kit.py; written by scripts/starlets_kit_data.py.",
        "source": "The plan's survey of 22 Nov 2026 (its lots, Appendix A, and its table of every building) and OpenStreetMap "
                  "(API map calls of 23 Nov 2026, (c) OpenStreetMap contributors, ODbL); the corridor is 150 m either side of "
                  "either carriageway plus 40 m either side of each cross street's first 250 m.",
        "line": [[round(x, 7), round(y, 7)] for x, y in line.coords],
        "segments": SEGMENTS,
        "shop_segments": SHOPS,
        "fins": {"reach_m": FIN_REACH},
        "corridor": [ring(p) for p in polys],
        "lots": lots,
        "buildings": buildings,
        "plan_counts": dict(sorted(Counter(TREAT.get(r["treat"], r["treat"]) for r in rows if not r["stage"].startswith("avenue")).items())),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"line {transform(fwd, line).length:.0f} m; corridor {corridor_m.area / 1e6:.2f} km2 in {len(polys)} part(s); "
          f"{len(lots)} lots; {len(buildings)} named buildings {dict(Counter(b['treat'] for b in buildings))}")
    print(f"lot treats {dict(Counter(l['treat'] for l in lots))}")
    if missing:
        print(f"not in the OSM files ({len(missing)}): {', '.join(missing)}")


if __name__ == "__main__":
    main()
