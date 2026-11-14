"""The Osu facade kit: modelled parts for the plain district buildings, built by Blender in the background.

    blender -b --factory-startup -P scripts/build_kit.py -- --out ../circle-game/public/kit/osu-kit.glb \
        [--preview kit-preview.png]

circle-bible DECISIONS.md (13 Nov 2026): buildings come from open data and kits made by Blender scripts. This first
kit dresses the walls the game already has, on the same grid as the wall material (circle-game render/windows.ts):
bays 3.0 m wide and floors 3.2 m high, the window 50% of the bay wide from 30% to 78% of the floor height, the shop
opening 84% of the bay wide and 2.6 m high. Shapes and sizes follow circle-reference's street survey and photos:
window surrounds with sills and hoods, burglar bars, balconies with concrete balusters or steel rails, roller-shutter
boxes, corrugated canopies on brackets or posts, air-conditioning units, polytanks on stands, parapets with rebar
left for the next floor, concrete electricity poles and the small LED heads they carry.

Conventions, so the game can place every part with one transform:
  - one object per part, named kit_<part>, at the world origin, Z up (glTF export turns it to Y up);
  - X runs along the wall, the facade plane is Y = 0 and the part stands out towards -Y (glTF +Z, the wall's normal);
  - Z = 0 is the floor line the part belongs to (a window's floor, the ground for shops, poles and canopies, the roof
    edge for parapets and tanks);
  - materials are named M_Kit_*; trim, sheet and plastic are near white or neutral so the game can tint them per
    instance (instance colours multiply the base colour).
Low poly throughout: thousands of instances are drawn at once.
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

BAY, FLOOR = 3.0, 3.2
WIN_X0, WIN_X1 = -0.75, 0.75                 # window: 25% to 75% of the bay, centred
WIN_Z0, WIN_Z1 = 0.30 * FLOOR, 0.78 * FLOOR  # 0.96 m to 2.496 m above the floor line
SHOP_HALF, SHOP_TOP = 0.42 * BAY, 0.81 * FLOOR


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out, preview = None, None
    for i, a in enumerate(argv):
        if a == "--out":
            out = argv[i + 1]
        elif a == "--preview":
            preview = argv[i + 1]
    if not out:
        raise SystemExit("usage: blender -b -P build_kit.py -- --out kit.glb [--preview kit.png]")
    return out, preview


# ---------------------------------------------------------------------------------------------- materials

MATS = {}


def material(name, rgb, rough=0.8, metal=0.0, emit=None, double=False):
    """A Principled material; colours are linear, as glTF stores them."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        key = "Emission Color" if "Emission Color" in b.inputs else "Emission"
        b.inputs[key].default_value = (*emit, 1)
        if "Emission Strength" in b.inputs:
            b.inputs["Emission Strength"].default_value = 4.0
    m.use_backface_culling = not double
    MATS[name] = m
    return m


def make_materials():
    material("M_Kit_Trim", (0.82, 0.8, 0.76), 0.9)            # render and concrete, tinted per wall
    material("M_Kit_Iron", (0.025, 0.025, 0.028), 0.45, 0.7)  # burglar bars, rails, brackets
    material("M_Kit_Metal", (0.32, 0.33, 0.34), 0.5, 0.6)     # shutter boxes and guides
    material("M_Kit_Sheet", (0.8, 0.8, 0.8), 0.5, 0.45, double=True)  # corrugated sheet, tinted per instance
    material("M_Kit_Plastic", (0.012, 0.012, 0.012), 0.6)     # polytanks
    material("M_Kit_Unit", (0.72, 0.72, 0.69), 0.55)          # air-conditioning units
    material("M_Kit_Grille", (0.06, 0.06, 0.06), 0.7)
    material("M_Kit_Pole", (0.42, 0.41, 0.38), 0.95)          # concrete poles
    material("M_Kit_Ceramic", (0.55, 0.42, 0.3), 0.35)        # insulators
    material("M_Kit_LED", (0.9, 0.9, 0.88), 0.3, emit=(1.0, 0.97, 0.92))


