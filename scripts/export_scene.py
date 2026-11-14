"""Write the engine-neutral scene description that goes with the GLB files of export_glb.py.

    python3 scripts/export_scene.py --district data/work/osu-poc/osu-poc.json \
        --lookdev data/work/osu-lookdev/osu-lookdev.json --out data/work/web/osu-scene.json

Everything is in glTF axes (x = east, y = up, z = south), metres from the district origin, so the web
engines read it without knowing the builders' conventions. Headings are unit vectors on the ground, not
yaw angles, because engines disagree on yaw. Light settings live in look/look.json (lightKinds),
keyed by each light's "kind".

Contents: street lights (pole foot, lamp head, light position, kind, road), look-dev lights (shops,
kiosks, bulbs, billboard floods, facade uplights with their targets), the benchmark lap and its spawn,
parked cars (the look-dev block's or, without --lookdev, those build_district.py parked along the streets)
and generated-model placements.
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
    args = ap.parse_args()
    glb = args.glb or os.path.splitext(os.path.basename(args.district))[0] + ".glb"

    d = json.load(open(args.district))
    scene = {
        "axes": "glTF: x east, y up, z south, metres from the district origin",
        "origin_wgs84": d.get("origin_wgs84"),
        "bounds_m": {"xmin": d["bounds_m"]["xmin"], "xmax": d["bounds_m"]["xmax"],
                     "zmin": -d["bounds_m"]["zmax"], "zmax": -d["bounds_m"]["zmin"]},
        "files": {"district": glb, "look": "look.json"},
        "credits": d.get("credits", ""),
        "streetLights": street_lights(d["lights"]),
    }
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
    json.dump(scene, open(args.out, "w"), indent=1)
    n_lamps = sum(1 for s in scene["streetLights"] if "light" in s)
    print(f"{os.path.basename(args.out)}: {len(scene['streetLights'])} street lights ({n_lamps} lit), "
          f"{len(scene.get('blockLights', []))} block lights, lap {scene['lap']['length_m']:.0f} m "
          f"({len(scene['lap']['points'])} points), {len(scene.get('parked', []))} parked cars, {len(scene.get('models', []))} models")


if __name__ == "__main__":
    main()
