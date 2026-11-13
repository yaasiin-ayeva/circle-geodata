"""Applies the street survey's lighting rules to a scene already exported by export_scene.py, without rebuilding
the district (which needs the Overture slice). New builds get the same rules from build_district.place_lights.

The rules (circle-reference/catalogue/street-survey.md, Street View Aug 2024):
  - heads at 10 m on a 1.75 m arm;
  - Oxford Street lit from the east side only, poles 35 to 145 m apart (39, 142 and 84 m measured);
  - Ring Road East and Danquah Circle poles at least 38 m apart (about 40 m measured);
  - no sodium on the lap: the heads there are white LED.

    python3 scripts/relight_scene.py data/work/web/osu-scene.json [--out other.json]
"""
import argparse
import hashlib
import json
import math

HEAD_M, ARM_M = 10.0, 1.75
LAP_ROADS = ("Oxford Street", "Ring Road East", "Danquah Circle")
OXFORD_GAP_M = (35.0, 145.0)
RING_MIN_GAP_M = 38.0


def unit_hash(*parts):
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def reshape(lamp):
    """Moves the head and the light to the surveyed height and arm."""
    foot, f = lamp["foot"], lamp["forward"]
    lamp["head"] = [round(foot[0] + f[0] * ARM_M, 3), round(foot[1] + HEAD_M, 3), round(foot[2] + f[2] * ARM_M, 3)]
    if "light" in lamp:
        lamp["light"] = [lamp["head"][0], round(foot[1] + HEAD_M - 0.13, 3), lamp["head"][2]]
    return lamp


def thin(lamps, gap_for):
    """Keeps lamps in order along the road's main axis, each at least gap_for(k) after the last one kept."""
    if not lamps:
        return []
    xs = [l["foot"][0] for l in lamps]
    zs = [l["foot"][2] for l in lamps]
    along_z = (max(zs) - min(zs)) >= (max(xs) - min(xs))
    ordered = sorted(lamps, key=lambda l: l["foot"][2] if along_z else l["foot"][0])
    kept = [ordered[0]]
    for l in ordered[1:]:
        last = kept[-1]
        if math.dist((l["foot"][0], l["foot"][2]), (last["foot"][0], last["foot"][2])) >= gap_for(len(kept)):
            kept.append(l)
    return kept


def relight(scene):
    by_road = {}
    for lamp in scene["streetLights"]:
        by_road.setdefault(lamp.get("road", ""), []).append(lamp)
    out = []
    for road, lamps in by_road.items():
        if road == "Oxford Street":
            # East side only: the arm of a pole on the east kerb points west, over the road (glTF x is east).
            east = [l for l in lamps if l["forward"][0] < 0]
            lo, hi = OXFORD_GAP_M
            lamps = thin(east, lambda k: lo + (hi - lo) * unit_hash("oxford", "gap", k) ** 1.2)
        elif road in LAP_ROADS:
            lamps = thin(lamps, lambda k: RING_MIN_GAP_M)
        for k, lamp in enumerate(lamps):
            if road in LAP_ROADS and lamp["kind"] == "sodium":
                lamp["kind"] = "led_cool" if unit_hash(road, "kind", k) >= 0.8 else "led"
            out.append(reshape(lamp))
    scene["streetLights"] = out
    scene["streetLightRules"] = "circle-reference/catalogue/street-survey.md (13 Nov 2026)"
    return scene


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene")
    ap.add_argument("--out")
    args = ap.parse_args()
    scene = json.load(open(args.scene))
    before = len(scene["streetLights"])
    relight(scene)
    with open(args.out or args.scene, "w") as f:
        json.dump(scene, f, separators=(",", ":"))
    kinds = {}
    for l in scene["streetLights"]:
        kinds[l["kind"]] = kinds.get(l["kind"], 0) + 1
    print(f"{args.out or args.scene}: {before} -> {len(scene['streetLights'])} street lights, {kinds}")


if __name__ == "__main__":
    main()