# ---------------------------------------------------------------------------------------------- mesh building

class Part:
    """One kit part: a bmesh with material slots, turned into an object at the origin."""

    def __init__(self, name):
        self.name = "kit_" + name
        self.bm = bmesh.new()
        self.slots = []

    def slot(self, mat):
        if mat not in self.slots:
            self.slots.append(mat)
        return self.slots.index(mat)

    def _tag(self, faces, mat):
        i = self.slot(mat)
        for f in faces:
            f.material_index = i

    def box(self, x0, x1, y0, y1, z0, z1, mat):
        """An axis-aligned box from its bounds."""
        c = Vector(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
        m = Matrix.Translation(c) @ Matrix.Diagonal((abs(x1 - x0), abs(y1 - y0), abs(z1 - z0), 1))
        r = bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)
        self._tag({f for v in r["verts"] for f in v.link_faces}, mat)

    def beam(self, a, b, w, h, mat):
        """A square-section bar from point a to point b."""
        a, b = Vector(a), Vector(b)
        d = b - a
        length = d.length
        z = d.normalized()
        up = Vector((0, 0, 1)) if abs(z.z) < 0.95 else Vector((1, 0, 0))
        x = up.cross(z).normalized()
        y = z.cross(x)
        rot = Matrix((x, y, z)).transposed().to_4x4()
        m = Matrix.Translation((a + b) / 2) @ rot @ Matrix.Diagonal((w, h, length, 1))
        r = bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)
        self._tag({f for v in r["verts"] for f in v.link_faces}, mat)

    def cylinder(self, x, y, z0, z1, r0, r1, seg, mat, cap=True):
        """A vertical (possibly tapered) cylinder."""
        m = Matrix.Translation((x, y, (z0 + z1) / 2))
        res = bmesh.ops.create_cone(self.bm, cap_ends=cap, cap_tris=False, segments=seg, radius1=r0, radius2=r1,
                                    depth=z1 - z0, matrix=m)
        self._tag({f for v in res["verts"] for f in v.link_faces}, mat)

    def cylinder_y(self, x, z, y0, y1, r, seg, mat):
        """A cylinder lying along Y (out of the wall)."""
        m = Matrix.Translation((x, (y0 + y1) / 2, z)) @ Matrix.Rotation(math.pi / 2, 4, "X")
        res = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=seg, radius1=r, radius2=r,
                                    depth=abs(y1 - y0), matrix=m)
        self._tag({f for v in res["verts"] for f in v.link_faces}, mat)

    def sheet(self, x0, x1, y0, y1, z_back, z_front, waves, amp, mat):
        """A corrugated sheet sloping from the wall (y0) to its front edge (y1); ridges run out from the wall."""
        n = waves * 4
        rows = []
        for y, zb in ((y0, z_back), (y1, z_front)):
            row = []
            for i in range(n + 1):
                t = i / n
                row.append(self.bm.verts.new((x0 + (x1 - x0) * t, y, zb + amp * math.sin(t * waves * 2 * math.pi))))
            rows.append(row)
        faces = []
        for i in range(n):
            faces.append(self.bm.faces.new((rows[0][i], rows[0][i + 1], rows[1][i + 1], rows[1][i])))
        self._tag(faces, mat)

    def build(self, collection):
        me = bpy.data.meshes.new(self.name)
        bmesh.ops.remove_doubles(self.bm, verts=self.bm.verts, dist=1e-5)
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces)
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.slots:
            me.materials.append(MATS[m])
        for p in me.polygons:
            p.use_smooth = False
        ob = bpy.data.objects.new(self.name, me)
        collection.objects.link(ob)
        return ob


# ---------------------------------------------------------------------------------------------- the parts

