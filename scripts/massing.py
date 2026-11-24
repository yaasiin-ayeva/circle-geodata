"""Building modules for the district: what turns an extruded footprint into an Osu building.

Used by build_district.py. The rules come from circle-reference's street survey (building heights, roofs,
boundary walls) and circle-bible's research/landscape-look.md (roof materials):

- house: one or two storeys away from Oxford Street, on a footprint close to a rectangle. Walls up to the
  eaves and a hipped roof over the footprint's best-fit rectangle with a 0.5 m overhang, a fascia and a
  soffit. Grey cement or galvanised sheet, rusted sheet, or red tile (tile mostly on two-storey houses).
- shed: in districts whose palette asks for it (Jamestown, up to three storeys), the other low buildings of any footprint carry a
  single-slope sheet roof at a low pitch, 0.3 m over the walls, falling across the footprint's short side.
- block: a flat concrete roof behind a parapet, as before. Most blocks of two storeys or more carry a stair
  bulkhead on the roof; the top storey of tall blocks is often set back 2 m behind a terrace.
- frame: a finished block with an unfinished storey on top, bare concrete columns and no walls.
- compound walls: residential buildings stand in walled yards. The walls follow the street frontage of the
  blocks, 1.2 m back from the road edge, stop short of every building and of shopfronts, and open on a steel
  gate in front of each yard's house.

Nothing here adds a material per building kind beyond six: M_RoofSheet_Rust, M_RoofTile_Red, M_RoofSheet_Painted
(districts beyond Osu), M_Gate, M_Concrete_Raw and M_Pool (the pools the satellite sees, imagery.py). Compound walls, fascias, soffits and bulkheads reuse the wall paints (M_Wall_*), so a cell
draws them with the walls it already has. The game tells these surfaces apart by a flag in the integer
thousands of the texture v coordinate (district meshes carry no textures): see NO_OPENINGS, NO_PARAPET, YARD.
"""
import hashlib
import math

import mapbox_earcut as earcut
import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box

STOREY_M = 3.2

# Surface flags for circle-game, bits in the integer thousands of v on vertical faces (v = flags * 1000; the plain
# district's walls without modules have v = height / 3, under 1000, so they read as no flags):
#   NO_OPENINGS  no window or shop openings (compound walls, gate posts, fascias, frame columns);
#   NO_PARAPET   nothing on top: a wall under a pitched roof or an unfinished frame storey, or a roof bulkhead
#                (no parapet, roof tank or rebar);
#   YARD         a building that stands in a walled compound: few shops on its ground floor (residential
#                streets have 10-20% shopfront, Oxford Street 80-90%).
NO_OPENINGS, NO_PARAPET, YARD = 1, 2, 4
# The typologies' flags (typology.py, 19 Nov 2026), read by circle-game's wall material and dressing under ?kit2:
#   SHOPFRONT  a storey building's frontage: shops in almost every ground bay, balconies on a third of the bays above;
#   CURTAIN    a glass curtain wall: the game draws mullions, spandrels and reflective glass instead of punched windows;
#   BALCONIES  flats: balconies with glass rails on most bays, almost no shops;
#   HALL       a church's or mosque's hall: no shops, tall windows over the double-height floor.
# Older games read these walls by their low bits only, as before (a SHOPFRONT wall is a plain street wall to them).
SHOPFRONT, CURTAIN, BALCONIES, HALL = 8, 16, 32, 64
# The Independence Avenue kit's flags (avenue_kit.py, 22 Nov 2026), read by circle-game under ?avenue-kit (on by default):
#   OFFICE     an office, ministry, bank, hospital or school wall: punched windows, no shop bays, no laundry or balconies;
#   BANDS      tinted ribbon glazing, a glass band along each floor between opaque spandrels (the office slabs' variant);
#   HOME       a villa or bungalow in its walled compound: the survey's few shops (15 %), barred windows.
#   KIND_*     the avenue kit's type of facade, a code in the three bits from 1024, written into u as kind * 1000 (see
#              _uv; the game's own paint, window grid and dressing for each; circle-game render/windows.ts KIND): a
#              concrete-framed office, a glass office, a
#              bank's panelled storey, a ministry's painted concrete, an institution's cream (hospitals, schools,
#              bungalows), a villa's render, a plain volume.
# A wall with none of the bits from 8 up is read by the game exactly as before (its legacy reading).
OFFICE, BANDS, HOME = 128, 256, 512
KIND_OFFICE, KIND_GLASS, KIND_BANK, KIND_MINISTRY, KIND_INSTITUTION, KIND_VILLA, KIND_VOLUME = (k * 1024 for k in range(1, 8))

# Compound wall paints (street survey: white or cream, coral-red, ochre, dark grey on newer villas), as the
# district's wall materials; circle-game repaints them in the surveyed palette.
BOUNDARY_PAINTS = [("M_Wall_Concrete", 0.30), ("M_Wall_PaleBlue", 0.25), ("M_Wall_Terracotta", 0.15),
                   ("M_Wall_Ochre", 0.15), ("M_Wall_Grey", 0.15)]

# Roof palettes per district (circle-reference catalogue/district-roofs.md, roofs counted from above on 14 Nov 2026):
# the material mix of pitched roofs, the share of eligible houses given a pitched roof, and the pitch of sheet roofs
# in degrees (tile-red roofs keep 28). Osu keeps the mix by storeys in building(), which its count matches (75% grey
# or rusted, 24% red among 240 pitched roofs). Grey and rust are not told apart from above: their split is a guess.
# Districts not listed fall back to Osu.
ROOF_PALETTES = {
    # 80 of 86 roof points grey, beige or dark sheet, 1 clearly rusted, 2 red, 3 flat; low-pitched, edge to edge.
    # "shed": the share of the other low buildings (up to "shed_storeys", any footprint) given a low single-slope sheet
    # roof, at "shed_pitch" degrees; hipped roofs alone left half the roof area flat where the count saw 3%.
    "jamestown": {"mix": [("M_Roof", 0.95), ("M_RoofSheet_Rust", 0.03), ("M_RoofTile_Red", 0.02)], "pitched": 0.97, "pitch": 12,
                  "shed": 0.95, "shed_pitch": 5, "shed_storeys": 3},
    # The old centre around Jamestown, Ussher Town and Makola (build_district.py AREAS "central"): the same sheet roofs.
    "central": {"mix": [("M_Roof", 0.95), ("M_RoofSheet_Rust", 0.03), ("M_RoofTile_Red", 0.02)], "pitched": 0.97, "pitch": 12,
                "shed": 0.95, "shed_pitch": 5, "shed_storeys": 3},
    # North Ridge, about 156 roofs: of the 125 pitched, grey 48, brown 29, red 37, painted purple, blue or green 11.
    "cantonments-ridge": {"mix": [("M_Roof", 0.38), ("M_RoofSheet_Rust", 0.23), ("M_RoofTile_Red", 0.30),
                                  ("M_RoofSheet_Painted", 0.09)], "pitched": 0.85, "pitch": 18},
    # Spintex, about 125 roofs: of the 101 not flat or beige, red 40, grey 34, brown 16, painted 11.
    "spintex": {"mix": [("M_Roof", 0.33), ("M_RoofSheet_Rust", 0.16), ("M_RoofTile_Red", 0.40),
                        ("M_RoofSheet_Painted", 0.11)], "pitched": 0.85, "pitch": 18},
    # Adabraka (circle-reference adabraka-identity-survey.md, Street View 24 Nov 2026): sheet on nearly every roof,
    # much of it rusted brown on the old grid, grey or maroon on newer buildings, one painted blue; red clay rare;
    # flat concrete only on the commercial blocks. Low pitches. Not counted from above: estimated, not measured.
    "adabraka": {"mix": [("M_Roof", 0.45), ("M_RoofSheet_Rust", 0.40), ("M_RoofTile_Red", 0.05),
                         ("M_RoofSheet_Painted", 0.10)], "pitched": 0.9, "pitch": 12},
}

WALL_H = 2.25                         # a boundary wall, coping included
GATE_W, GATE_H = 3.2, 2.1             # a steel vehicle gate
VERGE_M = 1.2                         # from the road edge to the compound wall


def h01(*parts):
    """Deterministic number in [0, 1), the same scheme as build_district.unit_hash."""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def pick(choices, r):
    """The first (value, weight) whose cumulative weight passes r."""
    acc = 0.0
    for value, weight in choices:
        acc += weight
        if r < acc:
            return value
    return choices[-1][0]


def _uv(length, z0, z1, flag):
    """The four corners' texture coordinates: the flags only, the same at every corner. District walls have no
    texture, and constant values weld and compress to almost nothing (gltfpack -kv keeps them). The avenue kit's kind
    (the bits from 1024) goes in u instead, as kind * 1000: the packed tiles keep about 11 bits of mantissa, so a v of
    millions came back a thousand or two off; every other wall keeps u = 0, as before."""
    f = 1000.0 * (flag & 1023)
    k = 1000.0 * (flag >> 10)
    return ((k, f), (k, f), (k, f), (k, f))


