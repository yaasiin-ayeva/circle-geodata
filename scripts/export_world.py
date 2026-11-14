"""Write the world the game streams: areas built by build_district.py that join along their edges, in one frame.

    python3 scripts/export_world.py --name osu data/work/web/osu-scene.json data/work/web/ring-road-scene.json \
        --out data/work/web/osu-world.json

The first scene is home: the game loads its district (and its dressed block) before the first drive, as it loads a
single district. The others are streamed by distance from the car, tile by tile when their scene lists tiles
(export_scene.py --tiles), else as one GLB. Their scene data (street lights, roads, solid outlines, parked cars,
laps) is small and is merged at load. Each area carries its offset from home's origin in glTF x and z metres (x
east, z south), from the scenes' WGS84 origins in UTM 30N, so areas built from different origins still line up;
the Ring Road shares Osu's origin, so its offset is zero.

The game asks for <name>-world.json when it starts on that district (?district=osu, the default), and falls back to
the district alone when the file is missing.
"""
import argparse
import json
import os

from pyproj import Transformer


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenes", nargs="+", help="scene JSON files from export_scene.py, home first")
    ap.add_argument("--name", required=True, help="the world's name: the district the game starts on")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    areas, home = [], None
    for path in args.scenes:
        s = json.load(open(path))
        o = s["origin_wgs84"]
        e, n = to_utm.transform(o["lon"], o["lat"])
        if home is None:
            home = (e, n)
        name = s.get("name") or os.path.splitext(os.path.basename(s["files"]["district"]))[0]
        tiles = s["files"].get("tiles")
        folder = os.path.dirname(path)
        files = [t["file"] for t in tiles] if tiles else [s["files"]["district"]] + ([s["files"]["block"]] if s["files"].get("block") else [])
        packed = [os.path.join(folder, os.path.splitext(f)[0] + ".pack.glb") for f in files]
        area = {"name": name, "scene": os.path.basename(path),
                # glTF axes: x east, z south.
                "offset": [round(e - home[0], 2) + 0.0, round(home[1] - n, 2) + 0.0],
                "bounds_m": s["bounds_m"], "lap_m": round(s["lap"]["length_m"], 1), "lap_via": s["lap"].get("via"),
                "tiles": len(tiles) if tiles else 0,
                "scene_bytes": os.path.getsize(path),
                "packed_bytes": sum(os.path.getsize(p) for p in packed if os.path.exists(p))}
        missing = [p for p in packed if not os.path.exists(p)]
        if missing:
            print(f"{name}: {len(missing)} packed GLB missing next to the scene (first: {missing[0]})")
        areas.append(area)
    world = {"name": args.name,
             "axes": "glTF: x east, y up, z south, metres from the home area's origin",
             "origin_wgs84": json.load(open(args.scenes[0]))["origin_wgs84"],
             "home": areas[0]["name"],
             "areas": areas}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(world, f, indent=1)
        f.write("\n")
    for a in areas:
        print(f"{a['name']}: offset {a['offset']}, lap {a['lap_m']:.0f} m, "
              f"{a['tiles'] or 'one'} {'tiles' if a['tiles'] else 'file'}, {a['packed_bytes'] / 1048576:.2f} MB packed, "
              f"scene {a['scene_bytes'] / 1024:.0f} KB")


if __name__ == "__main__":
    main()
