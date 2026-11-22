"""The second facade kit: the parts the typologies (typology.py) add to Accra's generic districts, built by Blender in
the background, beside the Osu kit (build_kit.py) whose conventions, materials and helpers it shares.

    blender -b --factory-startup -P scripts/build_kit2.py -- --out ../circle-game/public/kit/kit2.glb \\
        [--preview kit2-preview.png]

  - balcony_glass: the balcony of the mid-rise flats of East Legon, Airport Residential and Cantonments (circle-reference
    legon-building-002, osu-building-002 and -003, ridge-building-005): a slab, glass panels on three sides on two
    steel posts, a steel handrail. Same footprint as build_kit's balconies (2.7 x 1.1 m), floor line at Z = 0.
  - solar_heater: a thermosiphon solar water heater on a flat roof (East Legon's and Spintex's roofs, beside the
    polytank): a collector of 2.0 x 1.0 m tilted to the sky at 20 degrees on a steel stand, its white tank along the
    top. Origin at its foot, on the roof, like the polytank's.

Conventions as build_kit.py's: one object per part, kit_<part>, Z up, the facade plane Y = 0 with the part standing out
towards -Y. Materials: build_kit's (M_Kit_Trim tinted per wall, M_Kit_Iron, M_Kit_Metal, M_Kit_Unit) and one more,
M_Kit_Glass, opaque dark glass: in the game every kit material is merged into one material per side, its colour,
roughness and metalness carried per vertex (circle-game render/facades.ts mergeKit), so a kit material is no render
pipeline. Low poly: drawn by the thousand.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import build_kit as kit  # noqa: E402
from build_kit import Part  # noqa: E402


def make_materials():
    kit.make_materials()
    # Opaque tinted glass: dark blue-green, smooth and a little metallic, so it catches the sky like the real panels.
    kit.material("M_Kit_Glass", (0.035, 0.07, 0.075), 0.08, 0.45)


def balcony_glass():
    """A balcony with glass panels: slab 2.7 x 1.1 m, panels 1.0 m high on its front and sides in a steel channel, a
    flat steel handrail on top, two square posts at the front corners."""
    p = Part("balcony_glass")
    d, w = 1.1, 2.7
    kit.slab(p, d, w)
    g, m = "M_Kit_Glass", "M_Kit_Metal"
    p.box(-w / 2 + 0.04, w / 2 - 0.04, -d + 0.03, -d + 0.05, 0.02, 1.0, g)        # front panel
    for x in (-w / 2 + 0.03, w / 2 - 0.05):                                      # side panels
        p.box(x, x + 0.02, -d + 0.05, -0.02, 0.02, 1.0, g)
    p.box(-w / 2, w / 2, -d - 0.01, -d + 0.07, 1.0, 1.05, m)                     # handrail
    for x in (-w / 2, w / 2 - 0.08):
        p.box(x, x + 0.08, -d + 0.07 - 0.08, -0.02, 1.0, 1.05, m)
        p.box(x, x + 0.06, -d - 0.01, -d + 0.05, 0.0, 1.0, m)                    # posts
    return p


def solar_heater():
    """A thermosiphon solar water heater: a 2.0 x 1.0 m collector tilted 20 degrees on a steel stand, the white tank
    along its top edge. Origin at its foot on the roof; the collector faces -Y."""
    import math
    p = Part("solar_heater")
    i, g, t = "M_Kit_Iron", "M_Kit_Glass", "M_Kit_Unit"
    w, l, tilt = 2.0, 1.0, math.radians(20)
    z0, z1 = 0.35, 0.35 + l * math.sin(tilt)
    y0, y1 = -0.3 - l * math.cos(tilt), -0.3
    # The collector: a slab from its low front edge (y0, z0) to its high back edge (y1, z1).
    p.beam((0, y0, z0), (0, y1, z1), w, 0.06, g)
    # The tank: a cylinder along X behind the top edge.
    import bmesh
    from mathutils import Matrix
    m = Matrix.Translation((0, y1 + 0.05, z1 + 0.3)) @ Matrix.Rotation(math.pi / 2, 4, "Y")
    res = bmesh.ops.create_cone(p.bm, cap_ends=True, cap_tris=False, segments=10, radius1=0.24, radius2=0.24, depth=w, matrix=m)
    p._tag({f for v in res["verts"] for f in v.link_faces}, t)
    for x in (-w / 2 + 0.1, w / 2 - 0.1):                                       # the stand's legs
        p.box(x - 0.02, x + 0.02, y0 - 0.02, y0 + 0.02, 0.0, z0, i)
        p.box(x - 0.02, x + 0.02, y1 - 0.02, y1 + 0.02, 0.0, z1 + 0.1, i)
        p.beam((x, y0, 0.02), (x, y1, 0.02), 0.04, 0.04, i)
    return p


PARTS = [balcony_glass, solar_heater]


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
