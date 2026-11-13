"""Turn the district and look-dev OBJ files into engine-neutral glTF binaries for the web engines.

    python3 scripts/export_glb.py data/work/osu-poc/osu-poc.obj --look look/look.json \
        --textures ../circle-game/Assets/_Circle/Art/Textures --out data/work/web/osu-poc.glb

The OBJ files from build_district.py and build_lookdev.py repeat every vertex of every face. This writes
each 250 m cell as one node with one indexed, welded primitive per material, and the materials as glTF PBR
from look.json (the values the Unity proof of concept used): base colour, roughness = 1 - smoothness,
metallic, emission with KHR_materials_emissive_strength above 1, texture tiling with KHR_texture_transform.
Atlases written by build_lookdev.py (signs, billboards, interiors) are embedded when they sit next to the
OBJ; tiled photo textures (ambientCG sets) are embedded from --textures when given, downscaled to
--max-texture. Compression (meshopt geometry, KTX2 textures) is a later pass: gltfpack.

Axes: the OBJ is X = west, Y = up, Z = north. glTF is written as x = east, y = up, z = south (a half turn
about Y, so face winding is kept). JSON positions from the builders (x east, z north) map to glTF as
(x, y, -z).
"""
import argparse
import io
import json
import math
import os
import struct
import sys

import numpy as np
from PIL import Image

ATLASES = ("signs-atlas.png", "billboards-atlas.png", "interiors-atlas.png")