def window():
    """Surround of an upper-floor window: jambs, a lintel with a hood over it, and a sill."""
    p = Part("window")
    t = "M_Kit_Trim"
    p.box(WIN_X0 - 0.1, WIN_X0, -0.06, 0, WIN_Z0, WIN_Z1, t)
    p.box(WIN_X1, WIN_X1 + 0.1, -0.06, 0, WIN_Z0, WIN_Z1, t)
    p.box(WIN_X0 - 0.1, WIN_X1 + 0.1, -0.08, 0, WIN_Z1, WIN_Z1 + 0.14, t)
    p.box(WIN_X0 - 0.18, WIN_X1 + 0.18, -0.24, 0, WIN_Z1 + 0.14, WIN_Z1 + 0.2, t)   # hood against sun and rain
    p.box(WIN_X0 - 0.12, WIN_X1 + 0.12, -0.14, 0, WIN_Z0 - 0.08, WIN_Z0, t)          # sill
    return p


def bars():
    """Burglar bars over a window: a frame, uprights every 12.5 cm and three rails, with a diamond in the middle."""
    p = Part("bars")
    i, y0, y1, s = "M_Kit_Iron", -0.07, -0.05, 0.016
    x0, x1, z0, z1 = WIN_X0 + 0.02, WIN_X1 - 0.02, WIN_Z0 + 0.02, WIN_Z1 - 0.02
    for z in (z0, z1, z0 + (z1 - z0) / 3, z0 + 2 * (z1 - z0) / 3):
        p.box(x0, x1, y0, y1, z - s / 2, z + s / 2, i)
    n = 12
    for k in range(n + 1):
        x = x0 + (x1 - x0) * k / n
        p.box(x - s / 2, x + s / 2, y0, y1, z0, z1, i)
    cz, cx, r = (z0 + z1) / 2, 0.0, 0.22
    pts = [(cx, cz + r), (cx + r, cz), (cx, cz - r), (cx - r, cz), (cx, cz + r)]
    for (ax, az), (bx, bz) in zip(pts, pts[1:]):
        p.beam((ax, (y0 + y1) / 2, az), (bx, (y0 + y1) / 2, bz), s, s, i)
    return p


def slab(p, depth=1.1, width=2.7):
    t = "M_Kit_Trim"
    p.box(-width / 2, width / 2, -depth, 0, -0.15, 0.0, t)


def balcony_balusters():
    """A balcony on concrete balusters, as on Osu's older apartment blocks: slab, plinth, balusters, handrail."""
    p = Part("balcony_balusters")
    t, d, w = "M_Kit_Trim", 1.1, 2.7
    slab(p, d, w)
    for (ax, ay), (bx, by) in (((-w / 2, -d), (w / 2, -d)), ((-w / 2, 0), (-w / 2, -d)), ((w / 2, 0), (w / 2, -d))):
        x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
        p.box(x0 - 0.07 if x0 == x1 else x0, x1 + 0.07 if x0 == x1 else x1,
              y0 - 0.07 if y0 == y1 else y0, y1 + 0.07 if y0 == y1 else y1, 0.0, 0.12, t)
        p.box(x0 - 0.08 if x0 == x1 else x0, x1 + 0.08 if x0 == x1 else x1,
              y0 - 0.08 if y0 == y1 else y0, y1 + 0.08 if y0 == y1 else y1, 0.78, 0.9, t)
    def baluster(x, y):
        p.cylinder(x, y, 0.12, 0.3, 0.035, 0.065, 6, t)
        p.cylinder(x, y, 0.3, 0.6, 0.065, 0.035, 6, t)
        p.cylinder(x, y, 0.6, 0.78, 0.035, 0.045, 6, t)
    k = 13
    for j in range(k):
        baluster(-w / 2 + 0.12 + (w - 0.24) * j / (k - 1), -d)
    for j in range(1, 5):
        y = -d * j / 5
        baluster(-w / 2, y)
        baluster(w / 2, y)
    return p


