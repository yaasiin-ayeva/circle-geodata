"""Accra's interchanges, flyovers, underpasses and footbridges, at their real levels: data/work/elevated.json.

    python3 scripts/elevated.py --pbf data/osm/ghana.osm.pbf --out data/work/elevated.json

Until now every road lay on the ground: where a flyover crosses a road, the two met in one junction and the deck was
drawn flat on the ground under it. OpenStreetMap maps the levels: a bridge carries `bridge=yes` and `layer=1` or `2`
(the Kwame Nkrumah Interchange at Circle and the Obetsebi Lamptey Interchange have three tiers), an underpass
`tunnel=yes` and `layer=-1` (the Tema Motorway Interchange), and the slip roads run on from the decks as `*_link`
ways. This turns them into height offsets above the natural ground, vertex by vertex, every 5 m:

  1. Which bridges are flyovers: a group of bridge ways of one layer (joined end to end) that crosses a drivable road
     or a footbridge that crosses a main road. The other bridges cross drains and the lagoon at the ground's level
     (Graphic Road over the Odaw) and stay roads on the ground.
  2. The level each one stands at: (layer - base) x LEVEL_M, where base is the layer at the ground of its site.
     Most are built up from the ground (base 0: Circle, Obetsebi Lamptey, Tema, the flyovers). At the Ako Adjei
     Interchange the roundabout is at the ground and the Ring Road runs in a cut under it, and at the Tetteh Quarshie
     Interchange the motorway is at the ground and Liberation Road runs in a cut under it (SITES, base 1, from photos:
     circle-reference interchanges notes and Wikimedia Commons, Tetteh Quarshie Interchange (Accra).jpg). A road
     under a bridge of its site's base level is held at -LEVEL_M where it passes under.
  3. The approaches: from every held vertex the level runs along the roads at GRADE (5 %, an urban flyover's ramp)
     back to the ground, until a junction on the ground; where the junction is too close for GRADE, the ramp
     steepens to MAX_GRADE (9 %), and beyond that the junction itself is lifted (a junction on a deck). The main
     roads carry the levels, and so does whatever road a flyover ends on; side streets stop at the walls.
  4. No step steeper than MAX_GRADE: where two levels still meet within metres (a slip road off Circle's top tier,
     tagged a layer lower over its whole length), the higher runs on down along the roads at MAX_GRADE, held vertices
     included; a cut and a ramp of opposite signs that meet get one even grade between them.

Each structure is cut into pieces that start and end on the ground (or on another piece). Per piece: the OpenStreetMap
way, class, name, width, lanes, one-way, the site and its style, the points (UTM 30N metres) and per point the offset
above the ground (m) and the kind of support under it: D a deck on piers, F an approach on fill between retaining
walls, C a cut, T a tunnel, G the ground where a piece starts or ends. build_district.py takes these pieces out of the roads it lays on the ground and writes
the ones in its area into the district (`structures`); relief.py gives them their absolute heights and lays the cuts
into the ground; the game builds the decks, piers, walls, parapets and lamps, and drives on them (circle-game
src/world/interchanges.ts).

Data (c) OpenStreetMap contributors, ODbL 1.0. Needs pyosmium, shapely, pyproj, numpy.
"""
import argparse
import json
import heapq
import math
import os
from collections import defaultdict

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString

LEVEL_M = 7.0       # one level: 5.3 m of clearance and the deck's depth with its surfacing (Ghana Highway Authority)
FOOT_M = 6.2        # a footbridge's deck over a main road: 5.5 m of clearance and the deck
GRADE = 0.05        # the approaches' grade
MAX_GRADE = 0.09    # steepest before a junction is lifted onto the deck
STEP_M = 5.0        # vertices along the structures
HOLD_M = 8.0        # a road under a base-level bridge is held down this far either side of the bridge's edge
EPS = 0.05
BOX = (5.50, 5.90, -0.27, 0.03)   # south, north, west, east: the whole world with a margin

DRIVE = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
         "service", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link")
