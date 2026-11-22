"""Write the engine-neutral scene description that goes with the GLB files of export_glb.py.

    python3 scripts/export_scene.py --district data/work/osu-poc/osu-poc.json \
        --lookdev data/work/osu-lookdev/osu-lookdev.json --out data/work/web/osu-scene.json

Everything is in glTF axes (x = east, y = up, z = south), metres from the district origin, so the web
engines read it without knowing the builders' conventions. Headings are unit vectors on the ground, not
yaw angles, because engines disagree on yaw. Light settings live in look/look.json (lightKinds),
keyed by each light's "kind".

Contents: street lights (pole foot, lamp head, light position, kind, road), look-dev lights (shops,
kiosks, bulbs, billboard floods, facade uplights with their targets), the benchmark lap and its spawn,
parked cars (the look-dev block's or, without --lookdev, those build_district.py parked along the streets),
generated-model placements, the road network and the solid outlines; with --tiles, the district's GLB tiles
for streaming (export_glb.py --tile-m).
"""
import argparse
import json
import math
import os


def g(x, y, z):
    """Builder axes (x east, y up, z north) to glTF axes (x east, y up, z south)."""
    return [round(x, 3), round(y, 3), round(-z, 3)]


HEAD_M, ARM_M = 10.0, 1.75


def heading(yaw_deg):
    """A Unity yaw (degrees clockwise from north, seen from above) as a glTF ground direction."""
    a = math.radians(yaw_deg)
    return [round(math.sin(a), 5), 0.0, round(-math.cos(a), 5)]


def parked_cars(cars):
    """Parked cars (build_lookdev's or build_district's "parked"): paint, position, heading."""
    return [{"colour": c["colour"], "position": g(c["x"], c["y"], c["z"]), "forward": heading(c["yaw"])} for c in cars]


def street_lights(lamps):
    out = []
    for l in lamps:
        f = heading(l["yaw"])
        foot = g(l["x"], l.get("y", 0.0), l["z"])
        # Heads at 10 m on a 1.75 m arm (circle-reference/catalogue/street-survey.md, measured on Oxford Street).
        head = [round(foot[0] + f[0] * ARM_M, 3), round(foot[1] + HEAD_M, 3), round(foot[2] + f[2] * ARM_M, 3)]
        entry = {"kind": l["kind"], "road": l.get("road", ""), "foot": foot, "forward": f, "head": head}
        if l.get("pole"):
            entry["pole"] = l["pole"]  # "solar": a panel on top of the pole (build_district.SOLAR_ROADS)
        if l["kind"] != "dead":
            entry["light"] = [head[0], round(foot[1] + HEAD_M - 0.13, 3), head[2]]
            entry["direction"] = [0.0, -1.0, 0.0]
        out.append(entry)
    return out


CHUNKED = ("streetLights", "parked", "solid", "hiding")