def walls(mesh, obj, mat, poly, z0, z1, flag=0):
    """The vertical faces of a footprint extruded from z0 to z1 (rings as shapely orients them: CCW outside)."""
    for ring in [poly.exterior, *poly.interiors]:
        pts = list(ring.coords)[:-1]
        for i in range(len(pts)):
            (ae, an), (be, bn) = pts[i], pts[(i + 1) % len(pts)]
            edge = math.hypot(be - ae, bn - an)
            if edge < 0.05:
                continue
            mesh.quad(obj, mat, (ae, an, z0), (be, bn, z0), (be, bn, z1), (ae, an, z1), uv=_uv(edge, z0, z1, flag))


def cap(mesh, obj, mat, poly, z, down=False):
    """A horizontal polygon at height z, facing up (a roof) or down (a soffit)."""
    rings = [poly.exterior, *poly.interiors]
    verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
    ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
    idx = earcut.triangulate_float64(verts, ends)
    for k in range(0, len(idx), 3):
        a, b, c = (tuple(verts[idx[k + j]]) for j in range(3))
        ccw = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) > 0
        if ccw == down:
            b, c = c, b
        mesh.tri(obj, mat, (*a, z), (*b, z), (*c, z))


def slab(mesh, obj, mat, a, b, t, z0, z1, flag=0, ends=(True, True), top=True):
    """A straight wall of thickness t from plan point a to b: both faces, the top, and the chosen ends."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    d = b - a
    L = float(np.hypot(*d))
    if L < 0.05:
        return
    u = d / L
    n = np.array([u[1], -u[0]]) * (t / 2)          # to the right of a -> b
    ar, br, al, bl = a + n, b + n, a - n, b - n
    uv = _uv(L, z0, z1, flag)
    mesh.quad(obj, mat, (*ar, z0), (*br, z0), (*br, z1), (*ar, z1), uv=uv)
    mesh.quad(obj, mat, (*bl, z0), (*al, z0), (*al, z1), (*bl, z1), uv=uv)
    if top:
        mesh.quad(obj, mat, (*ar, z1), (*br, z1), (*bl, z1), (*al, z1))
    uv_end = _uv(t, z0, z1, flag)
    if ends[0]:
        mesh.quad(obj, mat, (*al, z0), (*ar, z0), (*ar, z1), (*al, z1), uv=uv_end)
    if ends[1]:
        mesh.quad(obj, mat, (*br, z0), (*bl, z0), (*bl, z1), (*br, z1), uv=uv_end)


def post(mesh, obj, mat, c, size, z0, z1, flag=NO_OPENINGS):
    """A square pillar centred on plan point c."""
    c = np.asarray(c, dtype=float)
    slab(mesh, obj, mat, c - (size / 2, 0), c + (size / 2, 0), size, z0, z1, flag)


def frame_axes(poly):
    """The footprint's best-fit rectangle: centre, long axis u, short axis v (left of u), long and short sides."""
    rect = poly.minimum_rotated_rectangle
    if not isinstance(rect, Polygon) or rect.area <= 0:
        return None
    c = np.array(list(rect.exterior.coords)[:4])
    e0, e1 = c[1] - c[0], c[2] - c[1]
    l0, l1 = float(np.hypot(*e0)), float(np.hypot(*e1))
    u = e0 / l0 if l0 >= l1 else e1 / l1
    v = np.array([-u[1], u[0]])
    return np.array(rect.centroid.coords[0]), u, v, max(l0, l1), min(l0, l1), rect


def hip_roof(mesh, obj, roof_mat, wall_mat, poly, z0, pitch_deg, overhang=0.5):
    """A hipped roof over the footprint's best-fit rectangle, with a soffit under the overhang and a fascia."""
    o, u, v, long_side, short_side, _ = frame_axes(poly)
    Lu, Lv = long_side + 2 * overhang, short_side + 2 * overhang
    rise = min(2.4, math.tan(math.radians(pitch_deg)) * Lv / 2)
    z1 = z0 + rise
    A, B = o - u * Lu / 2 - v * Lv / 2, o + u * Lu / 2 - v * Lv / 2
    C, D = o + u * Lu / 2 + v * Lv / 2, o - u * Lu / 2 + v * Lv / 2
    half = max(0.0, Lu / 2 - Lv / 2)
    q0, q1 = o - u * half, o + u * half
    mesh.quad(obj, roof_mat, (*A, z0), (*B, z0), (*q1, z1), (*q0, z1))      # the long slopes
    mesh.quad(obj, roof_mat, (*C, z0), (*D, z0), (*q0, z1), (*q1, z1))
    mesh.tri(obj, roof_mat, (*B, z0), (*C, z0), (*q1, z1))                  # the hips
    mesh.tri(obj, roof_mat, (*D, z0), (*A, z0), (*q0, z1))
    cap(mesh, obj, wall_mat, Polygon([A, B, C, D]), z0, down=True)          # soffit
    for P, Q in ((A, B), (B, C), (C, D), (D, A)):                          # fascia board
        mesh.quad(obj, wall_mat, (*P, z0 - 0.2), (*Q, z0 - 0.2), (*Q, z0), (*P, z0),
                  uv=_uv(float(np.hypot(*(Q - P))), z0 - 0.2, z0, NO_OPENINGS))
    return z1


def shed_roof(mesh, obj, roof_mat, wall_mat, poly, z0, pitch_deg, flags=0, overhang=0.3):
    """A single-slope sheet roof over any footprint: a plane that rises across the best-fit rectangle's short side
    (at most 1.5 m), 0.3 m over the walls, with its underside, a fascia, and the wall infill up to the plane."""
    axes = frame_axes(poly)
    if axes is None:
        return None
    o, u, v, _, Lv, _ = axes
    grade = min(math.tan(math.radians(pitch_deg)), 1.5 / max(Lv + 2 * overhang, 1.0))
    z = lambda p: z0 + 0.15 + grade * (float(np.dot(np.asarray(p[:2]) - o, v)) + Lv / 2 + overhang)
    foot = poly.buffer(overhang, join_style="mitre", mitre_limit=2.0).simplify(0.05)
    if not isinstance(foot, Polygon) or foot.is_empty:
        foot = poly
    foot = shapely.geometry.polygon.orient(foot, 1.0)
    rings = [foot.exterior, *foot.interiors]
    verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
    ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
    idx = earcut.triangulate_float64(verts, ends)
    for k in range(0, len(idx), 3):
        a, b, c = (tuple(verts[idx[k + j]]) for j in range(3))
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
            b, c = c, b
        mesh.tri(obj, roof_mat, (*a, z(a)), (*b, z(b)), (*c, z(c)))         # the sheet, facing up
        mesh.tri(obj, wall_mat, (*a, z(a) - 0.05), (*c, z(c) - 0.05), (*b, z(b) - 0.05))   # its underside
    for ring in rings:                                                       # fascia board along the edge
        pts = list(ring.coords)[:-1]
        for i in range(len(pts)):
            P, Q = pts[i], pts[(i + 1) % len(pts)]
            L = math.hypot(Q[0] - P[0], Q[1] - P[1])
            if L >= 0.05:
                mesh.quad(obj, wall_mat, (*P, z(P) - 0.15), (*Q, z(Q) - 0.15), (*Q, z(Q)), (*P, z(P)),
                          uv=_uv(L, 0, 0, NO_OPENINGS))
    for ring in [poly.exterior, *poly.interiors]:                            # walls up to the sloping plane
        pts = list(ring.coords)[:-1]
        for i in range(len(pts)):
            P, Q = pts[i], pts[(i + 1) % len(pts)]
            L = math.hypot(Q[0] - P[0], Q[1] - P[1])
            if L >= 0.05:
                mesh.quad(obj, wall_mat, (*P, z0), (*Q, z0), (*Q, z(Q) - 0.05), (*P, z(P) - 0.05),
                          uv=_uv(L, 0, 0, flags | NO_OPENINGS | NO_PARAPET))
    return z0 + 0.15 + grade * (Lv + 2 * overhang)


def pool(mesh, obj, poly, counts):
    """A swimming pool the satellite saw (imagery.py) where Overture had a building: the water just above the ground
    in M_Pool and a 0.5 m concrete coping around it, flat (the game lays it on the relief with the yard)."""
    poly = shapely.geometry.polygon.orient(poly.simplify(0.1), 1.0)
    rim = poly.buffer(0.5, join_style="mitre", mitre_limit=2.0).difference(poly)
    for part in (rim.geoms if hasattr(rim, "geoms") else [rim]):
        if isinstance(part, Polygon) and not part.is_empty:
            cap(mesh, obj, "M_Concrete_Raw", shapely.geometry.polygon.orient(part, 1.0), 0.06)
    cap(mesh, obj, "M_Pool", poly, 0.03)
    counts["pools"] += 1


def bulkhead(mesh, obj, wall_mat, roof_mat, poly, z, seed, flags=0):
    """A stair bulkhead on a flat roof, 3.4 x 2.6 m and 2.6 m high, in a corner of the roof that holds it."""
    axes = frame_axes(poly)
    if axes is None:
        return False
    o, u, v, Lu, Lv, _ = axes
    bw, bdp, hgt = 3.4, 2.6, 2.6
    if Lu < bw + 1.6 or Lv < bdp + 1.6:
        return False
    inner = poly.buffer(-0.3)
    corners = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
    k = int(seed * 4)
    for su, sv in corners[k:] + corners[:k]:
        c = o + u * su * (Lu / 2 - bw / 2 - 0.8) + v * sv * (Lv / 2 - bdp / 2 - 0.8)
        pts = [c - u * bw / 2 - v * bdp / 2, c + u * bw / 2 - v * bdp / 2, c + u * bw / 2 + v * bdp / 2, c - u * bw / 2 + v * bdp / 2]
        foot = shapely.geometry.polygon.orient(Polygon(pts), 1.0)
        if inner.contains(foot):
            walls(mesh, obj, wall_mat, foot, z, z + hgt, flags | NO_PARAPET)
            cap(mesh, obj, roof_mat, foot, z + hgt)
            return True
    return False


