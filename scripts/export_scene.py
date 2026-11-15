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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True)
    ap.add_argument("--lookdev")
    ap.add_argument("--out", required=True)
    ap.add_argument("--glb", help="the district GLB the scene goes with (default: named after --district, "
                                  "osu-poc.json -> osu-poc.glb)")
    ap.add_argument("--tiles", help="the tiles.json export_glb.py --tile-m wrote: the district is streamed in those "
                                    "tiles rather than loaded as one GLB")
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
    # The drivable network, for traffic: polylines in glTF axes with their class, width and name, and "oneway" on the
    # pieces driven one way only, which run the way the traffic goes.
    if d.get("roads"):
        scene["roads"] = [{"cls": r["cls"], "w": r["w"], "name": r["name"], **({"oneway": True} if r.get("oneway") else {}),
                           "pts": [[r["pts"][i], round(-r["pts"][i + 1], 1)] for i in range(0, len(r["pts"]), 2)]}
                          for r in d["roads"]]
    # The named places for the maps (scripts/places.py), best first: name, icon category, a point (glTF axes), rank.
    if d.get("places"):
        scene["places"] = [{"name": p["name"], "cat": p["cat"], "at": [p["x"], round(-p["z"], 1) + 0.0], "rank": p["rank"],
                            **({"brand": True} if p.get("brand") else {})} for p in d["places"]]
    if d.get("solid"):
        # Flat lists x0, z0, x1, z1, ... with z flipped to glTF's south.
        flip = lambda flat: [v if i % 2 == 0 else round(-v, 1) + 0.0 for i, v in enumerate(flat)]
        scene["solid"] = {k: [flip(f) for f in d["solid"].get(k, [])] for k in ("buildings", "walls")}
    # Hiding places for the heat chase: the plot's centre, the way its open front faces, its width and depth.
    if d.get("hiding"):
        scene["hiding"] = [{"kind": h["kind"], "position": g(h["x"], 0.0, h["z"]), "forward": heading(h["yaw"]),
                            "w": h["w"], "d": h["d"]} for h in d["hiding"]]
    if args.lookdev:
        lk = json.load(open(args.lookdev))
        scene["files"]["block"] = "osu-lookdev.glb"
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
    # The road network, the solid outlines and the places are long lists for the game, not for reading: one line
    # each, the rest of the scene indented.
    compact = {k: scene.pop(k) for k in ("roads", "solid", "places") if k in scene}
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
