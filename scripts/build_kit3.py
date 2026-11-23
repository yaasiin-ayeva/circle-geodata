"""The third facade kit: the civic parts of Independence Avenue's kit (avenue_kit.py; circle-game
docs/independence-avenue-plan.md, stage 2), built by Blender in the background with build_kit.py's conventions, materials
and helpers.

    blender -b --factory-startup -P scripts/build_kit3.py -- --out ../circle-game/public/kit/kit3.glb \\
        [--preview kit3-preview.png]

  - sun_hood: the cast-concrete hood over a window of Accra's hospital wards and school blocks of the 1950s to 1970s:
    a sloping slab 0.55 m deep over the opening with a short cheek at each end. 1 m wide (the game stretches it to the
    window), its underside at the facade's Z = 0 line, standing out towards -Y.
  - flagpole: a white steel flagpole 9 m high on a square concrete plinth, Ghana's flag (red, gold and green bands, a
    black star) at its head, as before the ministries. Origin at its foot.

The looks are estimates from Accra's known civic architecture; no photograph of the avenue's buildings was used. All
materials merge into the game's one kit material (render/facades.ts mergeKit), so the kit adds no render pipeline.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import build_kit as kit  # noqa: E402
from build_kit import Part  # noqa: E402


def make_materials():
    kit.make_materials()
    kit.material("M_Kit_FlagRed", (0.62, 0.05, 0.06), 0.8)
    kit.material("M_Kit_FlagGold", (0.85, 0.62, 0.05), 0.8)
    kit.material("M_Kit_FlagGreen", (0.0, 0.32, 0.17), 0.8)
    kit.material("M_Kit_FlagBlack", (0.01, 0.01, 0.01), 0.8)
    kit.material("M_Kit_White", (0.86, 0.86, 0.84), 0.5, 0.2)


def sun_hood():
    """A concrete window hood 1 m wide: a slab 0.55 m deep, 0.09 m thick, falling 0.08 m to its front edge, its back on
    the facade at Z = 0 to 0.09, and a 0.08 m cheek at each end, 0.35 m deep and 0.3 m high."""
    p = Part("sun_hood")
    t = "M_Kit_Trim"
    p.beam((0, -0.0, 0.045), (0, -0.55, -0.035), 1.0, 0.09, t)
    for x in (-0.5, 0.42):
        p.box(x, x + 0.08, -0.35, 0.0, -0.3, 0.06, t)
    return p


def flagpole():
    """A 9 m flagpole on a 0.8 m square plinth 0.5 m high, a gold finial, and Ghana's flag, 1.8 x 1.2 m, at its head
    (three bands and the black star, a flat card)."""
    p = Part("flagpole")
    p.box(-0.4, 0.4, -0.4, 0.4, 0.0, 0.5, "M_Kit_Trim")
    p.cylinder(0, 0, 0.5, 9.0, 0.06, 0.04, 8, "M_Kit_White")
    p.cylinder(0, 0, 9.0, 9.12, 0.07, 0.07, 8, "M_Kit_FlagGold")
    x0, x1, top, h = 0.06, 1.86, 8.9, 1.2
    for i, mat in enumerate(("M_Kit_FlagRed", "M_Kit_FlagGold", "M_Kit_FlagGreen")):
        z1 = top - i * h / 3
        p.box(x0, x1, -0.006, 0.006, z1 - h / 3, z1, mat)
    # The black star on the gold band, a little proud of the flag on both faces: a five-pointed star as triangles.
    cx, cz, r0, r1 = (x0 + x1) / 2, top - h / 2, 0.19, 0.08
    for face in (-0.012, 0.012):
        pts = [(cx + (r0 if k % 2 == 0 else r1) * math.sin(math.pi * k / 5), cz + (r0 if k % 2 == 0 else r1) * math.cos(math.pi * k / 5))
               for k in range(10)]
        for k in range(10):
            a, b = pts[k], pts[(k + 1) % 10]
            p.tri((cx, face, cz), (a[0], face, a[1]), (b[0], face, b[1]), "M_Kit_FlagBlack")
    return p


def _tri(self, a, b, c, mat):
    """A single triangle (both faces) in the part's mesh."""
    vs = [self.bm.verts.new(x) for x in (a, b, c)]
    f1 = self.bm.faces.new(vs)
    vs2 = [self.bm.verts.new(x) for x in (a, c, b)]
    f2 = self.bm.faces.new(vs2)
    self._tag({f1, f2}, mat)


Part.tri = _tri
PARTS = [sun_hood, flagpole]


def main():
    out, preview_path = kit.parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    make_materials()
    col = bpy.context.scene.collection
    objects = [build().build(col) for build in PARTS]
    for ob in objects:
        tris = sum(len(p.vertices) - 2 for p in ob.data.polygons)
        print(f"{ob.name:24s} {tris:6d} tris  {len(ob.data.materials)} materials  "
              f"{ob.dimensions.x:.2f} x {ob.dimensions.y:.2f} x {ob.dimensions.z:.2f} m")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True, export_apply=True,
                              export_materials="EXPORT", use_selection=False)
    print(f"wrote {out} ({os.path.getsize(out) / 1024:.0f} KB)")
    if preview_path:
        kit.preview(objects, preview_path)
        print(f"wrote {preview_path}")


if __name__ == "__main__":
    main()