def frame_storey(mesh, obj, poly, z, hgt=2.9):
    """An unfinished storey: bare concrete columns at the corners and about every 4 m, 0.3 m inside the walls."""
    ring = poly.buffer(-0.35, join_style="mitre")
    if not isinstance(ring, Polygon) or ring.is_empty:
        return 0
    pts = np.array(list(ring.exterior.coords)[:-1])
    n = 0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        L = float(np.hypot(*(b - a)))
        steps = max(1, int(round(L / 4.0)))
        for s in range(steps):
            c = a + (b - a) * s / steps
            slab(mesh, obj, "M_Concrete_Raw", c - (0.15, 0), c + (0.15, 0), 0.3, z, z + hgt, NO_OPENINGS)
            n += 1
    return n


# The roofs the satellite sees as never flat (imagery.py): clay tile, rusted and painted sheet.
PITCHED_ROOFS = ("M_RoofSheet_Rust", "M_RoofTile_Red", "M_RoofSheet_Painted")


def building(mesh, obj, wall_mat, bid, poly, n_storeys, parapet, near_oxford, yard, counts, district="osu", seen=None,
             extra=0):
    """One building's massing: a house, a frame or a block. yard: it stands in a walled compound. district picks the
    roof palette (ROOF_PALETTES; Osu's mix when it has none). seen: the roof material the satellite saw (imagery.py),
    or None: it replaces the draw from the palette, and a clay tile, rusted or painted sheet roof is always pitched
    (hipped where the footprint allows, else a low single-slope sheet over the whole footprint). extra: typology flags
    (SHOPFRONT, BALCONIES) on every wall; a storey building (SHOPFRONT) keeps a flat roof whatever its palette."""
    palette = ROOF_PALETTES.get(district)
    pitched_seen = seen in PITCHED_ROOFS and n_storeys <= 3 and not near_oxford
    base = (YARD if yard else 0) | extra
    flat_only = bool(extra & SHOPFRONT)
    if flat_only:
        pitched_seen = False
    area = poly.area
    eaves = n_storeys * STOREY_M
    axes = frame_axes(poly)
    rect_fit = poly.area / axes[5].area if axes else 0.0
    # Houses: hipped roofs where the footprint is near a rectangle and narrow enough to span.
    if (not flat_only and n_storeys <= 2 and not near_oxford and 30 <= area <= 400 and axes and rect_fit >= 0.8
            and 4.0 <= axes[4] <= 15.0 and axes[3] <= 40.0
            and (pitched_seen or h01(bid, "hip") < (palette["pitched"] if palette else 0.85))):
        r = h01(bid, "roof")
        if seen:
            roof = seen
            counts["roofs_seen"] += 1
        elif palette:
            roof = pick(palette["mix"], r)
        elif n_storeys == 1:
            roof = pick([("M_Roof", 0.5), ("M_RoofSheet_Rust", 0.4), ("M_RoofTile_Red", 0.1)], r)
        else:
            roof = pick([("M_Roof", 0.35), ("M_RoofSheet_Rust", 0.2), ("M_RoofTile_Red", 0.45)], r)
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, base | NO_PARAPET)
        hip_roof(mesh, obj, roof, wall_mat, poly, eaves, 28 if roof == "M_RoofTile_Red" else (palette["pitch"] if palette else 18))
        counts["hip_roofs"] += 1
        counts[f"roof {roof}"] += 1
        return "house"
    # A pitched roof seen from above on a footprint no hip spans (an L-shaped villa): a low sheet over all of it.
    if pitched_seen and area >= 12 and axes:
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, base | NO_PARAPET)
        if shed_roof(mesh, obj, seen, wall_mat, poly, eaves, 12, base) is not None:
            counts["shed_roofs"] += 1
            counts["roofs_seen"] += 1
            counts[f"roof {seen}"] += 1
            return "house"
    # Districts of sheet roofs (Jamestown): the other low buildings, whatever their footprint, take a shed roof.
    if (not flat_only and palette and palette.get("shed") and n_storeys <= palette.get("shed_storeys", 2) and not near_oxford and area >= 12 and axes
            and h01(bid, "shed") < palette["shed"]):
        roof = pick(palette["mix"], h01(bid, "roof"))
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, base | NO_PARAPET)
        if shed_roof(mesh, obj, roof, wall_mat, poly, eaves, palette.get("shed_pitch", 5), base) is not None:
            counts["shed_roofs"] += 1
            counts[f"roof {roof}"] += 1
            return "house"
    top = eaves + parapet
    # An unfinished storey on a finished block, away from Oxford Street.
    # (Not on a storey building: its waiting floor is the game's rebar on the parapet, and bare concrete columns would put
    # M_Concrete_Raw into most cells of the old centre, a draw per pass for every tile.)
    if n_storeys >= 2 and not near_oxford and not flat_only and area >= 60 and h01(bid, "frame") < 0.1:
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, base | NO_PARAPET)
        cap(mesh, obj, "M_Roof", poly, eaves)
        counts["frame_columns"] += frame_storey(mesh, obj, poly, eaves)
        counts["frames"] += 1
        return "frame"
    # Tall blocks: the top storey set back 2 m behind a terrace and its parapet.
    if n_storeys >= 4 and area >= 180 and h01(bid, "setback") < 0.5:
        upper = poly.buffer(-2.0, join_style="mitre")
        if isinstance(upper, Polygon) and not upper.is_empty and upper.area >= 0.45 * area:
            upper = shapely.geometry.polygon.orient(upper.simplify(0.1), 1.0)
            lower = (n_storeys - 1) * STOREY_M + parapet
            walls(mesh, obj, wall_mat, poly, 0.0, lower, base)
            cap(mesh, obj, "M_Roof", poly, lower)
            walls(mesh, obj, wall_mat, upper, lower, top, base)
            cap(mesh, obj, "M_Roof", upper, top)
            counts["setbacks"] += 1
            if h01(bid, "stair") < 0.65 and bulkhead(mesh, obj, wall_mat, "M_Roof", upper, top, h01(bid, "corner"), base):
                counts["bulkheads"] += 1
            return "block"
    walls(mesh, obj, wall_mat, poly, 0.0, top, base)
    cap(mesh, obj, "M_Roof", poly, top)
    if n_storeys >= 2 and area >= 70 and h01(bid, "stair") < 0.65 and bulkhead(mesh, obj, wall_mat, "M_Roof", poly, top, h01(bid, "corner"), base):
        counts["bulkheads"] += 1
    return "block"


