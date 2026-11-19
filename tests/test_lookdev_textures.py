"""Checks the look-dev block's textures for the game (scripts/export_glb.py --ktx2 --bake-tiling) and its shop signs
on the fascias (scripts/build_lookdev.py shopfront), on a synthetic OBJ and a stand-in encoder.

    python3 tests/test_lookdev_textures.py
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import build_lookdev as bl  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


def read_glb(path):
    b = open(path, "rb").read()
    jl = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + jl]), b[20 + jl + 8:]


def uv_of(j, bin_, prim):
    a = j["accessors"][prim["attributes"]["TEXCOORD_0"]]
    v = j["bufferViews"][a["bufferView"]]
    return np.frombuffer(bin_, np.float32, a["count"] * 2, v.get("byteOffset", 0)).reshape(-1, 2)


tmp = tempfile.mkdtemp()
# Two materials over one tiled set (tiling 1 and 2), one over an atlas with lettering, one over another atlas.
os.makedirs(os.path.join(tmp, "tex", "ambientCG", "Concrete036"))
Image.new("RGB", (64, 64), (120, 110, 100)).save(os.path.join(tmp, "tex", "ambientCG", "Concrete036", "Concrete036_1K-JPG_Color.jpg"))
Image.new("RGB", (128, 64), (200, 30, 30)).save(os.path.join(tmp, "signs-atlas.png"))
Image.new("RGB", (64, 64), (30, 30, 200)).save(os.path.join(tmp, "interiors-atlas.png"))
look = {"materials": {
    "M_Gutter": {"texture": "Concrete036", "tiling": 1.0},
    "M_Trunk": {"texture": "Concrete036", "tiling": 2.0},
    "M_Sign_Painted": {"map": "signs-atlas.png"},
    "M_WindowLit_Warm": {"map": "interiors-atlas.png"},
}}
json.dump(look, open(os.path.join(tmp, "look.json"), "w"))
lines = []
for k, mtl in enumerate(look["materials"]):
    x = k * 2
    lines += [f"v {x} 0 0", f"v {x + 1} 0 0", f"v {x + 1} 1 0", "vt 0.25 0.5", "vt 0.75 0.5", "vt 0.75 1"]
for k, mtl in enumerate(look["materials"]):
    i = 3 * k + 1
    lines += [f"o LookDev_Test_{k}", f"usemtl {mtl}", f"f {i}/{i} {i + 1}/{i + 1} {i + 2}/{i + 2}"]
open(os.path.join(tmp, "block.obj"), "w").write("\n".join(lines) + "\n")
# A stand-in for circle-game's scripts/ktx2_encode.mjs: a KTX2 signature, then the mode, the wrap and the size.
encoder = os.path.join(tmp, "encoder.py")
open(encoder, "w").write(
    "import sys\n"
    "a = sys.argv[1:]\n"
    "o = {a[i][2:]: a[i + 1] for i in range(0, 6, 2)}\n"
    "n = len(open(a[6], 'rb').read())\n"
    "open(a[7], 'wb').write(b'\\xabKTX 20\\xbb\\r\\n\\x1a\\n' + f\"{o['mode']} {o['wrap']} {o['size']} {n}\".encode())\n")
run = [sys.executable, os.path.join(SCRIPTS, "export_glb.py"), os.path.join(tmp, "block.obj"), "--look", os.path.join(tmp, "look.json"),
       "--textures", os.path.join(tmp, "tex")]
subprocess.run(run + ["--out", os.path.join(tmp, "old.glb")], check=True, stdout=subprocess.DEVNULL)
subprocess.run(run + ["--bake-tiling", "--ktx2", f"{sys.executable} {encoder}", "--out", os.path.join(tmp, "new.glb")],
               check=True, stdout=subprocess.DEVNULL)
old, old_bin = read_glb(os.path.join(tmp, "old.glb"))
new, new_bin = read_glb(os.path.join(tmp, "new.glb"))

# Without the options, the pack as before: JPEG images, the tiling in KHR_texture_transform.
check("by default the images stay JPEG", {i["mimeType"] for i in old["images"]} == {"image/jpeg"})
check("by default the tiling is a KHR_texture_transform", "KHR_texture_transform" in old.get("extensionsUsed", []))

mats = {m["name"]: m for m in new["materials"]}
tex_of = lambda name: mats[name]["pbrMetallicRoughness"]["baseColorTexture"]
check("no KHR_texture_transform left", "KHR_texture_transform" not in json.dumps(new))
check("the two materials over one image share one texture", tex_of("M_Gutter")["index"] == tex_of("M_Trunk")["index"])
check("one texture per image", len(new["textures"]) == len(new["images"]) == 3, f"{len(new['textures'])} textures")
check("every image is KTX2", {i["mimeType"] for i in new["images"]} == {"image/ktx2"})
check("every texture names its image through KHR_texture_basisu",
      all("source" not in t and "source" in t["extensions"]["KHR_texture_basisu"] for t in new["textures"]))
check("KHR_texture_basisu is used and required",
      "KHR_texture_basisu" in new["extensionsUsed"] and "KHR_texture_basisu" in new["extensionsRequired"])


def image_note(tex_index):
    im = new["images"][new["textures"][tex_index]["extensions"]["KHR_texture_basisu"]["source"]]
    v = new["bufferViews"][im["bufferView"]]
    return new_bin[v["byteOffset"] + 12:v["byteOffset"] + v["byteLength"]].decode().split()


sign, room, concrete = image_note(tex_of("M_Sign_Painted")["index"]), image_note(tex_of("M_WindowLit_Warm")["index"]), image_note(tex_of("M_Gutter")["index"])
check("the atlas with lettering in UASTC, clamped", sign[:3] == ["uastc", "clamp", "128x64"], " ".join(sign))
check("the other atlas in ETC1S, clamped", room[:2] == ["etc1s", "clamp"], " ".join(room))
check("a tiled set in ETC1S, wrapped", concrete[:2] == ["etc1s", "repeat"], " ".join(concrete))
check("the encoder gets the RGBA pixels themselves", sign[3] == str(128 * 64 * 4), sign[3])

uv = {}
for j, b, key in ((old, old_bin, "old"), (new, new_bin, "new")):
    names = [m["name"] for m in j["materials"]]
    for mesh in j["meshes"]:
        for p in mesh["primitives"]:
            uv[(key, names[p["material"]])] = uv_of(j, b, p)
check("a tiling of 2 is baked into the coordinates", np.allclose(uv[("new", "M_Trunk")], uv[("old", "M_Trunk")] * 2))
check("a tiling of 1 leaves them as they were", np.array_equal(uv[("new", "M_Gutter")], uv[("old", "M_Gutter")]))
check("an atlas's coordinates are left alone", np.array_equal(uv[("new", "M_Sign_Painted")], uv[("old", "M_Sign_Painted")]))

# The shop signs: on a fascia the board stands proud of the band's face, fills it and goes to LookDev_Signs_*; without
# one it stays 0.18 m out from the wall, in the building's object.
quads = []


class Recorder:
    def quad(self, obj, mat, a, b, c, d, uv=None):
        quads.append((obj, mat, (a, b, c, d)))

    def count(self, key, n=1):
        pass


for fascia in (True, False):
    quads.clear()
    # A wall along x, its outward normal -y: the depth out from the wall is -y.
    bl.shopfront(Recorder(), "LookDev_Buildings_0_0", "M_Facade_White", "b1", np.array([0.0, 0.0]), np.array([1.0, 0.0]),
                 np.array([0.0, -1.0]), 0.0, 3.6, 0.0, bl.STOREY, 0.9, (0, 0), fascia)
    board = [q for q in quads if q[1].startswith("M_Sign_")]
    edges = [q for q in quads if q[1] == "M_SignFrame"]
    rest = [q for q in quads if q not in board + edges]
    depth = -board[0][2][0][1]
    zs = [p[2] for p in board[0][2]]
    if fascia:
        check("on a fascia the board stands in front of the band's face", depth > bl.FASCIA_DEPTH, f"{depth:.2f} m")
        check("on a fascia the board fills the band", min(zs) >= bl.FASCIA_Z[0] and max(zs) <= bl.FASCIA_Z[1]
              and max(zs) - min(zs) >= 0.75, f"{min(zs):.2f} to {max(zs):.2f} m")
        check("on a fascia the board and its edges are LookDev_Signs_*",
              all(q[0] == "LookDev_Signs_0_0" for q in board + edges), str({q[0] for q in board + edges}))
        check("the shopfront's other parts stay in the building's object", rest and all(q[0] == "LookDev_Buildings_0_0" for q in rest),
              str({(q[0], q[1]) for q in rest if q[0] != "LookDev_Buildings_0_0"}))
        check("on a fascia the edges start at the band's face",
              all(min(-p[1] for p in q[2]) >= bl.FASCIA_DEPTH - 1e-9 for q in edges))
    else:
        check("without a fascia the board stays 0.18 m out", abs(depth - 0.18) < 1e-9, f"{depth:.2f} m")
        check("without a fascia it stays in the building's object", all(q[0] == "LookDev_Buildings_0_0" for q in board + edges))

print("all passed" if not failures else f"{failures} failed")
sys.exit(1 if failures else 0)