def write_chunks(scene, folder):
    """Moves the scene's heavy lists into one <tile>.data.json per tile (next to the tile's GLB), each item to the tile
    whose ground box holds its anchor (a lamp's foot, a car's or a hiding place's position, an outline's first point;
    the nearest tile when none does), and names the file in the tile's entry ("data"). Leaves empty lists in the
    scene, so a game that knows nothing of chunks still reads it."""
    tiles = scene["files"]["tiles"]
    boxes = [t["bounds"] for t in tiles]

    def tile_of(x, z):
        best, bd = 0, float("inf")
        for i, (x0, z0, x1, z1) in enumerate(boxes):
            dx = max(x0 - x, 0.0, x - x1)
            dz = max(z0 - z, 0.0, z - z1)
            d = dx * dx + dz * dz
            if d == 0.0:
                return i
            if d < bd:
                best, bd = i, d
        return best

    chunks = [{"streetLights": [], "parked": [], "solid": {"buildings": [], "walls": [], "gates": []}, "hiding": []} for _ in tiles]
    for l in scene.get("streetLights", []):
        chunks[tile_of(l["foot"][0], l["foot"][2])]["streetLights"].append(l)
    for c in scene.get("parked", []):
        chunks[tile_of(c["position"][0], c["position"][2])]["parked"].append(c)
    for h in scene.get("hiding", []):
        chunks[tile_of(h["position"][0], h["position"][2])]["hiding"].append(h)
    for k in ("buildings", "walls", "gates"):
        for flat in scene.get("solid", {}).get(k, []):
            chunks[tile_of(flat[0], flat[1])]["solid"][k].append(flat)
    for t, c in zip(tiles, chunks):
        t["data"] = os.path.splitext(t["file"])[0] + ".data.json"
        with open(os.path.join(folder, t["data"]), "w") as f:
            json.dump(c, f, separators=(",", ":"))
    scene["streetLights"] = []
    for k in ("parked", "solid", "hiding"):
        scene.pop(k, None)
    scene["chunked"] = list(CHUNKED)
    total = sum(os.path.getsize(os.path.join(folder, t["data"])) for t in tiles)
    print(f"chunks: {len(tiles)} tile data files, {total / 1048576:.2f} MB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True)
    ap.add_argument("--lookdev")
    ap.add_argument("--block-ktx2", metavar="GLB",
                    help="with --lookdev, the block's GLB with KTX2 textures (export_glb.py --ktx2 --bake-tiling), "
                         "named in files.blockKtx2: the game loads it in place of files.block")
    ap.add_argument("--out", required=True)
    ap.add_argument("--glb", help="the district GLB the scene goes with (default: named after --district, "
                                  "osu-poc.json -> osu-poc.glb)")
    ap.add_argument("--tiles", help="the tiles.json export_glb.py --tile-m wrote: the district is streamed in those "
                                    "tiles rather than loaded as one GLB")
    ap.add_argument("--chunk", action="store_true",
                    help="with --tiles: the heavy lists (street lights, parked cars, solid outlines, hiding places) go "
                         "to one <tile>.data.json per tile, which the game fetches with the tile; the scene keeps what "
                         "the whole world needs from the start (roads, places, garages, fuel stations, chargers, medians, the lap)")
    args = ap.parse_args()
    glb = args.glb or os.path.splitext(os.path.basename(args.district))[0] + ".glb"

    d = json.load(open(args.district))
    scene = {
        "name": d.get("name", os.path.splitext(os.path.basename(args.district))[0]),
        "axes": "glTF: x east, y up, z south, metres from the district origin",
        "origin_wgs84": d.get("origin_wgs84"),
        "bounds_m": {"xmin": d["bounds_m"]["xmin"], "xmax": d["bounds_m"]["xmax"],
                     "zmin": -d["bounds_m"]["zmax"], "zmax": -d["bounds_m"]["zmin"]},
        "files": {"district": glb, "look": "look.json"},
        "credits": d.get("credits", ""),
        "streetLights": street_lights(d["lights"]),
    }
    if args.tiles:
        # The district in tiles for the game to load by distance: each tile's GLB (the game loads <file>.pack.glb),
        # its ground bounds in glTF x and z, triangles and sizes. The paths are relative to the scene's folder.
        t = json.load(open(args.tiles))
        scene["files"]["tiles"] = [{k: tile[k] for k in ("file", "bounds", "height_m", "triangles", "primitives", "bytes", "packed_bytes")
                                    if k in tile} for tile in t["tiles"]]
    lap = d["lap"]
    path = lap["path"]
    scene["lap"] = {"length_m": lap["length_m"], "via": lap.get("via"),
                    "points": [[round(path[i], 2), round(-path[i + 1], 2)] for i in range(0, len(path), 2)],
                    "spawn": {"position": g(lap["spawn"]["x"], lap["spawn"].get("y", 0.0), lap["spawn"]["z"]),
                              "forward": heading(lap["spawn"]["yaw"])}}
    # Real signs at their places (build_district DISTRICT_RULES "signs"): a point on the street and the direction of
    # the facade that carries the sign, on the ground; the game casts from one along the other.
    if d.get("signs"):
        scene["signs"] = [{"name": sg["name"], "from": [sg["x"], -sg["z"]],
                           "toward": [round(math.sin(math.radians(sg["bearing"])), 4), round(-math.cos(math.radians(sg["bearing"])), 4)]}
                          for sg in d["signs"]]
    # The real names of the typologies' buildings (build_district.py "names": churches, mosques, storey buildings'
    # bands): the footprint's middle and the plan direction to the road its named facade faces, in glTF axes.
    if d.get("names"):
        scene["names"] = [{"name": nm["name"], "kind": nm["kind"], "at": [nm["x"], round(-nm["z"], 1) + 0.0],
                           "toward": [nm["nx"], round(-nm["nz"], 3) + 0.0]} for nm in d["names"]]
    # The drivable network, for traffic: polylines in glTF axes with their class, width and name, and "oneway" on the
    # pieces driven one way only, which run the way the traffic goes; "lanes" where OSM counts them (build_district.py
    # LANES: each way's count on a carriageway, the total on a two-way road).
    if d.get("roads"):
        scene["roads"] = [{"cls": r["cls"], "w": r["w"], "name": r["name"], **({"oneway": True} if r.get("oneway") else {}),
                           **({"lanes": r["lanes"]} if r.get("lanes") else {}),
                           "pts": [[r["pts"][i], round(-r["pts"][i + 1], 1)] for i in range(0, len(r["pts"]), 2)]}
                          for r in d["roads"]]
    # The named places for the maps (scripts/places.py), best first: name, icon category, a point (glTF axes), rank.
    if d.get("places"):
        scene["places"] = [{"name": p["name"], "cat": p["cat"], "at": [p["x"], round(-p["z"], 1) + 0.0], "rank": p["rank"],
                            **({"brand": True} if p.get("brand") else {}),
                            **({"person": True} if p.get("person") else {})} for p in d["places"]]
    # Real garages for the game's fitters (build_district find_garages): the garage's point and the door on the road's
    # edge in glTF axes, "facing" the yaw (atan2(x, z), three.js rotation.y) from the garage to the door, the road
    # there (name, width, direction along it), the outline when OpenStreetMap maps the building.
    if d.get("garages"):
        flip2 = lambda v: [v[0], round(-v[1], 4) + 0.0]
        scene["garages"] = [{"name": gg["name"], "kind": gg["kind"], "at": [gg["x"], round(-gg["z"], 1) + 0.0],
                             "door": flip2(gg["door"]), "facing": round(math.atan2(gg["toward"][0], -gg["toward"][1]), 4),
                             "road": {"name": gg["road"]["name"], "w": gg["road"]["w"], "dir": flip2(gg["road"]["dir"])},
                             **({"outline": [[x, round(-z, 1) + 0.0] for x, z in gg["outline"]]} if gg.get("outline") else {}),
                             **({"osm": gg["osm"]} if gg.get("osm") else {"source": gg.get("source", "overture")}),
                             **({"brand": True} if gg.get("brand") else {}),
                             **({"person": True} if gg.get("person") else {})} for gg in d["garages"]]
    # The fuel stations (scripts/fuel_stations.py) for the game's forecourts (circle-game src/world/fuel.ts): the
    # station's point, the door on the nearest street's asphalt edge, "facing" the yaw from the station to the door, the
    # street (name, width, direction along it), the brand and name as tagged, and the site, shop and canopy outlines
    # when OpenStreetMap maps them, all in glTF axes.
    if d.get("fuelStations"):
        flip2 = lambda v: [v[0], round(-v[1], 4) + 0.0]
        ring = lambda r: [[x, round(-z, 1) + 0.0] for x, z in r]
        out = []
        for f in d["fuelStations"]:
            e = {"name": f["name"], **({"brand": f["brand"]} if f.get("brand") else {}),
                 **({"operator": f["operator"]} if f.get("operator") else {}), "at": [f["x"], round(-f["z"], 1) + 0.0]}
            if f.get("door"):
                e.update({"door": flip2(f["door"]), "facing": round(math.atan2(f["toward"][0], -f["toward"][1]), 4),
                          "road": {"name": f["road"]["name"], "w": f["road"]["w"], "dir": flip2(f["road"]["dir"])}})
            for k in ("site", "shop"):
                if f.get(k):
                    e[k] = ring(f[k])
            if f.get("canopies"):
                e["canopies"] = [ring(r) for r in f["canopies"]]
            e.update({"osm": f["osm"]} if f.get("osm") else {"source": f.get("source", "overture")})
            if f.get("person"):
                e["person"] = True
            out.append(e)
        scene["fuelStations"] = out
    # The electric-vehicle chargers (scripts/ev_chargers.py) for the game's bays (circle-game src/world/ev.ts): the
    # charger's point, the door on the nearest street's asphalt edge, "facing" the yaw from the charger to the door, the
    # street, the operator and name as tagged, the plugs, the site when mapped (or its car park's), and the fuel station
    # it stands in (its point, to find it among the scene's fuelStations), all in glTF axes.
    if d.get("evChargers"):
        flip2 = lambda v: [v[0], round(-v[1], 4) + 0.0]
        out = []
        for c in d["evChargers"]:
            e = {"name": c["name"], "at": [c["x"], round(-c["z"], 1) + 0.0],
                 **{k: c[k] for k in ("operator", "brand", "network", "capacity", "sockets", "kw", "kind", "fee", "host") if c.get(k)}}
            if c.get("door"):
                e.update({"door": flip2(c["door"]), "facing": round(math.atan2(c["toward"][0], -c["toward"][1]), 4),
                          "road": {"name": c["road"]["name"], "w": c["road"]["w"], "dir": flip2(c["road"]["dir"])}})
            if c.get("site"):
                e["site"] = [[x, round(-z, 1) + 0.0] for x, z in c["site"]]
            if c.get("fuel"):
                f = c["fuel"]
                e["fuel"] = {"at": [f["x"], round(-f["z"], 1) + 0.0], **{k: f[k] for k in ("osm", "source", "brand") if f.get(k)}}
            e.update({"osm": c["osm"]} if c.get("osm") else {"source": c.get("source", "overture")})
            if c.get("sources"):
                e["sources"] = c["sources"]
            if c.get("person"):
                e["person"] = True
            out.append(e)
        scene["evChargers"] = out
    # The central reservations of the dual carriageways: the median's middle line (glTF axes), its width between
    # the two carriageways' asphalt, its kerb height, already cut where roads cross it (build_district find_medians).
    if d.get("medians"):
        scene["medians"] = [{"pts": [[x, round(-z, 1) + 0.0] for x, z in m["pts"]], "w": m["w"], "h": m["h"],
                             **({"name": m["name"]} if m.get("name") else {})} for m in d["medians"]]
    # The interchanges' and flyovers' pieces in the area (scripts/elevated.py ids), for relief.py.
    if d.get("structures"):
        scene["structures"] = d["structures"]
    if d.get("solid"):
        # Flat lists x0, z0, x1, z1, ... with z flipped to glTF's south.
        flip = lambda flat: [v if i % 2 == 0 else round(-v, 1) + 0.0 for i, v in enumerate(flat)]
        scene["solid"] = {k: [flip(f) for f in d["solid"].get(k, [])] for k in ("buildings", "walls")}
        # The compound walls' gates, [x, z] each, when the district has any (build_district solid_outlines).
        if d["solid"].get("gates"):
            scene["solid"]["gates"] = [flip(f) for f in d["solid"]["gates"]]
    # Hiding places for the heat chase: the plot's centre, the way its open front faces, its width and depth.
    if d.get("hiding"):
        scene["hiding"] = [{"kind": h["kind"], "position": g(h["x"], 0.0, h["z"]), "forward": heading(h["yaw"]),
                            "w": h["w"], "d": h["d"]} for h in d["hiding"]]
    if args.lookdev:
        lk = json.load(open(args.lookdev))
        scene["files"]["block"] = "osu-lookdev.glb"
        if args.block_ktx2:
            scene["files"]["blockKtx2"] = os.path.basename(args.block_ktx2)
        lights = []
        for l in lk["lights"]:
            e = {"kind": l["kind"], "position": g(l["x"], l["y"], l["z"])}
            if "tx" in l:
                e["target"] = g(l["tx"], l["ty"], l["tz"])
            lights.append(e)
        scene["blockLights"] = lights
        scene["parked"] = parked_cars(lk.get("parked", []))
        scene["models"] = [{"kind": m["kind"], "position": g(m["x"], m["y"], m["z"]), "forward": heading(m["yaw"]),
                            "scale": m.get("scale", 1.0)} for m in lk.get("models", [])]
        scene["blockOutline"] = [[x, -z] for x, z in lk.get("outline", [])]
    elif d.get("parked"):
        # No dressed block (Jamestown): the cars build_district.py parked along the streets.
        scene["parked"] = parked_cars(d["parked"])
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if args.chunk:
        if not args.tiles:
            ap.error("--chunk needs --tiles")
        write_chunks(scene, os.path.dirname(args.out))
    # The road network, the solid outlines and the places are long lists for the game, not for reading: one line
    # each, the rest of the scene indented.
    compact = {k: scene.pop(k) for k in ("roads", "solid", "places", "garages", "fuelStations", "evChargers", "medians", "structures") if k in scene}
    text = json.dumps(scene, indent=1)
    for k, v in compact.items():
        text = text[:text.rstrip().rfind("}")].rstrip() + f',\n "{k}": {json.dumps(v, separators=(",", ":"))}\n}}'
        scene[k] = v
    with open(args.out, "w") as f:
        f.write(text + "\n")
    n_lamps = sum(1 for s in scene["streetLights"] if "light" in s)
    print(f"{os.path.basename(args.out)}: {len(scene['streetLights'])} street lights ({n_lamps} lit), "
          f"{len(scene.get('blockLights', []))} block lights, lap {scene['lap']['length_m']:.0f} m "
          f"({len(scene['lap']['points'])} points), {len(scene.get('parked', []))} parked cars, {len(scene.get('models', []))} models")


if __name__ == "__main__":
    main()
