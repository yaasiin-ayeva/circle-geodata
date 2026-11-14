"""Building modules for the district: what turns an extruded footprint into an Osu building.

Used by build_district.py. The rules come from circle-reference's street survey (building heights, roofs,
boundary walls) and circle-bible's research/landscape-look.md (roof materials):

- house: one or two storeys away from Oxford Street, on a footprint close to a rectangle. Walls up to the
  eaves and a hipped roof over the footprint's best-fit rectangle with a 0.5 m overhang, a fascia and a
  soffit. Grey cement or galvanised sheet, rusted sheet, or red tile (tile mostly on two-storey houses).
- block: a flat concrete roof behind a parapet, as before. Most blocks of two storeys or more carry a stair
  bulkhead on the roof; the top storey of tall blocks is often set back 2 m behind a terrace.
- frame: a finished block with an unfinished storey on top, bare concrete columns and no walls.
- compound walls: residential buildings stand in walled yards. The walls follow the street frontage of the
  blocks, 1.2 m back from the road edge, stop short of every building and of shopfronts, and open on a steel
  gate in front of each yard's house.

Nothing here adds a material per building kind beyond four: M_RoofSheet_Rust, M_RoofTile_Red, M_Gate and
M_Concrete_Raw. Compound walls, fascias, soffits and bulkheads reuse the wall paints (M_Wall_*), so a cell
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

# Compound wall paints (street survey: white or cream, coral-red, ochre, dark grey on newer villas), as the
# district's wall materials; circle-game repaints them in the surveyed palette.
BOUNDARY_PAINTS = [("M_Wall_Concrete", 0.30), ("M_Wall_PaleBlue", 0.25), ("M_Wall_Terracotta", 0.15),
                   ("M_Wall_Ochre", 0.15), ("M_Wall_Grey", 0.15)]

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
    texture, and constant values weld and compress to almost nothing (gltfpack -kv keeps them)."""
    f = 1000.0 * flag
    return ((0, f), (0, f), (0, f), (0, f))


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


def building(mesh, obj, wall_mat, bid, poly, n_storeys, parapet, near_oxford, yard, counts):
    """One building's massing: a house, a frame or a block. yard: it stands in a walled compound."""
    base = YARD if yard else 0
    area = poly.area
    eaves = n_storeys * STOREY_M
    axes = frame_axes(poly)
    rect_fit = poly.area / axes[5].area if axes else 0.0
    # Houses: hipped roofs where the footprint is near a rectangle and narrow enough to span.
    if (n_storeys <= 2 and not near_oxford and 30 <= area <= 400 and axes and rect_fit >= 0.8
            and 4.0 <= axes[4] <= 15.0 and axes[3] <= 40.0 and h01(bid, "hip") < 0.85):
        r = h01(bid, "roof")
        if n_storeys == 1:
            roof = pick([("M_Roof", 0.5), ("M_RoofSheet_Rust", 0.4), ("M_RoofTile_Red", 0.1)], r)
        else:
            roof = pick([("M_Roof", 0.35), ("M_RoofSheet_Rust", 0.2), ("M_RoofTile_Red", 0.45)], r)
        walls(mesh, obj, wall_mat, poly, 0.0, eaves, base | NO_PARAPET)
        hip_roof(mesh, obj, roof, wall_mat, poly, eaves, 28 if roof == "M_RoofTile_Red" else 18)
        counts["hip_roofs"] += 1
        counts[f"roof {roof}"] += 1
        return "house"
    top = eaves + parapet
    # An unfinished storey on a finished block, away from Oxford Street.
    if n_storeys >= 2 and not near_oxford and area >= 60 and h01(bid, "frame") < 0.1:
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


def compounds(mesh, cell_name, yards, keep_out, roads, road_width, counts, district, avoid=None):
    """Compound walls along the residential streets, with a gate towards each yard's house.

    The walls follow the street frontage of the city blocks, VERGE_M back from the road edge, wherever a yard
    building stands within 25 m behind it. They stop where a shop or block fronts the street, where a
    building reaches the frontage itself, and near `avoid` (Oxford Street's line, its shops face the road).
    yards: (bid, footprint) of the buildings in compounds; keep_out: every other footprint, look-dev ones
    included; roads: (class, line); district: the district's outline.
    """
    if not yards:
        return
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
        return
    network = MultiLineString(pieces)
    # Gates: the frontage point nearest each yard house, at most 30 m away, no two within 8 m.
    gates = []
    for bid, foot in yards:
        g = shapely.ops.nearest_points(network, foot.centroid)[0]
        if g.distance(foot) > 30.0 or any(g.distance(o) < 8.0 for o in gates):
            continue
        gates.append(g)
    for i, piece in enumerate(pieces):
        start = piece.coords[0]
        paint = pick(BOUNDARY_PAINTS, h01("wall", round(start[0], 1), round(start[1], 1)))
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
            counts["compound_wall_m"] += a1 - a0
        for s in cuts:
            c = np.array(piece.interpolate(s).coords[0])
            a = np.array(piece.interpolate(max(0.0, s - 0.5)).coords[0])
            b = np.array(piece.interpolate(min(piece.length, s + 0.5)).coords[0])
            t = (b - a) / max(1e-6, float(np.hypot(*(b - a))))
            obj = cell_name("Buildings", c[0], c[1])
            slab(mesh, obj, "M_Gate", c - t * GATE_W / 2, c + t * GATE_W / 2, 0.06, 0.0, GATE_H)
            for side in (-1, 1):
                post(mesh, obj, paint, c + t * side * (GATE_W / 2 + 0.22), 0.44, 0.0, WALL_H + 0.35)
            counts["gates"] += 1
