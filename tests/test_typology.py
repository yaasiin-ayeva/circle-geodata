"""Checks the typology rules (scripts/typology.py) and their builders (scripts/massing.py) on synthetic footprints.

    python3 tests/test_typology.py
"""
import os
import sys
from collections import Counter

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import massing  # noqa: E402
import typology  # noqa: E402

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
        assert uv is None or len(uv) == 3, "a triangle carries three texture coordinates"
        self.groups.setdefault((obj, mat), []).append((a, b, c, uv))

    def quad(self, obj, mat, a, b, c, d, uv=None):
        uva, uvb = (None, None) if uv is None else ((uv[0], uv[1], uv[2]), (uv[0], uv[2], uv[3]))
        self.tri(obj, mat, a, b, c, uva)
        self.tri(obj, mat, a, c, d, uvb)

    def tris(self, mat=None):
        return [t for (o, m), ts in self.groups.items() for t in ts if mat is None or m == mat or (mat.endswith("*") and m.startswith(mat[:-1]))]

    def materials(self):
        return {m for _, m in self.groups}


def flags_of(tri):
    return int(tri[3][0][1] // 1000) if tri[3] else None


def normal(t):
    n = np.cross(np.subtract(t[1], t[0]), np.subtract(t[2], t[0]))
    return n / (np.linalg.norm(n) or 1)


def facing_out(m, poly, mat="M_Wall_*"):
    """The share of the vertical triangles on the footprint's outline whose normal points away from its middle (a
    bell tower's or a plant room's faces inside the outline are left out)."""
    c = np.array(poly.centroid.coords[0])
    ok = total = 0
    for t in m.tris(mat):
        n = normal(t)
        if abs(n[2]) > 0.3:
            continue
        mid = np.mean([p[:2] for p in t[:3]], axis=0)
        if poly.exterior.distance(Point(*mid)) > 0.3:
            continue
        total += 1
        ok += float(np.dot(n[:2], mid - c)) > -1e-6
    return ok / max(total, 1)


def find_bid(test):
    for i in range(5000):
        if test(f"t{i}"):
            return f"t{i}"
    raise RuntimeError("no id found")


# The materials every builder may use: the district's (the six-material rule of massing.py), nothing new.
ALLOWED = {"M_Roof", "M_RoofSheet_Rust", "M_RoofTile_Red", "M_RoofSheet_Painted", "M_Gate", "M_Concrete_Raw", "M_Wall_Ochre"}

# --- the rules -----------------------------------------------------------------------------------------------------
c = lambda **k: typology.classify(**{"bid": "b1", "area": 300, "storeys": 2, "tagged": False, "height": None, "cls": None,
                                     "name": None, "places": [], "front": None, "district": "legon", **k})
check("church by class", c(cls="church") == "church")
check("church by OSM tag inside", c(places=[("amenity=place_of_worship", "Christ the King")]) == "church")
check("church by its own name", c(name="Pentecost Church, Labone branch") == "church")
check("mosque by class", c(cls="mosque") == "mosque")
check("mosque by name", c(places=[("amenity=place_of_worship", "Abossey Okai Central Mosque")]) == "mosque")
check("a clinic named Cathedral is no church", c(cls="commercial", name="Cathedral Clinic") != "church")
check("a shop inside a church's footprint does not make it one", c(places=[("shop=kiosk", "Grace Pharmacy")]) != "church")
check("warehouse by class", c(cls="warehouse", area=900) == "warehouse")
check("big box: a large supermarket without floors", c(cls="supermarket", area=2500) == "warehouse")
check("a bank's tall office is no warehouse", c(cls="commercial", area=3000, storeys=6) != "warehouse")
check("tower: tagged office of 8 storeys", c(cls="office", area=900, storeys=8, tagged=True) == "tower")
check("tower: 30 m mapped", c(area=600, storeys=9, tagged=True, height=30.0) == "tower")
check("tower zone: large untagged footprint", c(area=1200, storeys=3, tower_zone=True) == "tower")
check("tower zone: small footprint stays", c(area=400, storeys=2, tower_zone=True) != "tower")
check("flats: apartments of 5 storeys", c(cls="apartments", area=600, storeys=5, tagged=True) == "apartments")
check("flats: untagged 4 storeys outside the centre", c(area=700, storeys=4) == "apartments")
check("no flats: untagged 4 storeys in the old centre", c(area=700, storeys=4, district="central") != "apartments")
check("no flats: a 4-storey block with an office inside", c(area=700, storeys=4, places=[("office=company", "SIC")]) != "apartments")
sb = find_bid(lambda b: massing.h01(b, "storey") < 0.85)
check("storey building on a main road", c(bid=sb, storeys=3, front="secondary") == "storey")
check("storey building on any street in Kaneshie", c(bid=sb, storeys=3, front="residential", district="kaneshie") == "storey")
check("no storey building on a residential street in East Legon", c(bid=sb, storeys=3, front="residential") == "generic")
check("no storey building off the road", c(bid=sb, storeys=3, front=None) == "generic")
check("kiosk: 12 m2 of one storey on the street", c(area=12, storeys=1, front="residential") == "kiosk")
check("no kiosk: 12 m2 in a yard, off the street", c(area=12, storeys=1) != "kiosk")
check("no kiosk: a canopy (building=roof)", c(area=12, storeys=1, cls="roof", front="residential") != "kiosk")
check("deterministic", all(c(bid=f"x{i}", storeys=3, front="primary") == c(bid=f"x{i}", storeys=3, front="primary") for i in range(50)))

# Frontage: the class of the road whose edge is within 6 m, main roads first.
roads = [("residential", LineString([(-100, 0), (100, 0)])), ("primary", LineString([(-100, 30), (100, 30)]))]
idx = typology.front_index(roads, lambda k: {"residential": 6.0, "primary": 13.0}[k])
check("frontage: a building on the street", typology.frontage(idx, box(-5, 5, 5, 15)) == "residential")
check("frontage: the main road wins", typology.frontage(idx, box(-5, 5, 5, 22)) == "primary")
check("frontage: none behind", typology.frontage(idx, box(-5, 60, 5, 70)) is None)
f = typology.facing(idx, box(-5, 5, 5, 15))
check("facing: towards the nearest road", f is not None and f[1] < -0.9, str(f))
places = typology.Places([(Point(0, 10), "amenity=place_of_worship", "St Mary's Anglican Church"), (Point(50, 50), "shop=kiosk", "X")])
check("places inside a footprint", places.inside(box(-5, 5, 5, 15)) == [("amenity=place_of_worship", "St Mary's Anglican Church")])
check("worship name from OSM", typology.worship_name(None, places.inside(box(-5, 5, 5, 15))) == "St Mary's Anglican Church")
check("shop name: the building's own first", typology.shop_name("CITA HOUSE", [("shop=clothes", "Woodin")]) == "CITA HOUSE")

# --- the builders --------------------------------------------------------------------------------------------------
hall = orient(box(0, 0, 30, 14), 1.0)
bid = find_bid(lambda b: massing.h01(b, "bell tower") < 0.75)
m, k = Mesh(), Counter()
massing.church(m, "o", "M_Wall_Ochre", bid, hall, k)
top = max(p[2] for t in m.tris() for p in t[:3])
fl = Counter(flags_of(t) for t in m.tris("M_Wall_Ochre") if t[3])
check("classic church: a bell tower above the ridge", k["bell_towers"] == 1 and top > 7.5 + 7, f"{top:.1f} m")
check("classic church: hall walls flagged HALL, no shops", all(f & massing.HALL for f in fl if f is not None) and fl, dict(fl))
check("classic church: walls face out", facing_out(m, hall) > 0.97, f"{facing_out(m, hall):.2f}")
check("classic church: district materials only", m.materials() <= ALLOWED, m.materials() - ALLOWED)
small = orient(box(0, 0, 18, 10), 1.0)
m, k = Mesh(), Counter()
massing.church(m, "o", "M_Wall_Ochre", "a", small, k)
roofz = [p[2] for t in m.tris("M_Roof*") for p in t[:3]]
check("A-frame church: a steep roof from low walls", k["churches_aframe"] == 1 and max(roofz) - min(roofz) > 6, f"{max(roofz) - min(roofz):.1f} m")
check("A-frame church: walls face out", facing_out(m, small) > 0.97)
ups = [normal(t) for t in m.tris("M_Roof*")]
check("A-frame church: roof faces up", ups and min(n[2] for n in ups) > 0.2)

sq = orient(box(0, 0, 24, 20), 1.0)
bid = find_bid(lambda b: True)
m, k = Mesh(), Counter()
massing.mosque(m, "o", "M_Wall_Ochre", bid, sq, 1, 0.6, k)
dz = [p[2] for t in m.tris("M_RoofSheet_Painted") for p in t[:3]]
check("mosque: a dome and a minaret", k["domes"] == 1 and k["minarets"] == 1, dict(k))
check("mosque: the minaret rises 16 m or more", max(dz) >= 16, f"{max(dz):.1f} m")
inside = all(sq.buffer(0.5).contains(Point(p[0], p[1])) for t in m.tris() for p in t[:3])
check("mosque: everything over its footprint (the landmark mask finds it)", inside)
check("mosque: district materials only", m.materials() <= ALLOWED, m.materials() - ALLOWED)
dome_up = [normal(t) for t in m.tris("M_RoofSheet_Painted")]
check("mosque: dome and cap face outward and up", min(n[2] for n in dome_up) > -0.05)

shed = orient(box(0, 0, 50, 25), 1.0)
m, k = Mesh(), Counter()
massing.warehouse(m, "o", "M_Wall_Ochre", "w", shed, "warehouse", k)
check("warehouse: a shed with roller doors", k["sheds"] == 1 and k["roller_doors"] >= 1, dict(k))
fl = Counter(flags_of(t) for t in m.tris("M_Wall_Ochre") if t[3])
check("warehouse: plinth without openings", fl and all(f & massing.NO_OPENINGS for f in fl), dict(fl))
check("warehouse: sheet cladding", any(m.tris(x) for x in ("M_Roof", "M_RoofSheet_Rust", "M_RoofSheet_Painted")))
m, k = Mesh(), Counter()
massing.warehouse(m, "o", "M_Wall_Ochre", "w", shed, "supermarket", k)
check("big box: flat roof, blank walls", k["big_boxes"] == 1 and m.tris("M_Roof"))

big = orient(box(0, 0, 40, 30), 1.0)
bid = find_bid(lambda b: massing.h01(b, "podium") < 0.6)
m, k = Mesh(), Counter()
massing.tower(m, "o", "M_Wall_Ochre", bid, big, 10, k)
fl = Counter(flags_of(t) for t in m.tris("M_Wall_Ochre") if t[3])
check("tower: a podium and a curtain wall", k["podiums"] == 1 and fl[massing.CURTAIN | massing.NO_PARAPET] > 0, dict(fl))
top = max(p[2] for t in m.tris() for p in t[:3])
check("tower: ten storeys and a crown", top >= 10 * massing.STOREY_M + 1.5, f"{top:.1f} m")
check("tower: walls face out", facing_out(m, big) > 0.97)

flats = orient(box(0, 0, 25, 14), 1.0)
m, k = Mesh(), Counter()
massing.building(m, "o", "M_Wall_Ochre", "f1", flats, 5, 0.6, False, False, k, "osu", None, massing.BALCONIES)
fl = Counter(flags_of(t) for t in m.tris("M_Wall_Ochre") if t[3])
check("flats: every wall flagged BALCONIES", fl and all(f & massing.BALCONIES for f in fl), dict(fl))
house = orient(box(0, 0, 10, 8), 1.0)
bid = find_bid(lambda b: massing.h01(b, "hip") < 0.5 and massing.h01(b, "frame") >= 0.1)
m, k = Mesh(), Counter()
kind = massing.building(m, "o", "M_Wall_Ochre", bid, house, 2, 0.6, False, False, k, "osu", "M_RoofTile_Red", massing.SHOPFRONT)
fl = Counter(flags_of(t) for t in m.tris("M_Wall_Ochre") if t[3])
check("storey building: flat roof even where a house would be hipped", kind == "block" and k["hip_roofs"] == 0)
check("storey building: walls flagged SHOPFRONT", fl and all(f & massing.SHOPFRONT for f in fl), dict(fl))

kio = orient(box(0, 0, 4, 3), 1.0)
m, k = Mesh(), Counter()
massing.kiosk(m, "o", "M_Wall_Ochre", "k", kio, k)
wz = max(p[2] for t in m.tris("M_Wall_Ochre") if abs(normal(t)[2]) < 0.3 and flags_of(t) is not None and not flags_of(t) & 1 for p in t[:3])
check("kiosk: walls under the dressing's 2.5 m", wz < 2.5 + 1e-6, f"{wz:.2f} m")
check("kiosk: a sheet roof", any(m.tris(x) for x in ("M_Roof", "M_RoofSheet_Rust", "M_RoofSheet_Painted")))

print("typology: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