FOOT = ("footway", "path", "pedestrian", "steps", "cycleway")
MAIN = ("motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link")
# The roads an approach runs along: a lane or a side street that meets one on the plan meets the frontage road in
# fact, not the ramp, so the ramp runs on past it (the side street stops at its wall) and is never lifted for it.
CARRY = MAIN + ("tertiary", "tertiary_link")
# Widths by class for a carriageway with no lane count (one way) and a two-way road (m), and a lane's width.
ONE_WAY_W = {"motorway": 11.5, "trunk": 11.0, "primary": 7.5, "secondary": 7.0, "tertiary": 6.6}
TWO_WAY_W = {"motorway": 22.0, "trunk": 18.0, "primary": 13.0, "secondary": 10.0, "tertiary": 8.5, "residential": 6.0,
             "unclassified": 6.0, "living_street": 5.0, "service": 4.5}
LANE_M = {"motorway": 3.65, "trunk": 3.5, "primary": 3.5, "secondary": 3.3, "tertiary": 3.1}

# The interchanges, from their photos (lat, lon, radius m): the layer at the ground ("base") and the look the game
# gives them ("style", circle-game src/world/interchanges.ts STYLES). Structures elsewhere are base 0, "flyover".
SITES = [
    # Three tiers (2016): the old roundabout at the ground, Kwame Nkrumah Avenue to the Nsawam Road on the first
    # deck with the loop round the lorry park, the Ring Road over both on the second. Grey concrete on round piers
    # with hammerhead caps, a concrete parapet with a steel rail on it, twin-arm lamps on the decks.
    {"name": "Kwame Nkrumah Interchange", "lat": 5.5695, "lon": -0.2157, "r": 800, "base": 0, "style": "circle"},
    # Ring Road Central in a walled cut under the roundabout, Independence Avenue over both (1999); the walls
    # painted with Ga culture since 2020.
    {"name": "Ako Adjei Interchange", "lat": 5.5721, "lon": -0.1904, "r": 300, "base": 1, "style": "ako-adjei"},
    # Three tiers (2022-24): the remodelled roundabout, the Graphic Road to Kaneshie on the first deck, Ring Road West
    # on the second; murals on the fill walls, piers painted yellow, purple and red.
    {"name": "Obetsebi Lamptey Interchange", "lat": 5.5610, "lon": -0.2292, "r": 600, "base": 0, "style": "obetsebi"},
    # The motorway at the ground, Liberation Road and J.J. Rawlings Avenue in a cut under it with grassed banks.
    {"name": "Tetteh Quarshie Interchange", "lat": 5.6232, "lon": -0.1767, "r": 400, "base": 1, "style": "tetteh-quarshie"},
    # The motorway in a 730 m underpass, the roundabout at the ground, the Harbour Road flyover over both (2020-24).
    {"name": "Tema Motorway Interchange", "lat": 5.6865, "lon": -0.0148, "r": 600, "base": 0, "style": "tema"},
]


def osm_ways(pbf, box=BOX):
    """Every highway way with a point in the box: id, tags, node ids, lon/lat."""
    import osmium
    s, n, w, e = box
    out = []

    class H(osmium.SimpleHandler):
        def way(self, way):
            t = dict(way.tags)
            if "highway" not in t:
                return
            try:
                pts = [(nd.lon, nd.lat) for nd in way.nodes]
            except osmium.InvalidLocationError:
                return
            if len(pts) < 2 or not any(s < la < n and w < lo < e for lo, la in pts):
                return
            out.append({"id": way.id, "tags": t, "nodes": [nd.ref for nd in way.nodes], "ll": pts})

    H().apply_file(pbf, locations=True, idx="flex_mem")
    return out


def layer_of(t):
    try:
        return int(float(t.get("layer", "0")))
    except ValueError:
        return 0


def is_bridge(t):
    return t.get("bridge", "no") not in ("no", "") and layer_of(t) >= 0


def is_tunnel(t):
    return t.get("tunnel", "no") not in ("no", "", "building_passage", "culvert")