def balcony_rail():
    """A balcony with a steel rail: top and bottom rails, uprights every 11 cm."""
    p = Part("balcony_rail")
    i, d, w, s = "M_Kit_Iron", 1.1, 2.7, 0.02
    slab(p, d, w)
    for z in (0.12, 0.95):
        p.box(-w / 2, w / 2, -d - 0.02, -d + 0.02, z - 0.02, z + 0.02, i)
        for x in (-w / 2, w / 2):
            p.box(x - 0.02, x + 0.02, -d, 0, z - 0.02, z + 0.02, i)
    n = int(w / 0.11)
    for j in range(n + 1):
        x = -w / 2 + w * j / n
        p.box(x - s / 2, x + s / 2, -d - s / 2, -d + s / 2, 0.0, 0.95, i)
    for j in range(1, 10):
        y = -d * j / 10
        for x in (-w / 2, w / 2):
            p.box(x - s / 2, x + s / 2, y - s / 2, y + s / 2, 0.0, 0.95, i)
    return p


def shutter_box():
    """The roller-shutter housing over a shop opening, and its two guide rails."""
    p = Part("shutter_box")
    m = "M_Kit_Metal"
    p.box(-SHOP_HALF - 0.06, SHOP_HALF + 0.06, -0.24, 0, SHOP_TOP - 0.02, SHOP_TOP + 0.26, m)
    for x in (-SHOP_HALF, SHOP_HALF):
        p.box(x - 0.035, x + 0.035, -0.06, 0, 0.0, SHOP_TOP, m)
    return p


def canopy(posts):
    """A corrugated canopy over a shop, 2.8 m wide and 1.45 m deep, its back edge at 2.75 m: on two iron brackets,
    or on two posts at the front corners (the kiosk version)."""
    p = Part("canopy_posts" if posts else "canopy")
    z_back, depth, w = 2.75, 1.45, 2.8
    z_front = z_back - depth * math.tan(math.radians(15))
    p.sheet(-w / 2, w / 2, 0.0, -depth, z_back, z_front, 37, 0.018, "M_Kit_Sheet")
    i = "M_Kit_Iron"
    for x in (-w / 2 + 0.15, w / 2 - 0.15):
        p.beam((x, -0.02, z_back - 0.02), (x, -depth + 0.05, z_front - 0.02), 0.04, 0.04, i)   # rafter
        if posts:
            p.box(x - 0.03, x + 0.03, -depth + 0.02, -depth + 0.08, 0.0, z_front - 0.02, i)
        else:
            p.beam((x, -0.02, z_back - 0.75), (x, -depth * 0.7, z_back - depth * 0.7 * math.tan(math.radians(15)) - 0.04),
                   0.035, 0.035, i)
    p.box(-w / 2, w / 2, -depth - 0.01, -depth + 0.03, z_front - 0.1, z_front + 0.02, i)   # front fascia
    return p


def ac_unit():
    """A split air-conditioning unit on its brackets, its fan grille facing out; origin at its back, bottom, centre."""
    p = Part("ac")
    p.box(-0.4, 0.4, -0.3, -0.02, 0.0, 0.55, "M_Kit_Unit")
    p.cylinder_y(-0.08, 0.27, -0.31, -0.29, 0.2, 12, "M_Kit_Grille")
    for x in (-0.3, 0.3):
        p.box(x - 0.02, x + 0.02, -0.34, 0, -0.04, 0.0, "M_Kit_Iron")
        p.beam((x, 0, -0.3), (x, -0.32, -0.03), 0.025, 0.025, "M_Kit_Iron")
    return p


def tank():
    """A ribbed black polytank (about 1000 l) on a steel stand; origin at the foot of the stand."""
    p = Part("tank")
    i, h = "M_Kit_Iron", 0.6
    for x in (-0.45, 0.45):
        for y in (-0.45, 0.45):
            p.box(x - 0.025, x + 0.025, y - 0.025, y + 0.025, 0.0, h, i)
    p.box(-0.5, 0.5, -0.5, 0.5, h - 0.05, h, i)
    p.cylinder(0, 0, h, h + 1.25, 0.55, 0.55, 16, "M_Kit_Plastic")
    for k in range(1, 5):
        z = h + 1.25 * k / 5
        p.cylinder(0, 0, z - 0.03, z + 0.03, 0.57, 0.57, 16, "M_Kit_Plastic", cap=False)
    p.cylinder(0, 0, h + 1.25, h + 1.4, 0.55, 0.2, 16, "M_Kit_Plastic")
    p.cylinder(0, 0, h + 1.4, h + 1.47, 0.2, 0.2, 12, "M_Kit_Plastic")
    return p