def compounds(mesh, cell_name, yards, keep_out, roads, road_width, counts, district, avoid=None, gates_out=None, paints=None, gate_mat="M_Gate", fancy=None):
    """Compound walls along the residential streets, with a gate towards each yard's house.

    The walls follow the street frontage of the city blocks, VERGE_M back from the road edge, wherever a yard
    building stands within 25 m behind it. They stop where a shop or block fronts the street, where a
    building reaches the frontage itself, and near `avoid` (Oxford Street's line, its shops face the road).
    yards: (bid, footprint) of the buildings in compounds; keep_out: every other footprint, look-dev ones
    included; roads: (class, line); district: the district's outline.
    Returns the walls' lines, gates included (the gates stand closed), for the game's collisions. gates_out, a list,
    receives each gate built as (x, y) of its middle, for the game's planting by the gates (circle-game
    src/world/vegetation.ts). paints: the walls' (material, weight), BOUNDARY_PAINTS by default; a district with a paint
    list of its own (the old centre, where only the avenue's villas stand in yards) passes its own, and gate_mat the grey
    sheet (M_Roof) for the gates, so its cells gain no material. fancy: the footprints of yards whose walls and gates
    are a villa's (the avenue's kit): a coping along the wall's top, and gate pillars with a cap and a lamp on it.
    """
    if not yards:
        return []
    corridors = shapely.union_all([line.buffer(road_width[cls] / 2 + VERGE_M, cap_style="flat") for cls, line in roads])
    blocks = district.difference(corridors)
    frontage = shapely.boundary(blocks)
    houses = shapely.union_all([p for _, p in yards])
    lines = frontage.intersection(houses.buffer(25.0)).difference(houses.buffer(0.35))
    if keep_out:
        lines = lines.difference(shapely.union_all(keep_out).buffer(3.0))
    if avoid is not None:
        lines = lines.difference(avoid.buffer(35.0))
    lines = lines.simplify(0.25)
    pieces = [g for g in getattr(lines, "geoms", [lines]) if isinstance(g, LineString) and g.length >= 2.0]
    if not pieces:
        return []
    network = MultiLineString(pieces)
    # Gates: the frontage point nearest each yard house, at most 30 m away, no two within 8 m.
    gates = []
    fancy_zone = shapely.union_all(list(fancy)).buffer(30.0) if fancy else None
    if fancy_zone is not None:
        shapely.prepare(fancy_zone)
    for bid, foot in yards:
        g = shapely.ops.nearest_points(network, foot.centroid)[0]
        if g.distance(foot) > 30.0 or any(g.distance(o) < 8.0 for o in gates):
            continue
        gates.append(g)
    for i, piece in enumerate(pieces):
        start = piece.coords[0]
        paint = pick(paints or BOUNDARY_PAINTS, h01("wall", round(start[0], 1), round(start[1], 1)))
        cuts = sorted(piece.project(g) for g in gates if g.distance(piece) < 0.3)
        cuts = [s for s in cuts if GATE_W / 2 + 0.3 < s < piece.length - GATE_W / 2 - 0.3]
        spans, s0 = [], 0.0
        for s in cuts:
            spans.append((s0, s - GATE_W / 2))
            s0 = s + GATE_W / 2
        spans.append((s0, piece.length))
        for a0, a1 in spans:
            if a1 - a0 < 0.3:
                continue
            run = shapely.ops.substring(piece, a0, a1)
            pts = np.array(run.coords)
            for k in range(len(pts) - 1):
                p, q = pts[k], pts[k + 1]
                obj = cell_name("Buildings", (p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                first, last = k == 0, k == len(pts) - 2
                slab(mesh, obj, paint, p, q, 0.25, 0.0, WALL_H, NO_OPENINGS, ends=(first, last))
                if fancy_zone is not None and fancy_zone.contains(Point((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)):
                    # A villa's wall: a coping 0.12 m proud each side along its top.
                    slab(mesh, obj, paint, p, q, 0.49, WALL_H, WALL_H + 0.1, NO_OPENINGS, ends=(first, last))
                    counts["coped wall m"] += float(np.hypot(*(q - p)))
            counts["compound_wall_m"] += a1 - a0
        for s in cuts:
            c = np.array(piece.interpolate(s).coords[0])
            a = np.array(piece.interpolate(max(0.0, s - 0.5)).coords[0])
            b = np.array(piece.interpolate(min(piece.length, s + 0.5)).coords[0])
            t = (b - a) / max(1e-6, float(np.hypot(*(b - a))))
            obj = cell_name("Buildings", c[0], c[1])
            slab(mesh, obj, gate_mat, c - t * GATE_W / 2, c + t * GATE_W / 2, 0.06, 0.0, GATE_H)
            for side in (-1, 1):
                post(mesh, obj, paint, c + t * side * (GATE_W / 2 + 0.22), 0.44, 0.0, WALL_H + 0.35)
                if fancy_zone is not None and fancy_zone.contains(Point(*c)):
                    # A villa's gate pillar: wider, a cap, and a lamp on the cap (a lantern on a short post).
                    pc = c + t * side * (GATE_W / 2 + 0.22)
                    post(mesh, obj, paint, pc, 0.62, 0.0, WALL_H + 0.55)
                    post(mesh, obj, paint, pc, 0.78, WALL_H + 0.55, WALL_H + 0.67)
                    post(mesh, obj, "M_Roof", pc, 0.08, WALL_H + 0.67, WALL_H + 0.95)
                    post(mesh, obj, paint, pc, 0.26, WALL_H + 0.95, WALL_H + 1.3, NO_OPENINGS)
                    counts["gate lamps"] += 1
            counts["gates"] += 1
            if gates_out is not None:
                gates_out.append((float(c[0]), float(c[1])))
    return pieces


# ---------------------------------------------------------------------------------------------- typologies
# The builders typology.py picks (19 Nov 2026). They use the district's materials only: the wall paints (M_Wall_*),
# the roofs (M_Roof, M_RoofSheet_*, M_RoofTile_Red) and M_Concrete_Raw. Variety comes from the flags, the proportions
# and the game's per-plane paint, never from a material of their own (each is a render pipeline in the game).
# Sheet roofs for the typologies, from the two sheet materials every district already draws (grey and rusted): the
# painted sheet appears in few districts, and a material new to a cell is one more draw in each of the game's passes
# for every tile that holds it (the bench of 20 Nov 2026: 20 new cells per area, +8 % render objects).
SHEETS = [("M_Roof", 0.6), ("M_RoofSheet_Rust", 0.4)]


def tri_out(mesh, obj, mat, a, b, c, out, uv=None):
    """A triangle facing `out` (a direction), whatever the order its corners come in."""
    n = np.cross(np.subtract(b, a), np.subtract(c, a))
    if float(np.dot(n, out)) < 0:
        b, c = c, b
    mesh.tri(obj, mat, tuple(a), tuple(b), tuple(c), None if uv is None else tuple(uv[:3]))


def quad_out(mesh, obj, mat, a, b, c, d, out, uv=None):
    """A quad (a, b, c, d around it) facing `out`."""
    n = np.cross(np.subtract(b, a), np.subtract(c, a))
    if float(np.dot(n, out)) < 0:
        a, b, c, d = a, d, c, b
    mesh.quad(obj, mat, tuple(a), tuple(b), tuple(c), tuple(d), uv)


def gable_roof(mesh, obj, roof_mat, end_mat, poly, z0, pitch_deg, overhang=0.4, max_rise=6.0, end_flag=NO_OPENINGS):
    """A gable roof over the footprint's best-fit rectangle: two slopes meeting on a ridge along the long axis, the
    gable ends (triangles in `end_mat` over the end walls) and the soffit. Returns the ridge's height."""
    o, u, v, Lu, Lv, _ = frame_axes(poly)
    ou, ov = Lu / 2 + overhang, Lv / 2 + overhang
    rise = min(max_rise, math.tan(math.radians(pitch_deg)) * ov)
    z1 = z0 + rise
    P = lambda a, b, z: (*(o + u * a + v * b), z)
    up = (0, 0, 1)
    for s in (-1, 1):                                                       # the slopes
        quad_out(mesh, obj, roof_mat, P(-ou, s * ov, z0), P(ou, s * ov, z0), P(ou, 0, z1), P(-ou, 0, z1),
                 (*(v * s), 1.0))
        quad_out(mesh, obj, end_mat, P(-ou, s * ov, z0 - 0.02), P(ou, s * ov, z0 - 0.02), P(ou, 0, z1 - 0.02),
                 P(-ou, 0, z1 - 0.02), (*(-v * s), -1.0))                   # their underside
    inner = rise * (Lv / 2) / ov                                            # the rise over the wall line
    for s in (-1, 1):                                                       # the gable ends, on the end walls
        tri_out(mesh, obj, end_mat, P(s * Lu / 2, -Lv / 2, z0), P(s * Lu / 2, Lv / 2, z0), P(s * Lu / 2, 0, z0 + inner),
                (*(u * s), 0.0), uv=_uv(Lv, z0, z1, end_flag | NO_PARAPET))
    return z1


def cross(mesh, obj, mat, c, along, z, size=1.6):
    """A cross standing at plan point c (its arms along `along`), its foot at height z."""
    c, along = np.asarray(c, dtype=float), np.asarray(along, dtype=float)
    slab(mesh, obj, mat, c - along * 0.06, c + along * 0.06, 0.12, z, z + size, NO_OPENINGS)
    slab(mesh, obj, mat, c - along * size * 0.32, c + along * size * 0.32, 0.1, z + size * 0.6, z + size * 0.72, NO_OPENINGS)


def prism(mesh, obj, mat, c, r0, r1, z0, z1, sides=8, flag=NO_OPENINGS, top=None, phase=0.0):
    """A vertical prism (or frustum) of `sides` sides around plan point c, from radius r0 at z0 to r1 at z1; `top`, a
    material for its top face, or None for an open top."""
    pts = lambda r, z: [(c[0] + r * math.cos(phase + 2 * math.pi * k / sides), c[1] + r * math.sin(phase + 2 * math.pi * k / sides), z)
                        for k in range(sides)]
    a, b = pts(r0, z0), pts(r1, z1)
    for k in range(sides):
        k2 = (k + 1) % sides
        out = (math.cos(phase + 2 * math.pi * (k + 0.5) / sides), math.sin(phase + 2 * math.pi * (k + 0.5) / sides), 0.0)
        quad_out(mesh, obj, mat, a[k], a[k2], b[k2], b[k], out, uv=_uv(1, z0, z1, flag))
    if top:
        for k in range(1, sides - 1):
            tri_out(mesh, obj, top, b[0], b[k], b[k + 1], (0, 0, 1))


def dome(mesh, obj, mat, c, r, z, rings=4, sides=12):
    """A hemispherical dome of radius r on plan point c, its foot at height z, with a finial."""
    prev = [(c[0] + r * math.cos(2 * math.pi * k / sides), c[1] + r * math.sin(2 * math.pi * k / sides), z) for k in range(sides)]
    for i in range(1, rings + 1):
        a = (math.pi / 2) * i / rings
        if i == rings:
            apex = (c[0], c[1], z + r)
            for k in range(sides):
                tri_out(mesh, obj, mat, prev[k], prev[(k + 1) % sides], apex, np.subtract(apex, (c[0], c[1], z)) + np.subtract(prev[k], (c[0], c[1], z)))
            break
        rr, zz = r * math.cos(a), z + r * math.sin(a)
        cur = [(c[0] + rr * math.cos(2 * math.pi * k / sides), c[1] + rr * math.sin(2 * math.pi * k / sides), zz) for k in range(sides)]
        for k in range(sides):
            k2 = (k + 1) % sides
            mid = np.mean([prev[k], prev[k2], cur[k2], cur[k]], axis=0) - (c[0], c[1], z)
            quad_out(mesh, obj, mat, prev[k], prev[k2], cur[k2], cur[k], mid)
        prev = cur
    prism(mesh, obj, "M_Concrete_Raw", c, 0.08, 0.02, z + r, z + r + 1.2, sides=4)



def church(mesh, obj, wall_mat, bid, poly, counts, flags=0):
    """A church. Small ones (under 400 m2) the modern A-frame of Accra's newer churches: low side walls and a steep
    sheet roof down to them, its tall gable over the entrance with a cross. Larger ones the classic hall: walls of
    about 7.5 m under a gable of sheet or tile, and on three in four a square bell tower at one end, with a spire and a
    cross. The hall's walls carry the HALL flag: tall windows, no shops."""
    axes = frame_axes(poly)
    if axes is None:
        return None
    o, u, v, Lu, Lv, rect = axes
    fit = poly.area / rect.area
    roof = pick([("M_Roof", 0.45), ("M_RoofSheet_Rust", 0.3), ("M_RoofTile_Red", 0.25)], h01(bid, "church roof"))
    hall = flags | HALL
    if poly.area < 400 and fit >= 0.75 and Lv <= 22:
        eaves = 2.6
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, hall | NO_PARAPET)
        z1 = gable_roof(mesh, obj, roof if roof != "M_RoofTile_Red" else "M_RoofSheet_Rust", wall_mat, poly, eaves, 56,
                        overhang=0.6, max_rise=10.0, end_flag=HALL)
        front = h01(bid, "church front") < 0.5 and -1 or 1
        cross(mesh, obj, "M_Concrete_Raw", o + u * front * (Lu / 2 + 0.6), v, z1 - 0.2)
        counts["churches_aframe"] += 1
        return "church"
    eaves = 7.5 if poly.area < 1500 else 9.0
    walls(mesh, obj, wall_mat, poly, 0.0, eaves, hall | NO_PARAPET)
    if fit >= 0.7 and Lv <= 30:
        z1 = gable_roof(mesh, obj, roof, wall_mat, poly, eaves, 30 if roof == "M_RoofTile_Red" else 22, overhang=0.5,
                        max_rise=7.0, end_flag=HALL)
    else:
        cap(mesh, obj, "M_Roof", poly, eaves)
        z1 = eaves
    if h01(bid, "bell tower") < 0.75 and Lv >= 8:
        side = min(4.5, max(3.2, Lv * 0.3))
        end = -1 if h01(bid, "church front") < 0.5 else 1
        c = o + u * end * (Lu / 2 - side / 2)
        foot = shapely.geometry.polygon.orient(Polygon([c - u * side / 2 - v * side / 2, c + u * side / 2 - v * side / 2,
                                                         c + u * side / 2 + v * side / 2, c - u * side / 2 + v * side / 2]), 1.0)
        top = z1 + 4.0 + 4.0 * h01(bid, "tower h")
        walls(mesh, obj, wall_mat, foot, 0.0, top, hall | NO_PARAPET)
        spire = 3.5 + 3.0 * h01(bid, "spire")
        prism(mesh, obj, roof, c, side * 0.75, 0.05, top, top + spire,
              sides=4, phase=math.atan2(u[1], u[0]) + math.pi / 4)
        cross(mesh, obj, "M_Concrete_Raw", c, v, top + spire - 0.1, 1.4)
        counts["bell_towers"] += 1
    else:
        front = -1 if h01(bid, "church front") < 0.5 else 1
        cross(mesh, obj, "M_Concrete_Raw", o + u * front * (Lu / 2 + 0.5), v, z1 - 0.3)
    counts["churches"] += 1
    return "church"


def mosque(mesh, obj, wall_mat, bid, poly, n_storeys, parapet, counts, flags=0):
    """A mosque: the prayer hall's walls (HALL: tall windows, no shops) under a flat roof and parapet, a dome on a
    drum over its middle, and a minaret at one corner of the footprint: an octagonal shaft with a balcony and a pointed
    cap, its height drawn between 16 and 26 m and at least 7 m over the roof."""
    axes = frame_axes(poly)
    if axes is None:
        return None
    o, u, v, Lu, Lv, _ = axes
    n = max(1, min(2, n_storeys))
    top = n * STOREY_M + 1.2 + parapet
    walls(mesh, obj, wall_mat, poly, 0.0, top, flags | HALL)
    cap(mesh, obj, "M_Roof", poly, top)
    inner = poly.buffer(-1.0)
    # The dome over the hall's middle (its widest circle inside the footprint when the middle is outside it), as wide
    # as fits up to a third of the short side and 7 m.
    c = o if poly.contains(Point(*o)) else np.array(poly.representative_point().coords[0])
    r = min(Lv * 0.32, 7.0)
    while r >= 1.8 and not inner.contains(Point(*c).buffer(r)):
        r *= 0.8
    if r >= 1.8:
        prism(mesh, obj, "M_Concrete_Raw", c, r, r, top, top + 0.9, sides=12)
        dome(mesh, obj, "M_RoofSheet_Painted", c, r, top + 0.9)
        counts["domes"] += 1
    # The minaret at a corner of the best-fit rectangle that lies on the footprint, the drawn one first.
    k = int(h01(bid, "minaret") * 4)
    corners = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
    held = poly.buffer(-0.2)
    m = next((p for su, sv in corners[k:] + corners[:k]
              for p in [o + u * su * (Lu / 2 - 1.3) + v * sv * (Lv / 2 - 1.3)] if held.contains(Point(*p))), None)
    if m is not None and (r < 1.8 or float(np.hypot(*(m - c))) > r + 1.2):
        h = max(top + 7.0, 16.0 + 10.0 * h01(bid, "minaret h"))
        prism(mesh, obj, "M_Concrete_Raw", m, 1.1, 0.95, top, h * 0.78)
        prism(mesh, obj, "M_Concrete_Raw", m, 1.6, 1.6, h * 0.78, h * 0.78 + 0.35, top="M_Concrete_Raw")
        prism(mesh, obj, "M_Concrete_Raw", m, 0.8, 0.75, h * 0.78 + 0.35, h)
        prism(mesh, obj, "M_RoofSheet_Painted", m, 0.95, 0.03, h, h + 2.6)
        counts["minarets"] += 1
    counts["mosques"] += 1
    return "mosque"


def warehouse(mesh, obj, wall_mat, bid, poly, cls, counts, flags=0):
    """A warehouse or a big box. Sheds (warehouse, industrial...) on a footprint close to a rectangle: a block plinth
    of 2.4 m, corrugated sheet cladding above it to eaves of 5.5 to 8 m, a low gable of sheet and one or two roller
    doors on a long side. Retail boxes and footprints of any shape: blank painted walls of 7 to 8 m and a flat roof
    behind a parapet. No windows, no shops (NO_OPENINGS)."""
    axes = frame_axes(poly)
    if axes is None:
        return None
    o, u, v, Lu, Lv, rect = axes
    fit = poly.area / rect.area
    eaves = 5.5 + min(2.5, poly.area / 1500.0)
    retail = (cls or "") in ("retail", "supermarket", "commercial")
    if not retail and fit >= 0.75 and Lv <= 60:
        sheet = pick(SHEETS, h01(bid, "shed sheet"))
        walls(mesh, obj, wall_mat, poly, 0.0, 2.4, flags | NO_OPENINGS | NO_PARAPET)
        walls(mesh, obj, sheet, poly, 2.4, eaves, 0)
        gable_roof(mesh, obj, pick(SHEETS, h01(bid, "shed roof")), sheet, poly, eaves, 8, overhang=0.3, max_rise=3.0)
        for s, k in ((-1, 0), (-1, 1)):                                      # roller doors on the long side
            if k and Lu < 30:
                break
            a = (-0.25 + 0.5 * k) * Lu if Lu >= 30 else 0.0
            c = o + u * a + v * s * (Lv / 2 + 0.05)
            slab(mesh, obj, "M_RoofSheet_Rust", c - u * 2.2, c + u * 2.2, 0.08, 0.0, 4.6, NO_OPENINGS)
            counts["roller_doors"] += 1
        counts["sheds"] += 1
        return "warehouse"
    top = eaves + 1.2
    walls(mesh, obj, wall_mat, poly, 0.0, top, flags | NO_OPENINGS)
    cap(mesh, obj, "M_Roof", poly, top)
    counts["big_boxes"] += 1
    return "warehouse"


def tower(mesh, obj, wall_mat, bid, poly, n_storeys, counts, flags=0):
    """A glass office tower: its walls a curtain wall (CURTAIN: the game draws mullions, spandrels and glass) to the
    top floor, an opaque crown of 1.5 m above it, a plant room on the roof. On a large footprint (900 m2 or more,
    three in five) the tower stands on a two-storey glass podium of the whole footprint, 3 m in from its edges."""
    n = max(5, n_storeys)
    base = flags | CURTAIN | NO_PARAPET
    shaft, z0 = poly, 0.0
    if poly.area >= 900 and h01(bid, "podium") < 0.6:
        upper = poly.buffer(-3.0, join_style="mitre")
        if isinstance(upper, Polygon) and not upper.is_empty and upper.area >= 0.4 * poly.area:
            z0 = 2 * STOREY_M + 0.6
            walls(mesh, obj, wall_mat, poly, 0.0, z0, base)
            cap(mesh, obj, "M_Roof", poly, z0)
            shaft = shapely.geometry.polygon.orient(upper.simplify(0.1), 1.0)
            counts["podiums"] += 1
    top = n * STOREY_M + 0.6
    walls(mesh, obj, wall_mat, shaft, z0, top, base)
    walls(mesh, obj, wall_mat, shaft, top, top + 1.5, NO_OPENINGS | NO_PARAPET)
    cap(mesh, obj, "M_Roof", shaft, top + 1.5)
    bulkhead(mesh, obj, wall_mat, "M_Roof", shaft, top + 1.5, h01(bid, "corner"), NO_OPENINGS | NO_PARAPET)
    counts["towers"] += 1
    return "tower"


def kiosk(mesh, obj, wall_mat, bid, poly, counts):
    """A kiosk or a container shop on a verge: walls of 2.45 m (under the game's dressing, which leaves walls under
    2.5 m alone; its wall material still draws the shop opening) and a thin sheet roof falling to one side with
    a 0.5 m overhang over the counter."""
    walls(mesh, obj, wall_mat, poly, 0.0, 2.45, NO_PARAPET)
    # Grey sheet, the one roof material every district draws: kiosks stand in nearly every cell.
    if shed_roof(mesh, obj, "M_Roof", wall_mat, poly, 2.45, 4, NO_PARAPET, overhang=0.5) is None:
        cap(mesh, obj, "M_Roof", poly, 2.45)
    counts["kiosks"] += 1
    return "kiosk"


# ---------------------------------------------------------------------------------------------- the avenue's kit
# The builders of the Independence Avenue kit (avenue_kit.py; circle-game docs/independence-avenue-plan.md, Appendix
# C). As kit2's, they use only the materials the district already draws (wall paints, the grey and rusted sheet, red
# tile, gates), so no cell gains a draw for a material new to it; the kind shows in the proportions, the roofs, the
# parts (fins, colonnades, verandas, canopies, open decks) and the wall flags the game reads.


def _rect(o, u, v, a0, a1, b0, b1):
    """The rectangle [a0, a1] along u by [b0, b1] along v around o, counter-clockwise."""
    return shapely.geometry.polygon.orient(Polygon([o + u * a0 + v * b0, o + u * a1 + v * b0, o + u * a1 + v * b1,
                                                    o + u * a0 + v * b1]), 1.0)


def _front(axes, facing):
    """The footprint's long side towards the road: +1 or -1 along v (facing: the plan vector to the road, or None)."""
    _, u, v, *_ = axes
    if facing is None:
        return 1
    return 1 if float(np.dot(v, facing)) >= 0 else -1


def _canopy(mesh, obj, mat, o, u, v, side, half_v, width, depth, z, posts=2, post_mat=None, flag=NO_OPENINGS):
    """A flat concrete canopy slab on the long side `side` (along v) at height z, `width` along u and `depth` out from
    the wall, 0.2 m thick, on `posts` square posts at its outer edge (0 for a cantilever)."""
    foot = _rect(o, u, v, -width / 2, width / 2, *sorted((side * half_v, side * (half_v + depth))))
    walls(mesh, obj, mat, foot, z, z + 0.2, flag | NO_PARAPET)
    cap(mesh, obj, mat, foot, z + 0.2)
    cap(mesh, obj, mat, foot, z, down=True)
    for k in range(posts):
        a = -width / 2 + 0.3 + (width - 0.6) * (k / max(1, posts - 1))
        post(mesh, obj, post_mat or mat, o + u * a + v * side * (half_v + depth - 0.3), 0.35, 0.0, z, flag)


def _corner_glass(mesh, obj, mat, o, u, v, Lu, Lv, side, top, size=4.5):
    """A glazed corner: a box of curtain wall (CURTAIN) at the corner of the footprint's best-fit rectangle on the side
    `side`, 0.12 m proud of the walls, from the ground to `top`: the corner of a bank branch on its street."""
    a0, a1 = Lu / 2 - size, Lu / 2 + 0.12
    b0, b1 = sorted((side * (Lv / 2 - size), side * (Lv / 2 + 0.12)))
    foot = _rect(o, u, v, a0, a1, b0, b1)
    walls(mesh, obj, mat, foot, 0.0, top, CURTAIN | NO_PARAPET)
    cap(mesh, obj, "M_Roof", foot, top)


def _overhang(mesh, obj, mat, poly, z, out=1.2, thick=0.35, flag=NO_OPENINGS):
    """A flat roof slab overhanging the walls by `out`: its top, soffit and edge (no openings)."""
    roof = poly.buffer(out, join_style="mitre", mitre_limit=2.0).simplify(0.1)
    if not isinstance(roof, Polygon) or roof.is_empty:
        return
    roof = shapely.geometry.polygon.orient(roof, 1.0)
    walls(mesh, obj, mat, roof, z, z + thick, flag | NO_PARAPET)
    cap(mesh, obj, "M_Roof", roof, z + thick)
    cap(mesh, obj, mat, roof, z, down=True)


def _coping(mesh, obj, mat, poly, z, out=0.25, thick=0.18, flag=NO_OPENINGS):
    """A coping: a thin slab a little wider than the walls along the top of a parapet."""
    _overhang(mesh, obj, mat, poly, z, out, thick, flag)


def _solid(kind):
    """The flags of a building's solid parts (parapets, copings, canopies, fins, posts): no openings, and the building's
    kind, so the game paints them in its paint rather than Accra's vivid ones."""
    return NO_OPENINGS | OFFICE | kind


def _porch(mesh, obj, wall_mat, roof_mat, o, u, v, Lv, side, width=2.8, depth=3.2, z=2.9):
    """A villa's porch on the side `side`: a small gable roof over two posts, its ridge running out from the wall."""
    b0, b1 = sorted((side * Lv / 2, side * (Lv / 2 + depth)))
    foot = _rect(o, u, v, -width / 2, width / 2, b0, b1)
    for k in (-1, 1):
        post(mesh, obj, wall_mat, o + u * k * (width / 2 - 0.2) + v * side * (Lv / 2 + depth - 0.2), 0.3, 0.0, z)
    # Deeper than wide, so gable_roof's ridge (along the long side) runs out from the wall and the gable faces the road.
    gable_roof(mesh, obj, roof_mat, wall_mat, foot, z, 30, overhang=0.25, max_rise=1.6)


def villa(mesh, obj, wall_mat, bid, poly, n, parapet, counts, palette=None, seen=None, facing=None):
    """T1, a villa behind its wall: one or two storeys under a hipped roof of tile or sheet (the district's palette, or
    the roof the satellite saw), with a gabled porch towards the road on a near-rectangle, a hip-and-gable on larger
    ones (a cross gable over the front half); a low sheet over irregular footprints; a modern flat roof behind a
    coped parapet on one in five. Walls: HOME, YARD and the villa's kind (its own render, fewer and larger windows).
    The caller puts it among the yards: compounds() walls it, gates it with pillars and lamps."""
    flags = YARD | HOME | KIND_VILLA
    n = max(1, min(2, n))
    eaves = n * STOREY_M
    axes = frame_axes(poly)
    fit = poly.area / axes[5].area if axes else 0.0
    modern = h01(bid, "villa flat") < 0.2 and seen not in PITCHED_ROOFS
    if axes and not modern and fit >= 0.75 and 4.0 <= axes[4] <= 18.0 and axes[3] <= 45.0:
        o, u, v, Lu, Lv, rect = axes
        roof = seen or pick((palette or {}).get("mix") or [("M_RoofTile_Red", 0.45), ("M_Roof", 0.35), ("M_RoofSheet_Rust", 0.2)],
                            h01(bid, "roof"))
        if roof == "M_RoofSheet_Painted":
            roof = "M_RoofTile_Red"                     # painted sheet: few cells draw it (kit2's lesson)
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, flags | NO_PARAPET)
        pitch = 28 if roof == "M_RoofTile_Red" else 20
        hip_roof(mesh, obj, roof, wall_mat, poly, eaves, pitch, overhang=0.7)
        side = _front(axes, facing)
        if Lu >= 12.0 and Lv >= 7.0 and h01(bid, "cross gable") < 0.6:
            # Hip and gable: a gabled wing across the front half, its ridge up to the hip's, its gable to the road.
            # Narrower than deep, so its ridge runs out to the road, across the hip's.
            half = min(Lu * 0.18, Lv * 0.2)
            wing = _rect(o, u, v, -half, half, *sorted((0.0, side * (Lv / 2 + 0.3))))
            gable_roof(mesh, obj, roof, wall_mat, wing, eaves, pitch, overhang=0.35, max_rise=2.4)
            counts["villa hip and gable"] += 1
        if Lu >= 8.0:
            _porch(mesh, obj, wall_mat, roof, o + u * (Lu * 0.22 if Lu >= 12.0 else 0.0), u, v, Lv, side)
            counts["villa porches"] += 1
        counts["villa hipped"] += 1
        return "T1"
    if axes and not modern and poly.area >= 12:
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, flags | NO_PARAPET)
        if shed_roof(mesh, obj, pick(SHEETS, h01(bid, "roof")), wall_mat, poly, eaves, 10, flags) is not None:
            counts["villa sheet"] += 1
            return "T1"
    top = eaves + parapet
    walls(mesh, obj, wall_mat, poly, 0.0, top, flags)
    cap(mesh, obj, "M_Roof", poly, top)
    _coping(mesh, obj, wall_mat, poly, top, out=0.12, thick=0.12)
    counts["villa flat"] += 1
    return "T1"


