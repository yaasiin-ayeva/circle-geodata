"""Checks the building modules (scripts/massing.py) on synthetic footprints and a synthetic street.

    python3 tests/test_massing.py
"""
import os
import sys
from collections import Counter

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import massing  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


class Mesh:
    """Collects triangles as build_district.Mesh does: (object, material) -> [(a, b, c, uv)]."""

    def __init__(self):
        self.groups = {}

    def tri(self, obj, mat, a, b, c, uv=None):
        self.groups.setdefault((obj, mat), []).append((a, b, c, uv))

    def quad(self, obj, mat, a, b, c, d, uv=None):
        uva, uvb = (None, None) if uv is None else ((uv[0], uv[1], uv[2]), (uv[0], uv[2], uv[3]))
        self.tri(obj, mat, a, b, c, uva)
        self.tri(obj, mat, a, c, d, uvb)

    def tris(self, mat=None):
        return [t for (o, m), ts in self.groups.items() for t in ts if mat is None or m == mat]


def flags_of(tri):
    return int(tri[3][0][1] // 1000) if tri[3] else None


def find_bid(test):
    """A building id for which test(h01 draws) holds, so the checks do not depend on luck."""
    for i in range(5000):
        if test(f"b{i}"):
            return f"b{i}"
    raise RuntimeError("no id found")


# A one-storey house, 10 x 7 m: walls to the eaves with NO_PARAPET, a hipped roof that rises above them.
house = orient(box(0, 0, 10, 7), 1.0)
bid = find_bid(lambda b: massing.h01(b, "hip") < 0.85)
m, counts = Mesh(), Counter()
massing.building(m, "o", "M_Wall_Ochre", bid, house, 1, 0.6, False, True, counts)
check("house: one hipped roof", counts["hip_roofs"] == 1, dict(counts))
roof = [t for (o, mat), ts in m.groups.items() for t in ts if mat.startswith("M_Roof")]
top = max(p[2] for t in roof for p in t[:3])
check("house: ridge above the eaves", top > massing.STOREY_M + 0.5, f"ridge {top:.2f} m")
walls = [t for t in m.tris("M_Wall_Ochre") if t[3] and abs(t[0][2] - t[1][2]) + abs(t[1][2] - t[2][2]) > 0.1]
f = Counter(flags_of(t) for t in walls)
check("house: walls flagged NO_PARAPET and YARD", f.get(massing.NO_PARAPET | massing.YARD, 0) >= 8, dict(f))
eaves = [p for t in roof for p in t[:3] if abs(p[2] - massing.STOREY_M) < 1e-6]
xs = [p[0] for p in eaves]
check("house: 0.5 m overhang", abs(min(xs) + 0.5) < 1e-6 and abs(max(xs) - 10.5) < 1e-6, f"{min(xs):.2f}..{max(xs):.2f}")

# An L-shaped footprint is no rectangle: it keeps a flat roof.
ell = orient(Polygon([(0, 0), (12, 0), (12, 4), (4, 4), (4, 12), (0, 12)]), 1.0)
m, counts = Mesh(), Counter()
massing.building(m, "o", "M_Wall_Ochre", "ell", ell, 1, 0.6, False, True, counts)
check("L-shape: flat roof, no hip", counts["hip_roofs"] == 0 and len(m.tris("M_Roof")) > 0)

# Near Oxford Street nothing is hipped, whatever the draw.
m, counts = Mesh(), Counter()
massing.building(m, "o", "M_Wall_Ochre", bid, house, 1, 0.6, True, False, counts)
check("Oxford Street: no hipped roof", counts["hip_roofs"] == 0)

# A two-storey block with a bulkhead: the bulkhead stands inside the roof and is flagged NO_PARAPET.
block = orient(box(0, 0, 14, 11), 1.0)
bid = find_bid(lambda b: massing.h01(b, "stair") < 0.65 and massing.h01(b, "frame") >= 0.1 and massing.h01(b, "setback") >= 0.5)
m, counts = Mesh(), Counter()
massing.building(m, "o", "M_Wall_Grey", bid, block, 2, 0.6, True, False, counts)
check("block: one bulkhead", counts["bulkheads"] == 1, dict(counts))
roof_z = 2 * massing.STOREY_M + 0.6
high = [p for t in m.tris("M_Wall_Grey") for p in t[:3] if p[2] > roof_z + 0.01]
check("block: bulkhead inside the footprint", all(0 < p[0] < 14 and 0 < p[1] < 11 for p in high), f"{len(high)} vertices")

# A frame storey: columns on the roof, walls below flagged NO_PARAPET.
bid = find_bid(lambda b: massing.h01(b, "frame") < 0.1)
m, counts = Mesh(), Counter()
massing.building(m, "o", "M_Wall_Grey", bid, block, 3, 0.6, False, False, counts)
check("frame: columns on the roof", counts["frames"] == 1 and counts["frame_columns"] >= 10, dict(counts))

# A street with houses on both sides: walls along the frontage, gates, nothing crossing a building.
street = LineString([(-100, 0), (100, 0)])
yards = []
for i, x in enumerate(range(-80, 81, 20)):
    for side in (-1, 1):
        y0 = side * 12
        yards.append((f"h{i}{side}", orient(box(x - 4, min(y0, y0 + side * 8), x + 4, max(y0, y0 + side * 8)), 1.0)))
shop = orient(box(-6, -10, 6, -4.3), 1.0)            # a shop right on the south frontage
m, counts = Mesh(), Counter()
district = box(-120, -60, 120, 60)
massing.compounds(m, lambda p, e, n: "cell", yards, [shop], [("residential", street)], {"residential": 6.0}, counts, district)
check("street: compound walls built", counts["compound_wall_m"] > 250, f"{counts['compound_wall_m']:.0f} m")
check("street: a gate per house or so", 10 <= counts["gates"] <= len(yards), f"{counts['gates']} gates for {len(yards)} houses")
edge = 6.0 / 2 + massing.VERGE_M      # the frontage line; walls and gate posts are centred on it (0.44 m at most)
wall_pts = [p for (o, mat), ts in m.groups.items() if mat != "M_Gate" for t in ts for p in t[:3]]
check("street: walls clear of the road", all(abs(p[1]) >= edge - 0.25 for p in wall_pts), f"min |y| {min(abs(p[1]) for p in wall_pts):.2f}")
buildings = [p for _, p in yards] + [shop]
inside = [p for p in wall_pts if any(b.buffer(-0.05).contains(Point(p[0], p[1])) for b in buildings)]
check("street: no wall inside a building", not inside, f"{len(inside)} vertices inside")
near_shop = [p for p in wall_pts if shop.buffer(2.5).contains(Point(p[0], p[1]))]
check("street: no wall in front of the shop", not near_shop, f"{len(near_shop)} vertices")
f = Counter(flags_of(t) for (o, mat), ts in m.groups.items() if mat != "M_Gate" for t in ts if t[3])
check("street: compound walls flagged NO_OPENINGS", set(f) == {massing.NO_OPENINGS}, dict(f))

# District roof palettes: the same houses get the district's mix and pitch.
def roof_mix(district, n=400):
    mats, pitches = Counter(), []
    for i in range(n):
        m, c = Mesh(), Counter()
        if massing.building(m, "o", "M_Wall_Ochre", f"d{i}", house, 1, 0.6, False, True, c, district) != "house":
            continue
        roof = [(mat, ts) for (o, mat), ts in m.groups.items() if mat.startswith("M_Roof")][0]
        mats[roof[0]] += 1
        ridge = max(p[2] for t in roof[1] for p in t[:3])
        pitches.append((roof[0], ridge - massing.STOREY_M))
    return mats, pitches
mats, _ = roof_mix("jamestown")
total = sum(mats.values())
check("Jamestown: almost every pitched roof grey", mats["M_Roof"] / total > 0.88 and mats["M_RoofTile_Red"] / total < 0.06, dict(mats))
mats, pitches = roof_mix("spintex")
total = sum(mats.values())
check("Spintex: red the largest share, painted roofs present", mats["M_RoofTile_Red"] / total > 0.3 and mats["M_RoofSheet_Painted"] > 0, dict(mats))
_, jt = roof_mix("jamestown", 60)
_, sp = roof_mix("spintex", 60)
rise = lambda ps: max(h for mat, h in ps if mat != "M_RoofTile_Red")
check("Jamestown: sheet roofs flatter than Spintex's", rise(jt) < rise(sp), f"{rise(jt):.2f} m against {rise(sp):.2f} m")
mats, _ = roof_mix("nowhere", 200)
check("unknown district: Osu's mix, no painted roofs", mats["M_RoofSheet_Painted"] == 0 and mats["M_Roof"] > 0, dict(mats))

print("massing: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