def srgb_to_linear(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def read_obj(path):
    """Groups of (object, material) -> lists of corner indices into v / vt / vn."""
    v, vt, vn = [], [], []
    groups = {}
    order = []
    obj, mtl = "default", "default"
    with open(path) as f:
        for line in f:
            if line.startswith("v "):
                v.append(line.split()[1:4])
            elif line.startswith("vt "):
                vt.append(line.split()[1:3])
            elif line.startswith("vn "):
                vn.append(line.split()[1:4])
            elif line.startswith("f "):
                corners = [c.split("/") for c in line.split()[1:]]
                key = (obj, mtl)
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                for i in range(1, len(corners) - 1):   # fan, in case of quads
                    for c in (corners[0], corners[i], corners[i + 1]):
                        groups[key].append((int(c[0]), int(c[1]) if len(c) > 1 and c[1] else 0, int(c[2]) if len(c) > 2 and c[2] else 0))
            elif line.startswith("o ") or line.startswith("g "):
                obj = line.split(None, 1)[1].strip()
            elif line.startswith("usemtl "):
                mtl = line.split(None, 1)[1].strip()
    V = np.array(v, dtype=np.float32)
    VT = np.array(vt, dtype=np.float32) if vt else np.zeros((1, 2), np.float32)
    VN = np.array(vn, dtype=np.float32) if vn else np.zeros((1, 3), np.float32)
    return V, VT, VN, groups, order


def weld(V, VT, VN, corners):
    """Indexed vertex arrays for one primitive, in glTF axes, with identical corners merged."""
    c = np.array(corners, dtype=np.int64)
    p = V[c[:, 0] - 1] * np.array([-1, 1, -1], np.float32)
    uv = VT[np.maximum(c[:, 1] - 1, 0)].copy()
    uv[:, 1] = 1.0 - uv[:, 1]
    n = VN[np.maximum(c[:, 2] - 1, 0)] * np.array([-1, 1, -1], np.float32)
    key = np.hstack([np.round(p, 4), np.round(uv, 5), np.round(n, 3)])
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return p[first], uv[first], n[first], inverse.astype(np.uint32).ravel()


class GlbWriter:
    def __init__(self):
        self.gltf = {"asset": {"version": "2.0", "generator": "circle-geodata export_glb.py"},
                     "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
                     "accessors": [], "bufferViews": [], "buffers": [], "textures": [], "images": [], "samplers": []}
        self.bin = bytearray()
        self.extensions = set()

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin += b"\0"
        view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.bin += data
        self.gltf["bufferViews"].append(view)
        return len(self.gltf["bufferViews"]) - 1

    def accessor(self, array, kind, target, bounds=False):
        comp = {np.float32: 5126, np.uint16: 5123, np.uint32: 5125}[array.dtype.type]
        acc = {"bufferView": self.view(array.tobytes(), target), "componentType": comp, "count": len(array), "type": kind}
        if bounds:
            acc["min"] = array.min(axis=0).round(4).tolist()
            acc["max"] = array.max(axis=0).round(4).tolist()
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    def sampler(self, repeat):
        wrap = 10497 if repeat else 33071
        for i, s in enumerate(self.gltf["samplers"]):
            if s["wrapS"] == wrap:
                return i
        self.gltf["samplers"].append({"magFilter": 9729, "minFilter": 9987, "wrapS": wrap, "wrapT": wrap})
        return len(self.gltf["samplers"]) - 1

    def texture(self, image_path, repeat, max_size, cache):
        key = (image_path, repeat)
        if key in cache:
            return cache[key]
        img = Image.open(image_path)
        has_alpha = img.mode in ("RGBA", "LA") and img.getextrema()[-1][0] < 255
        img = img.convert("RGBA" if has_alpha else "RGB")
        if max(img.size) > max_size:
            s = max_size / max(img.size)
            img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
        out = io.BytesIO()
        if has_alpha:
            img.save(out, "PNG", optimize=True)
            mime = "image/png"
        else:
            img.save(out, "JPEG", quality=88)
            mime = "image/jpeg"
        self.gltf["images"].append({"bufferView": self.view(out.getvalue()), "mimeType": mime, "name": os.path.basename(image_path)})
        self.gltf["textures"].append({"sampler": self.sampler(repeat), "source": len(self.gltf["images"]) - 1})
        cache[key] = len(self.gltf["textures"]) - 1
        return cache[key]

    def write(self, path):
        g = self.gltf
        for k in ("textures", "images", "samplers"):
            if not g[k]:
                del g[k]
        if self.extensions:
            g["extensionsUsed"] = sorted(self.extensions)
        while len(self.bin) % 4:
            self.bin += b"\0"
        g["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(g, separators=(",", ":")).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin))


def find_texture_set(roots, name):
    """The base colour image of an ambientCG-style set ("A|B": first set found)."""
    for set_name in name.split("|"):
        for root in roots:
            for sub in ("ambientCG", "CC0", ""):
                d = os.path.join(root, sub, set_name)
                if os.path.isdir(d):
                    for f in sorted(os.listdir(d)):
                        low = f.lower()
                        if ("color" in low or "albedo" in low or "diffuse" in low) and low.endswith((".jpg", ".jpeg", ".png")):
                            return os.path.join(d, f)
    return None


def material(w, name, spec, obj_dir, tex_roots, max_tex, cache, missing):
    m = {"name": name, "pbrMetallicRoughness": {"metallicFactor": round(float(spec.get("metallic", 0)), 3),
                                                "roughnessFactor": round(1 - float(spec.get("smoothness", 0.5)), 3)}}
    pbr = m["pbrMetallicRoughness"]
    base = srgb_to_linear(spec.get("color", [0.8, 0.8, 0.8]))
    pbr["baseColorFactor"] = [round(float(x), 4) for x in base] + [1.0]
    tex_index = None
    if "map" in spec and os.path.exists(os.path.join(obj_dir, spec["map"])):
        tex_index = w.texture(os.path.join(obj_dir, spec["map"]), False, max(max_tex, 2048), cache)
        pbr["baseColorTexture"] = {"index": tex_index}
    elif "texture" in spec and tex_roots:
        img = find_texture_set(tex_roots, spec["texture"])
        if img:
            tex_index = w.texture(img, True, max_tex, cache)
            info = {"index": tex_index}
            tiling = float(spec.get("tiling", 1))
            if tiling != 1:
                info["extensions"] = {"KHR_texture_transform": {"scale": [tiling, tiling]}}
                w.extensions.add("KHR_texture_transform")
            pbr["baseColorTexture"] = info
        else:
            missing.add(spec["texture"])
    emission = None
    if "emissionColor" in spec:
        emission = srgb_to_linear(np.array(spec["emissionColor"]) / max(spec["emissionColor"])) * max(spec["emissionColor"])
    elif float(spec.get("emission", 0)) > 0:
        emission = srgb_to_linear(spec.get("color", [1, 1, 1])) * float(spec["emission"])
    if emission is not None:
        strength = float(np.max(emission))
        m["emissiveFactor"] = [round(float(x / strength), 4) for x in emission]
        if strength > 1:
            m["extensions"] = {"KHR_materials_emissive_strength": {"emissiveStrength": round(strength, 3)}}
            w.extensions.add("KHR_materials_emissive_strength")
        if "emissionMap" in spec and tex_index is not None:
            m["emissiveTexture"] = {"index": tex_index}
    if spec.get("alpha") == "mask":
        m["alphaMode"] = "MASK"
        m["alphaCutoff"] = 0.5
    if spec.get("doubleSided"):
        m["doubleSided"] = True
    w.gltf["materials"].append(m)
    return len(w.gltf["materials"]) - 1


def export(obj_path, look_path, tex_roots, out_path, max_tex):
    look = json.load(open(look_path))["materials"]
    V, VT, VN, groups, order = read_obj(obj_path)
    w = GlbWriter()
    mat_index, cache, missing, unknown = {}, {}, set(), set()
    nodes = {}
    tris = verts_in = verts_out = 0
    for obj, mtl in order:
        corners = groups[(obj, mtl)]
        p, uv, n, idx = weld(V, VT, VN, corners)
        verts_in += len(corners)
        verts_out += len(p)
        tris += len(idx) // 3
        if mtl not in mat_index:
            spec = look.get(mtl)
            if spec is None:
                unknown.add(mtl)
                spec = {"color": [0.8, 0.8, 0.8], "smoothness": 0.2}
            mat_index[mtl] = material(w, mtl, spec, os.path.dirname(obj_path), tex_roots, max_tex, cache, missing)
        index = idx.astype(np.uint16) if len(p) < 65536 else idx
        prim = {"attributes": {"POSITION": w.accessor(p, "VEC3", 34962, bounds=True),
                               "NORMAL": w.accessor(n, "VEC3", 34962),
                               "TEXCOORD_0": w.accessor(uv, "VEC2", 34962)},
                "indices": w.accessor(index, "SCALAR", 34963), "material": mat_index[mtl]}
        nodes.setdefault(obj, []).append(prim)
    for obj, prims in nodes.items():
        w.gltf["meshes"].append({"name": obj, "primitives": prims})
        w.gltf["nodes"].append({"name": obj, "mesh": len(w.gltf["meshes"]) - 1})
        w.gltf["scenes"][0]["nodes"].append(len(w.gltf["nodes"]) - 1)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    w.write(out_path)
    size = os.path.getsize(out_path)
    print(f"{os.path.basename(out_path)}: {len(nodes)} cells, {sum(len(p) for p in nodes.values())} primitives, {len(mat_index)} materials, "
          f"{tris} triangles, vertices {verts_in} -> {verts_out}, {len(w.gltf.get('images', []))} images, {size / 1048576:.2f} MB")
    if unknown:
        print("materials not in look.json (grey):", ", ".join(sorted(unknown)))
    if missing:
        print("texture sets not found:", ", ".join(sorted(missing)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obj")
    ap.add_argument("--look", default=os.path.join(os.path.dirname(__file__), "..", "look", "look.json"))
    ap.add_argument("--textures", action="append", default=[], help="folder holding ambientCG/ and CC0/ texture sets (repeatable)")
    ap.add_argument("--max-texture", type=int, default=1024)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    export(args.obj, args.look, args.textures, args.out, args.max_texture)


if __name__ == "__main__":
    sys.exit(main())