def office(mesh, obj, wall_mat, bid, poly, n, counts, facing=None, porte=False):
    """T2 (and T12 with porte=True), an office or hotel slab of three to eight storeys. On 500 m2 or more and four
    storeys or more: a two-storey podium on the whole footprint, the slab above it 3 m in from the podium's edge, and
    its top floor recessed 1.5 m more behind a terrace; smaller ones a plain slab. The facade: a concrete frame on a
    1.5 m module with glass and spandrels (KIND_OFFICE), or, on two in five offices, glass from floor to floor
    (KIND_GLASS); a hotel the frame. A coping on each roof edge, a plant room, and an entrance canopy on posts
    towards the road (a hotel's porte-cochere, 8 by 6 m)."""
    glass = not porte and h01(bid, "glass office") < 0.4
    kind = OFFICE | (KIND_GLASS if glass else KIND_OFFICE)
    sol = _solid(KIND_OFFICE)
    top = n * STOREY_M
    axes = frame_axes(poly)
    shaft, z0 = poly, 0.0
    if n >= 4 and poly.area >= 500:
        upper = poly.buffer(-3.0, join_style="mitre")
        if isinstance(upper, Polygon) and not upper.is_empty and upper.area >= 0.4 * poly.area:
            z0 = 2 * STOREY_M
            walls(mesh, obj, wall_mat, poly, 0.0, z0, kind)
            walls(mesh, obj, wall_mat, poly, z0, z0 + 0.9, sol)
            cap(mesh, obj, "M_Roof", poly, z0 + 0.9)
            _coping(mesh, obj, wall_mat, poly, z0 + 0.9, flag=sol)
            shaft = shapely.geometry.polygon.orient(upper.simplify(0.1), 1.0)
            counts["office podiums"] += 1
    recess = None
    if n >= 5:
        r = shaft.buffer(-1.5, join_style="mitre")
        if isinstance(r, Polygon) and not r.is_empty and r.area >= 0.5 * shaft.area:
            recess = shapely.geometry.polygon.orient(r.simplify(0.1), 1.0)
    body_top = top - (STOREY_M if recess is not None else 0.0)
    walls(mesh, obj, wall_mat, shaft, z0, body_top, kind)
    if recess is not None:
        walls(mesh, obj, wall_mat, shaft, body_top, body_top + 1.1, sol)
        cap(mesh, obj, "M_Roof", shaft, body_top + 1.1)
        _coping(mesh, obj, wall_mat, shaft, body_top + 1.1, flag=sol)
        walls(mesh, obj, wall_mat, recess, body_top, top, OFFICE | KIND_GLASS | NO_PARAPET)
        _overhang(mesh, obj, wall_mat, recess, top, out=1.0, thick=0.3, flag=sol)
        counts["office recessed tops"] += 1
        roof_poly, roof_z = recess, top + 0.3
    else:
        walls(mesh, obj, wall_mat, shaft, body_top, body_top + 1.1, sol)
        cap(mesh, obj, "M_Roof", shaft, body_top + 1.1)
        _coping(mesh, obj, wall_mat, shaft, body_top + 1.1, flag=sol)
        roof_poly, roof_z = shaft, body_top + 1.1
    bulkhead(mesh, obj, wall_mat, "M_Roof", roof_poly, roof_z, h01(bid, "corner"), sol | NO_PARAPET)
    if axes and axes[3] >= 10:
        o, u, v, Lu, Lv, _ = axes
        side = _front(axes, facing)
        if porte:
            _canopy(mesh, obj, wall_mat, o, u, v, side, Lv / 2, min(8.0, Lu * 0.6), 6.0, 4.2, posts=2, flag=sol)
        else:
            _canopy(mesh, obj, wall_mat, o, u, v, side, Lv / 2, min(6.0, Lu * 0.4), 2.5, 3.3, posts=2, flag=sol)
    counts["office glass" if glass else "office framed"] += 1
    return "T12" if porte else "T2"