def width(t):
    """The carriageway's width (m): OpenStreetMap's lanes when mapped, else the class's."""
    cls = t["highway"].replace("_link", "")
    oneway = t.get("oneway") in ("yes", "1", "true") or t["highway"].endswith("_link") or cls == "motorway"
    try:
        lanes = int(t.get("lanes", "0"))
    except ValueError:
        lanes = 0
    if lanes and cls in LANE_M:
        return round(lanes * LANE_M[cls] + 0.6, 1)
    if t["highway"].endswith("_link"):
        return 6.5
    return (ONE_WAY_W if oneway else TWO_WAY_W).get(cls, TWO_WAY_W.get(cls, 6.0))


def site_of(x, y, sites):
    for s in sites:
        if math.hypot(x - s["x"], y - s["y"]) < s["r"]:
            return s
    return None


def build(ways, to_utm):
    sites = []
    for s in SITES:
        x, y = to_utm.transform(s["lon"], s["lat"])
        sites.append({**s, "x": x, "y": y})
    drive = [w for w in ways if w["tags"]["highway"] in DRIVE]
    for w in ways:
        e, n = to_utm.transform(np.array([p[0] for p in w["ll"]]), np.array([p[1] for p in w["ll"]]))
        w["xy"] = np.column_stack([e, n])
        w["line"] = LineString(w["xy"])
    lines = [w["line"] for w in drive]
    tree = shapely.STRtree(lines)

    # 1. Flyovers: bridge groups (same layer, joined) that cross a drivable road; tunnels; footbridges over main roads.
    bridges = [w for w in drive if is_bridge(w["tags"])]
    by_node = defaultdict(list)
    for w in bridges:
        for nd in (w["nodes"][0], w["nodes"][-1]):
            by_node[nd].append(w)
    crosses = {}
    for w in bridges:
        crosses[w["id"]] = any(
            lines[j].crosses(w["line"]) and not set(drive[j]["nodes"]) & set(w["nodes"])
            and not (is_bridge(drive[j]["tags"]) and layer_of(drive[j]["tags"]) == layer_of(w["tags"]))
            for j in tree.query(w["line"]))
    flyover = set()
    for w in bridges:
        if not crosses[w["id"]] or w["id"] in flyover:
            continue
        stack = [w]
        while stack:
            u = stack.pop()
            if u["id"] in flyover:
                continue
            flyover.add(u["id"])
            for nd in (u["nodes"][0], u["nodes"][-1]):
                for v in by_node[nd]:
                    if v["id"] not in flyover and layer_of(v["tags"]) == layer_of(u["tags"]):
                        stack.append(v)
    # Underpasses at the interchanges only: elsewhere a "tunnel" is a road under the railway or a passage a few metres
    # long, at the ground's level for the game.
    tunnels = {w["id"] for w in drive if is_tunnel(w["tags"]) and layer_of(w["tags"]) < 0
               and site_of(*w["line"].interpolate(0.5, normalized=True).coords[0], sites)}

    # Dense graph: every OSM node, plus vertices every STEP_M along the ways near a structure.
    near = shapely.union_all([w["line"] for w in drive if w["id"] in flyover or w["id"] in tunnels]).buffer(900)
    shapely.prepare(near)
    pos = {}                    # vertex -> (x, y)
    adj = defaultdict(list)     # vertex -> [(vertex, length, way id)]
    wverts = {}                 # way id -> its vertices in order
    for w in drive:
        if not near.intersects(w["line"]):
            continue
        vs = []
        for i, nd in enumerate(w["nodes"]):
            p = tuple(w["xy"][i])
            if i:
                a = np.array(pos[vs[-1]])
                seg = math.dist(a, p)
                k = int(seg // STEP_M)
                for j in range(1, k + 1):
                    if seg - j * seg / (k + 1) < 0.5:
                        break
                    v = ("w", w["id"], i, j)
                    pos[v] = tuple(a + (np.array(p) - a) * j / (k + 1))
                    vs.append(v)
            pos[nd] = p
            vs.append(nd)
        wverts[w["id"]] = vs
        for a, b in zip(vs, vs[1:]):
            if a == b:
                continue
            d = math.dist(pos[a], pos[b])
            adj[a].append((b, d, w["id"]))
            adj[b].append((a, d, w["id"]))
    wby = {w["id"]: w for w in drive}

    # 2. Held offsets.
    held = {}

    def hold(v, off):
        held[v] = max(held[v], off, key=abs) if v in held else off

    for wid, vs in wverts.items():
        t = wby[wid]["tags"]
        s = site_of(*pos[vs[len(vs) // 2]], sites)
        base = s["base"] if s else 0
        if wid in flyover:
            for v in vs:
                hold(v, (layer_of(t) - base) * LEVEL_M)
        elif wid in tunnels:
            # Held down only where it passes under a deck (the portals' ramps run on at the approaches' grade).
            over = [wby[b]["line"] for b in flyover if b in wby and wby[b]["line"].distance(wby[wid]["line"]) < 1.0]
            over_u = shapely.union_all(over) if over else None
            reach = width(t) / 2 + HOLD_M
            for v in vs:
                if over_u is None or over_u.distance(shapely.Point(pos[v])) < reach + width(t):
                    hold(v, (min(layer_of(t), -1) - base) * LEVEL_M if base <= 0 else (layer_of(t) - base) * LEVEL_M)
    # Roads under a base-level bridge at a site whose base is above 0: held in their cut. The nodes of the ways the
    # bridge joins, per bridge: a road ending on those is an approach at the ground, not a road under it.
    way_of_node = defaultdict(list)
    for wid in wverts:
        for nd in wby[wid]["nodes"]:
            way_of_node[nd].append(wid)
    near_bridge = {}
    for bid in flyover:
        if bid not in wby:
            continue
        nds = set()
        for nd in wby[bid]["nodes"]:
            for o in way_of_node.get(nd, ()):
                if o != bid:
                    nds |= set(wby[o]["nodes"])
        near_bridge[bid] = nds
    for wid, vs in wverts.items():
        t = wby[wid]["tags"]
        if wid in flyover or wid in tunnels:
            continue
        line = wby[wid]["line"]
        for bid in flyover:
            b = wby.get(bid)
            if b is None or bid not in near_bridge:
                continue
            s = site_of(*b["line"].interpolate(0.5, normalized=True).coords[0], sites)
            if not s or s["base"] <= 0 or layer_of(b["tags"]) != s["base"] or not line.crosses(b["line"]):
                continue
            if set(b["nodes"]) & set(wby[wid]["nodes"]):
                continue
            # A road that enters the bridge's own ring or carriageway (Independence Avenue onto the Ako Adjei
            # roundabout) meets it at the ground: it only seems to cross where it joins.
            if near_bridge[bid] & set(wby[wid]["nodes"]):
                continue
            x = line.intersection(b["line"])
            reach = width(b["tags"]) / 2 + HOLD_M
            for v in vs:
                if x.distance(shapely.Point(pos[v])) < reach:
                    hold(v, (layer_of(t) - s["base"]) * LEVEL_M)

    # 3. Approaches: chains of degree-2 vertices between held vertices and junctions.
    # The roads carrying the levels: the main ones, and whatever road a flyover or an underpass ends on (its approach,
    # an unclassified street at times), so that no deck ends in the air.
    ends = {nd for wid in flyover | tunnels if wid in wby for nd in (wby[wid]["nodes"][0], wby[wid]["nodes"][-1])}
    carry = {wid for wid, w in wby.items() if w["tags"]["highway"] in CARRY or wid in flyover or wid in tunnels
             or ends & set(w["nodes"])}
    cadj = {v: [e for e in es if e[2] in carry] for v, es in adj.items()}

    def degree(v):
        return len({u for u, _, _ in cadj[v]})

    off = dict(held)
    lifted = {}
    for _ in range(4):
        changed = False
        seen = set()
        for start in list(off):
            for first, d0, wid in cadj.get(start, ()):
                if first in off and first not in lifted:
                    continue
                if (start, first) in seen:
                    continue
                chain, length, prev, cur = [(first, d0)], d0, start, first
                while cur not in off and degree(cur) == 2:
                    nxt = [(u, d) for u, d, _ in cadj[cur] if u != prev]
                    if not nxt:
                        break
                    prev, (cur, d) = cur, nxt[0]
                    length += d
                    chain.append((cur, length))
                seen.add((chain[-1][0], chain[-2][0] if len(chain) > 1 else start))
                o1 = off[start]
                end = chain[-1][0]
                o2 = off.get(end, lifted.get(end, 0.0))
                # Only a deck lifts a junction; a cut steepens its ramp instead, so the roads that leave it at the
                # ground (the Ring Road's slip roads up to Ako Adjei's roundabout) stay there.
                if end not in off and degree(end) >= 3 and o1 > EPS:
                    need = abs(o1) - MAX_GRADE * length
                    if need > EPS and abs(o2) < need:
                        lifted[end] = math.copysign(need, o1)
                        o2 = lifted[end]
                        changed = True
                if abs(o1) < EPS and abs(o2) < EPS:
                    continue
                for v, s in chain[:-1] if end in off else chain:
                    if v in held:
                        continue
                    if (o1 >= 0) == (o2 >= 0):
                        sg = 1.0 if (o1 + o2) >= 0 else -1.0
                        a, b = abs(o1), abs(o2)
                        g = max(GRADE, (a - b) / length if a > b else 0.0, (b - a) / length if b > a else 0.0)
                        h = sg * max(a - g * s, b - g * (length - s), 0.0)
                    else:
                        h = o1 + (o2 - o1) * s / length
                    if v not in off or abs(h) > abs(off[v]):
                        off[v] = h
                        if v not in held:
                            changed = True
        for v, h in lifted.items():
            if v not in off or abs(h) > abs(off[v]):
                off[v] = h
        if not changed:
            break

    # 3b. No step down from a deck steeper than MAX_GRADE: where two levels still meet within a few metres (a slip road off
    # Circle's top tier tagged a layer lower over its whole length, a short way between Ako Adjei's cut and the
    # ground), the higher level runs on down along the roads at MAX_GRADE, the held ones included (a slip road ramps
    # down from the tier it leaves). Levels of opposite signs (a cut meeting a ramp) are left to step 3's line.
    heap = [(-abs(h), k, v) for k, (v, h) in enumerate(off.items()) if h > EPS]
    tick = len(heap)
    heapq.heapify(heap)
    while heap:
        m, _, v = heapq.heappop(heap)
        m = -m
        if abs(abs(off.get(v, 0.0)) - m) > 1e-9:
            continue
        sg = 1.0 if off[v] > 0 else -1.0
        for u, d, _ in cadj.get(v, ()):
            want = m - MAX_GRADE * d
            o = off.get(u, 0.0)
            # A vertex held at the ground (a base-level bridge: Ako Adjei's roundabout) stays there.
            if u in held and abs(held[u]) < EPS:
                continue
            if want > EPS and want > abs(o) + 0.01 and (abs(o) < EPS or (o > 0) == (sg > 0)):
                off[u] = sg * want
                heapq.heappush(heap, (-want, tick, u))
                tick += 1

    # 3c. A cut and a ramp of opposite signs that still meet in a step (Nsawam Road out of Circle's underpass into the
    # connector up to a flyover): one even grade along the road between the held vertices on either side, through
    # the vertices between them, through a junction along the structure.
    def walk(v, came):
        path = [v]
        while v not in held:
            nxt = [u for u, _, _ in cadj.get(v, ()) if u != came and u not in path and off.get(u, 0.0) * off[v] > 0]
            if not nxt:
                break
            # Through a junction, along the structure (the level that runs on furthest from the ground).
            came, v = v, max(nxt, key=lambda u: abs(off[u]))
            path.append(v)
        return path

    for _ in range(3):
        steps = [(a, b) for a in list(off) for b, d, _ in cadj.get(a, ())
                 if off.get(a, 0.0) > EPS and off.get(b, 0.0) < -EPS and off[a] - off[b] > MAX_GRADE * d + 0.01]
        if not steps:
            break
        for a, b in steps:
            path = walk(a, b)[::-1] + walk(b, a)
            cum = [0.0]
            for k in range(len(path) - 1):
                cum.append(cum[-1] + math.dist(pos[path[k]], pos[path[k + 1]]))
            oa, ob = off.get(path[0], 0.0), off.get(path[-1], 0.0)
            for k in range(1, len(path) - 1):
                if path[k] not in held:
                    off[path[k]] = oa + (ob - oa) * cum[k] / (cum[-1] or 1)

    # 4. Pieces: runs along each way of structure vertices, ending on the ground.
    pieces = []
    for wid, vs in wverts.items():
        w = wby[wid]
        t = w["tags"]
        if wid not in carry:
            continue
        struct = [abs(off.get(v, 0.0)) > EPS or wid in flyover or wid in tunnels for v in vs]
        i = 0
        while i < len(vs):
            if not struct[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(vs) and struct[j + 1]:
                j += 1
            a, b = max(0, i - 1), min(len(vs) - 1, j + 1)
            run = vs[a:b + 1]
            if len(run) >= 2:
                kinds = "".join(
                    "D" if wid in flyover else "T" if wid in tunnels else
                    "F" if off.get(v, 0.0) > EPS else "C" if off.get(v, 0.0) < -EPS else "G" for v in run)
                s = site_of(*pos[run[len(run) // 2]], sites)
                pieces.append({
                    "id": f"{wid}/{i}", "osm": wid, "cls": t["highway"], "name": t.get("name", t.get("ref", "")), "w": width(t),
                    **({"lanes": int(t["lanes"])} if t.get("lanes", "").isdigit() else {}),
                    **({"oneway": True} if t.get("oneway") in ("yes", "1", "true") or t["highway"] == "motorway"
                       or t["highway"].endswith("_link") else {}),
                    "site": s["name"] if s else "", "style": s["style"] if s else "flyover",
                    "pts": [[round(pos[v][0], 2), round(pos[v][1], 2)] for v in run],
                    "off": [round(off.get(v, 0.0), 2) for v in run], "kind": kinds})
            i = j + 1

    # Footbridges over main roads: a deck at FOOT_M over the road, stairs at its ends (the game draws them).
    main = [i for i, w in enumerate(drive) if w["tags"]["highway"] in MAIN]
    feet = []
    for w in ways:
        t = w["tags"]
        if t["highway"] not in FOOT or not is_bridge(t):
            continue
        under = [drive[j]["tags"].get("name", "") for j in tree.query(w["line"]) if j in set(main)
                 and lines[j].crosses(w["line"])]
        if not under:
            continue
        s = site_of(*w["line"].interpolate(0.5, normalized=True).coords[0], sites)
        if s and s["base"] > 0:
            continue    # a sidewalk on a base-level deck (Ako Adjei's roundabout): the deck carries it
        feet.append({"id": f"fb{w['id']}", "osm": w["id"], "over": sorted(set(under)), "w": 2.6,
                     "pts": [[round(x, 2), round(y, 2)] for x, y in w["xy"]], "h": FOOT_M})
    return pieces, feet, sorted(flyover), sorted(tunnels)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", default="data/osm/ghana.osm.pbf")
    ap.add_argument("--out", default="data/work/elevated.json")
    args = ap.parse_args()
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    ways = osm_ways(args.pbf)
    pieces, feet, flyover, tunnels = build(ways, to_utm)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"crs": "EPSG:32630", "level_m": LEVEL_M, "sites": SITES, "pieces": pieces, "footbridges": feet,
                   "credits": "Interchanges, flyovers and footbridges: OpenStreetMap contributors (ODbL)."}, f)
    km = sum(LineString(p["pts"]).length for p in pieces) / 1000
    print(f"elevated: {len(pieces)} pieces ({km:.1f} km) from {len(flyover)} flyover ways and {len(tunnels)} tunnel "
          f"ways, {len(feet)} footbridges")


if __name__ == "__main__":
    main()