def parapet(rebar):
    """A parapet over one bay: 0.9 m of wall on the roof edge with a coping. The unfinished version has column
    stubs at the bay ends with their rebar left standing for the next floor, as on so many Accra roofs."""
    p = Part("parapet_rebar" if rebar else "parapet")
    t = "M_Kit_Trim"
    p.box(-BAY / 2, BAY / 2, 0.0, 0.2, 0.0, 0.9, t)
    p.box(-BAY / 2, BAY / 2, -0.04, 0.24, 0.9, 0.98, t)
    if rebar:
        i = "M_Kit_Iron"
        for x in (-BAY / 2 + 0.15, BAY / 2 - 0.15):
            p.box(x - 0.15, x + 0.15, 0.0, 0.3, 0.98, 1.3, t)
            for dx in (-0.09, 0.09):
                for dy in (0.06, 0.24):
                    p.box(x + dx - 0.008, x + dx + 0.008, dy - 0.008, dy + 0.008, 1.3, 2.1, i)
    return p


def pole():
    """A concrete electricity pole, 9.5 m, tapering; a steel crossarm across the street at 8.9 m with four pin
    insulators. The crossarm runs along X; the game turns X across the road."""
    p = Part("pole")
    p.cylinder(0, 0, 0.0, 9.5, 0.15, 0.09, 8, "M_Kit_Pole")
    p.box(-0.85, 0.85, -0.05, 0.05, 8.85, 8.95, "M_Kit_Iron")
    for x in (-0.7, -0.25, 0.25, 0.7):
        p.cylinder(x, 0, 8.95, 9.08, 0.035, 0.025, 6, "M_Kit_Ceramic")
    p.beam((0, 0, 8.4), (-0.6, 0, 8.85), 0.04, 0.04, "M_Kit_Iron")   # brace
    return p


def pole_led():
    """The small LED head the residential streets get on their electricity poles, on a 0.55 m bracket pointing
    along -Y; origin at the pole's axis, 8.35 m up."""
    p = Part("pole_led")
    p.beam((0, -0.08, -0.25), (0, -0.55, 0.0), 0.04, 0.04, "M_Kit_Iron")
    p.box(-0.11, 0.11, -0.75, -0.35, -0.04, 0.03, "M_Kit_Metal")
    p.box(-0.08, 0.08, -0.72, -0.38, -0.05, -0.04, "M_Kit_LED")
    return p


PARTS = [window, bars, balcony_balusters, balcony_rail, shutter_box, lambda: canopy(False), lambda: canopy(True),
         ac_unit, tank, lambda: parapet(False), lambda: parapet(True), pole, pole_led]


# ---------------------------------------------------------------------------------------------- export and preview

def preview(objects, path):
    """Renders every part side by side, in front of a grey wall, with the Workbench engine."""
    scene = bpy.context.scene
    col = bpy.data.collections.new("preview")
    scene.collection.children.link(col)
    copies, x = [], 0.0
    for ob in objects:
        w = max(ob.dimensions.x, 1.0)
        c = ob.copy()
        c.location = (x + w / 2, 0, 0)
        col.objects.link(c)
        copies.append(c)
        x += w + 0.6
    wall = bpy.data.objects.new("wall", bpy.data.meshes.new("wall"))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((x / 2, 0.3, 4.5)) @ Matrix.Diagonal((x + 4, 0.6, 9.5, 1)))
    bm.to_mesh(wall.data)
    bm.free()
    col.objects.link(wall)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = x + 2
    cam.location = (x / 2, -30, 5.5)
    cam.rotation_euler = (math.radians(80), 0, 0)
    col.objects.link(cam)
    scene.camera = cam
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 2400, 800
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    for c in copies + [wall, cam]:
        bpy.data.objects.remove(c, do_unlink=True)


def main():
    out, preview_path = parse_args()
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
        preview(objects, preview_path)
        print(f"wrote {preview_path}")


main()