def bank(mesh, obj, wall_mat, bid, poly, n, counts, facing=None):
    """T4, a bank branch: one or two storeys, the ground floor glazed (CURTAIN, to 3.6 m) under a panelled storey
    (KIND_BANK: composite or stone panels, a strip window), a glazed corner towards the road from the ground to the
    parapet, a coped parapet, and a canopy along the front."""
    n = max(1, min(2, n))
    sol = _solid(KIND_BANK)
    top = max(4.2, n * STOREY_M + 0.6)
    walls(mesh, obj, wall_mat, poly, 0.0, 3.6, CURTAIN | NO_PARAPET)
    walls(mesh, obj, wall_mat, poly, 3.6, top, OFFICE | KIND_BANK)
    walls(mesh, obj, wall_mat, poly, top, top + 0.9, sol)
    cap(mesh, obj, "M_Roof", poly, top + 0.9)
    _coping(mesh, obj, wall_mat, poly, top + 0.9, flag=sol)
    axes = frame_axes(poly)
    if axes:
        o, u, v, Lu, Lv, _ = axes
        side = _front(axes, facing)
        _canopy(mesh, obj, wall_mat, o, u, v, side, Lv / 2, Lu * 0.7, 1.8, 3.6, posts=0, flag=sol)
        if Lu >= 9.0 and Lv >= 7.0:
            _corner_glass(mesh, obj, wall_mat, o, u, v, Lu, Lv, side, top + 0.9, size=min(4.5, Lv * 0.45))
            counts["bank glazed corners"] += 1
    counts["banks"] += 1
    return "T4"


def ministry(mesh, obj, wall_mat, bid, poly, n, counts):
    """T5, a ministry block of the 1950s to 1970s: a slab of three to six storeys of painted concrete (KIND_MINISTRY:
    narrow windows on a 1.5 m module in deep reveals) on pilotis: the ground floor open under the slab, its core set
    back behind a ring of columns; the facades an egg-crate brise-soleil of vertical fins every 1.5 m and a slab edge
    at every floor standing 0.6 m out; and a flat roof slab overhanging 1.2 m."""
    top = n * STOREY_M
    kind = OFFICE | KIND_MINISTRY
    sol = _solid(KIND_MINISTRY)
    axes = frame_axes(poly)
    setback = min(4.0, (axes[4] if axes else 10.0) * 0.28)
    core = poly.buffer(-setback, join_style="mitre")
    pilotis = isinstance(core, Polygon) and not core.is_empty and core.area >= 0.25 * poly.area and h01(bid, "pilotis") < 0.85
    if pilotis:
        core = shapely.geometry.polygon.orient(core.simplify(0.1), 1.0)
        walls(mesh, obj, wall_mat, core, 0.0, STOREY_M, kind | NO_PARAPET)
        cap(mesh, obj, wall_mat, poly, STOREY_M, down=True)
        ring = np.array(list(poly.buffer(-0.45, join_style="mitre").exterior.coords)[:-1])
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            L = float(np.hypot(*(b - a)))
            k = max(1, int(round(L / 4.5)))
            for s in range(k):
                post(mesh, obj, wall_mat, a + (b - a) * s / k, 0.55, 0.0, STOREY_M, sol)
                counts["ministry pilotis"] += 1
        walls(mesh, obj, wall_mat, poly, STOREY_M, top, kind)
    else:
        walls(mesh, obj, wall_mat, poly, 0.0, top, kind)
    walls(mesh, obj, wall_mat, poly, top, top + 0.9, sol | NO_PARAPET)
    _overhang(mesh, obj, wall_mat, poly, top + 0.9, out=1.2, thick=0.4, flag=sol)
    # The brise-soleil: fins every 1.5 m and a slab edge at every floor, on every wall of 6 m or more.
    z_first = STOREY_M
    pts = list(poly.exterior.coords)[:-1]
    for i in range(len(pts)):
        a, b = np.array(pts[i]), np.array(pts[(i + 1) % len(pts)])
        L = float(np.hypot(*(b - a)))
        if L < 6.0:
            continue
        t = (b - a) / L
        out = np.array([t[1], -t[0]])                     # outward on a counter-clockwise ring
        for s in np.arange(0.75, L - 0.5, 1.5):
            p = a + t * s
            slab(mesh, obj, wall_mat, p + out * 0.02, p + out * 0.62, 0.15, z_first, top + 0.9, sol)
            counts["ministry fins"] += 1
        for f in range(1, n):
            z = f * STOREY_M
            slab(mesh, obj, wall_mat, a + out * 0.32, b + out * 0.32, 0.6, z - 0.12, z + 0.12, sol)
            counts["ministry slab edges"] += 1
    counts["ministries"] += 1
    return "T5"


def veranda_block(mesh, obj, wall_mat, bid, poly, n, counts, kind, facing=None):
    """T6 (a colonial bungalow: a veranda all round, a hipped roof of red tile or rusted sheet), T14 (a school's
    classroom block) and T7 (a hospital ward or a barracks block; both with a veranda along the long side towards the
    road, under a hipped or, on long blocks, gable sheet roof): the walls stand 2.4 m in where the veranda runs, the
    roof covers the whole footprint, posts carry its edge. The walls: an institution's cream (KIND_INSTITUTION, long
    louvre windows under concrete hoods in the game). Footprints too small or too irregular are low blocks."""
    flags = OFFICE | KIND_INSTITUTION
    n = max(1, min(2 if kind != "T7" else 3, n))
    axes = frame_axes(poly)
    if not axes or poly.area / axes[5].area < 0.75 or axes[4] < 8.0:
        return low_block(mesh, obj, wall_mat, bid, poly, n, counts, kind)
    o, u, v, Lu, Lv, rect = axes
    eaves = n * STOREY_M
    if kind == "T6":
        body = _rect(o, u, v, -Lu / 2 + 2.4, Lu / 2 - 2.4, -Lv / 2 + 2.4, Lv / 2 - 2.4)
        roof = pick([("M_RoofTile_Red", 0.5), ("M_RoofSheet_Rust", 0.3), ("M_Roof", 0.2)], h01(bid, "roof"))
    else:
        side = _front(axes, facing)
        b0, b1 = (-Lv / 2, Lv / 2 - 2.4) if side > 0 else (-Lv / 2 + 2.4, Lv / 2)
        body = _rect(o, u, v, -Lu / 2, Lu / 2, b0, b1)
        roof = pick(SHEETS, h01(bid, "roof"))
    walls(mesh, obj, wall_mat, body, 0.0, eaves, flags | NO_PARAPET)
    cap(mesh, obj, wall_mat, rect, eaves, down=True)
    if kind == "T6" or Lu <= 40.0:
        hip_roof(mesh, obj, roof, wall_mat, rect, eaves, 26 if roof == "M_RoofTile_Red" else 18, overhang=0.4)
    else:
        gable_roof(mesh, obj, roof, wall_mat, rect, eaves, 15, overhang=0.4, max_rise=2.5)
    edge = rect.exterior.difference(body.buffer(0.5))
    for line in getattr(edge, "geoms", [edge]):
        if not isinstance(line, LineString) or line.length < 1.0:
            continue
        k = max(1, int(line.length // 3.0))
        for s in range(k + 1):
            p = np.array(line.interpolate(s * line.length / k).coords[0])
            p = p + (o - p) / max(1e-6, float(np.hypot(*(o - p)))) * 0.25
            post(mesh, obj, wall_mat, p, 0.25, 0.0, eaves, _solid(KIND_INSTITUTION))
            counts["veranda posts"] += 1
    counts[{"T6": "bungalows", "T7": "hospital blocks"}.get(kind, "school blocks")] += 1
    return kind


def low_block(mesh, obj, wall_mat, bid, poly, n, counts, kind="T7"):
    """A hospital, barracks or school block that no veranda fits (and the fallback of T6 and T14): one to three
    storeys of the institution's cream (KIND_INSTITUTION) under a hipped sheet roof where the footprint is near a
    rectangle, a low gable on long ones, a flat roof behind a coped parapet on the rest."""
    flags = OFFICE | KIND_INSTITUTION
    n = max(1, min(3, n))
    eaves = n * STOREY_M
    axes = frame_axes(poly)
    fit = poly.area / axes[5].area if axes else 0.0
    roof = pick(SHEETS, h01(bid, "roof"))
    if axes and fit >= 0.75 and 4.0 <= axes[4] <= 20.0:
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, flags | NO_PARAPET)
        if axes[3] <= 45.0:
            hip_roof(mesh, obj, roof, wall_mat, poly, eaves, 18, overhang=0.8)
        else:
            gable_roof(mesh, obj, roof, wall_mat, poly, eaves, 14, overhang=0.8, max_rise=2.5)
        counts["low blocks pitched"] += 1
        return kind
    top = eaves + 0.6
    walls(mesh, obj, wall_mat, poly, 0.0, top, flags)
    cap(mesh, obj, "M_Roof", poly, top)
    _coping(mesh, obj, wall_mat, poly, top, flag=_solid(KIND_INSTITUTION))
    counts["low blocks flat"] += 1
    return kind


def covered_walks(mesh, cell_name, mat, blocks, counts, avoid=(), reach=15.0, width=2.6, height=2.7):
    """Covered walks between neighbouring hospital or school blocks (blocks: their footprints): from each block to its
    nearest neighbour within `reach`, where the walk crosses no other building (avoid): a flat sheet roof (M_Roof) on
    posts every 3 m each side, in the wall paint `mat`. Returns how many it built."""
    if len(blocks) < 2:
        return 0
    tree = shapely.STRtree(blocks)
    others = shapely.union_all(list(avoid)) if avoid else None
    done, built = set(), 0
    for i, a in enumerate(blocks):
        best = None
        for j in tree.query(a, predicate="dwithin", distance=reach):
            if j == i:
                continue
            d = a.distance(blocks[j])
            if 2.0 <= d <= reach and (best is None or d < best[0]):
                best = (d, j)
        if best is None or (min(i, best[1]), max(i, best[1])) in done:
            continue
        j = best[1]
        done.add((min(i, j), max(i, j)))
        p, q = shapely.ops.nearest_points(a, blocks[j])
        line = LineString([p, q])
        path = line.buffer(width / 2, cap_style="flat")
        if others is not None and path.buffer(-0.3).intersects(others):
            continue
        if any(k not in (i, j) and blocks[k].intersects(path.buffer(-0.3)) for k in tree.query(path)):
            continue
        roof = shapely.geometry.polygon.orient(path, 1.0)
        if not isinstance(roof, Polygon) or roof.is_empty:
            continue
        c = roof.centroid
        obj = cell_name("Buildings", c.x, c.y)
        cap(mesh, obj, "M_Roof", roof, height + 0.12)
        cap(mesh, obj, mat, roof, height, down=True)
        walls(mesh, obj, mat, roof, height, height + 0.12, _solid(KIND_INSTITUTION) | NO_PARAPET)
        d = np.array(q.coords[0]) - np.array(p.coords[0])
        L = float(np.hypot(*d))
        t = d / max(L, 1e-6)
        nrm = np.array([-t[1], t[0]])
        k = max(1, int(L // 3.0))
        for s in range(k + 1):
            m = np.array(p.coords[0]) + t * (L * s / k)
            for side in (-1, 1):
                post(mesh, obj, mat, m + nrm * side * (width / 2 - 0.15), 0.16, 0.0, height, _solid(KIND_INSTITUTION))
        built += 1
    counts["covered walks"] += built
    return built


def car_park(mesh, obj, wall_mat, bid, poly, n, counts):
    """T13, an open-sided multi-storey car park: a deck at every level (its top and soffit), columns on a 7.5 m grid
    and along the edge, a 1.1 m parapet at each deck's edge (no openings), and the stair core in a corner."""
    n = max(2, n)
    deck = shapely.geometry.polygon.orient(poly.simplify(0.2), 1.0)
    for k in range(1, n + 1):
        z = k * 3.0
        cap(mesh, obj, "M_Roof", deck, z)
        cap(mesh, obj, wall_mat, deck, z - 0.3, down=True)
        walls(mesh, obj, wall_mat, deck, z - 0.3, z, _solid(KIND_OFFICE) | NO_PARAPET)
        walls(mesh, obj, wall_mat, deck, z, z + 1.1, _solid(KIND_OFFICE) | NO_PARAPET)
    minx, miny, maxx, maxy = deck.bounds
    inner = deck.buffer(-0.5)
    for x in np.arange(minx + 3.0, maxx, 7.5):
        for y in np.arange(miny + 3.0, maxy, 7.5):
            if inner.contains(Point(x, y)):
                post(mesh, obj, wall_mat, (x, y), 0.5, 0.0, n * 3.0 - 0.3, _solid(KIND_OFFICE))
                counts["car park columns"] += 1
    bulkhead(mesh, obj, wall_mat, "M_Roof", deck, n * 3.0, h01(bid, "corner"), _solid(KIND_OFFICE) | NO_PARAPET)
    counts["car parks"] += 1
    return "T13"


def volume(mesh, obj, wall_mat, bid, poly, n, counts):
    """VOL, the sensitive sites' plain volume (embassies, the military sites; the director, 22 Nov 2026: only what road
    photographs show): the outline extruded to its storeys, plain painted walls with punched windows and no shops
    (KIND_VOLUME), a coped parapet; the caller puts it among the yards, so a wall and gate stand along the street."""
    top = n * STOREY_M
    walls(mesh, obj, wall_mat, poly, 0.0, top, OFFICE | YARD | KIND_VOLUME)
    walls(mesh, obj, wall_mat, poly, top, top + 0.8, _solid(KIND_VOLUME))
    cap(mesh, obj, "M_Roof", poly, top + 0.8)
    _coping(mesh, obj, wall_mat, poly, top + 0.8, flag=_solid(KIND_VOLUME))
    counts["volumes"] += 1
    return "VOL"
