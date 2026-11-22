"""Checks the Independence Avenue kit (scripts/avenue_kit.py): the typology each corridor building gets, the data file
of the plan's corridor, lots and named buildings, and the builders in scripts/massing.py on synthetic footprints.

    python3 tests/test_avenue_kit.py
"""
import os
import sys
from collections import Counter

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import avenue_kit  # noqa: E402
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
        return [t for (o, m), ts in self.groups.items() for t in ts if mat is None or m == mat or (mat.endswith("*") and m.startswith(mat[:-1]))]

    def materials(self):
        return {m for _, m in self.groups}


def flags(m):
    return {int(t[3][0][1] // 1000) for t in m.tris("M_Wall_*") if t[3]}


def top(m):
    return max(max(p[2] for p in t[:3]) for t in m.tris())


# --- The choice -------------------------------------------------------------------------------------------------
c = avenue_kit.choose
check("a house in North Ridge: a villa behind its wall", c(180, None, None, []) == "T1")
check("the same in Tudu (segment F): a shop and office block", c(180, None, None, [], segment="F") == "T9")
check("a mapped office of two storeys: an office slab", c(400, 2, "office", []) == "T2")
check("three storeys of nothing known: an office slab", c(300, 3, None, []) == "T2")
check("700 m2 of nothing known: an office slab", c(800, None, None, []) == "T2")
check("a bank inside: a bank branch", c(300, None, None, [("amenity=bank", "GT Bank")]) == "T4")
check("an ATM in a big building: an office slab", c(1500, None, None, [("category=atms", "")]) == "T2")
check("a government office: a ministry block", c(900, 4, "government", []) == "T5")
check("an embassy inside: the plain volume", c(600, None, None, [("office=diplomatic", "Embassy of X")]) == "VOL")
check("on a military lot: the plain volume", c(200, None, None, [], lot={"tags": {"landuse": "military"}}) == "VOL")
check("on the hospital's grounds: a low block", c(400, None, None, [], lot={"tags": {"amenity": "hospital"}}) == "T7")
check("a church by its class", c(500, None, "church", []) == "T11")
check("on a school's lot: a school block", c(300, None, None, [], lot={"tags": {"amenity": "school"}}) == "T14")
check("a hotel inside: a hotel slab", c(900, None, None, [("category=hotel", "")]) == "T12")
check("apartments: flats", c(600, 4, "apartments", []) == "T8")
check("apartments in Tudu: a shop block", c(600, 4, "apartments", [], segment="F") == "T9")
check("nine levels: left for the tower generator (stage 3)", c(900, 9, "office", []) is None)
check("a mapped kiosk under 40 m2: a kiosk", c(25, None, "kiosk", []) == "T10")
check("a machine-learnt footprint under 40 m2: as drawn today (the director, 22 Nov 2026)",
      c(25, None, None, [], ml=True) is None)
check("a machine-learnt footprint of 40 m2 or more takes the rules", c(60, None, None, [], ml=True) == "T1")
check("a small shed on a station's lot: waits for the yard kit (stage 4)", c(120, None, None, [], lot={"treat": "T15", "tags": {}}) is None)
check("a large building on a station's lot: a shop block", c(400, None, None, [], lot={"treat": "T15", "tags": {}}) == "T9")
check("on Jubilee House's grounds: as today", c(400, None, None, [], lot={"treat": "KEEP", "tags": {}}) is None)
check("a small building in a car park: a kiosk", c(60, None, None, [], lot={"tags": {"amenity": "parking"}}) == "T10")
check("a large one in a car park: the rules", c(300, None, None, [], lot={"tags": {"amenity": "parking"}}) == "T1")
for t, want in (("T5", "T5"), ("T13", "T13"), ("T3", None), ("T15", None), ("HERO", None), ("KEEP", None), ("T16", None),
                ("VOL", "VOL")):
    check(f"a building the plan names {t}: {want}", c(500, None, "office", [], named={"treat": t}) == want)
check("the plan's name wins over the uses", c(500, None, "church", [], named={"treat": "T2"}) == "T2")

# The storeys: mapped ones kept, estimates held to the typology's range.
s = avenue_kit.storeys_for
check("mapped storeys kept", s("T1", 1, 4) == 4)
check("a villa's estimate held to two storeys", s("T1", 4, None) == 2)
check("an office slab's estimate at least three", s("T2", 1, None) == 3)
check("a shop block's estimate at most five", s("T9", 7, None) == 5)

# --- The data -----------------------------------------------------------------------------------------------------
data = avenue_kit.load()
corr = shapely.union_all([Polygon(r) for r in data["corridor"]])
check("the corridor is one valid area", corr.is_valid and not corr.is_empty)
km2 = corr.area * (111_320 * 110_574) / 1e6      # degrees to km2 near 5.5 N (cos 5.5 = 0.995)
check("the corridor's area about the plan's 1.91 km2", 1.7 < km2 * 0.995 < 2.1, f"{km2 * 0.995:.2f}")
line = LineString(data["line"])
check("the avenue's line runs south from 37 Circle to Tudu", line.coords[0][1] > 5.585 and line.coords[-1][1] < 5.55,
      f"{line.coords[0][1]:.4f} to {line.coords[-1][1]:.4f}")
check("six segments A to F", [k for k, *_ in data["segments"]] == list("ABCDEF"))
treats = Counter(b["treat"] for b in data["buildings"])
check("the plan's named buildings carry its typologies", {"T5", "T13", "T11", "T8", "VOL", "HERO", "KEEP"} <= set(treats), dict(treats))
names = {b["osm"]: b for b in data["buildings"]}
check("Ministry of Roads and Highways: a ministry block", names["relation/8406754"]["treat"] == "T5")
check("East Ridge flats: six blocks of flats", sum(1 for k, b in names.items() if k.startswith("way/15701269") or k == "way/157012700") == 6
      and all(b["treat"] == "T8" for k, b in names.items() if k.startswith("way/157012")))
check("the embassies: plain volumes", names["way/519339257"]["treat"] == "VOL" and names["way/879118560"]["treat"] == "VOL")
lots = {l["lot"]: l for l in data["lots"]}
check("37 Military Hospital's grounds carry their tags", lots["A13"]["tags"].get("amenity") == "hospital"
      and lots["A13"]["treat"] == "T7")
check("the stations' lots wait for the yard kit", sum(1 for l in data["lots"] if l["treat"] == "T15") >= 4)

# The Avenue class on the data, in metres east and north of 37 Circle (an equirectangular frame, as the survey's).
LON0, LAT0 = line.coords[0]
KX, KY = 111_320 * np.cos(np.radians(LAT0)), 110_574
to_m = lambda g: shapely.transform(g, lambda xy: np.column_stack(((xy[:, 0] - LON0) * KX, (xy[:, 1] - LAT0) * KY)))
av = avenue_kit.Avenue(data, to_m)
mid = av.line.interpolate(0.5, normalized=True)
check("the line measures the plan's 5.95 km", 5800 < av.line.length < 6100, f"{av.line.length:.0f} m")
check("a footprint on the avenue is in the corridor", av.inside(Point(mid.x + 30, mid.y).buffer(5)))
check("a footprint 1 km off is not", not av.inside(Point(mid.x + 1000, mid.y).buffer(5)))
check("37 Circle is segment A, Tudu segment F", av.segment(Point(av.line.coords[0]).buffer(5)) == "A"
      and av.segment(Point(av.line.coords[-1]).buffer(5)) == "F")
mrh = names["relation/8406754"]
found = av.named_of(to_m(Point(mrh["at"])).buffer(np.sqrt(mrh["area_m2"] / np.pi)))
check("a named building is found by its point", found is not None and found["osm"] == "relation/8406754")
check("but not on a footprint of another size", av.named_of(to_m(Point(mrh["at"])).buffer(4)) is None)

# --- The builders ---------------------------------------------------------------------------------------------------
DISTRICT_MATS = {"M_Wall_Concrete", "M_Wall_Ochre", "M_Wall_PaleBlue", "M_Wall_Terracotta", "M_Wall_Grey", "M_Wall_Mint",
                 "M_Roof", "M_RoofSheet_Rust", "M_RoofTile_Red", "M_Gate"}
slab_foot = orient(box(0, 0, 40, 14), 1.0)
cnt = Counter()

m = Mesh()
massing.villa(m, "o", "M_Wall_Ochre", "v1", orient(box(0, 0, 14, 10), 1.0), 2, 0.6, cnt)
check("villa: walls carry HOME and YARD", all(f & massing.HOME and f & massing.YARD for f in flags(m)), flags(m))
check("villa: a pitched roof on a near-rectangle", m.materials() & {"M_RoofTile_Red", "M_Roof", "M_RoofSheet_Rust"} != set()
      and cnt["villa hipped"] + cnt["villa flat"] == 1)
check("villa: district materials only", m.materials() <= DISTRICT_MATS, m.materials())

m = Mesh()
massing.office(m, "o", "M_Wall_Concrete", "o1", slab_foot, 5, cnt, facing=np.array([0.0, -1.0]))
check("office: every storey's wall an OFFICE wall", any(f & massing.OFFICE for f in flags(m)))
check("office: no shops' flags", not any(f & (massing.SHOPFRONT | massing.YARD) for f in flags(m)))
check("office: five storeys and a parapet", 5 * 3.2 + 1.0 < top(m) < 5 * 3.2 + 4.0, f"{top(m):.1f}")
check("office: an entrance canopy stands out of the footprint", any(min(p[1] for p in t[:3]) < -0.5 for t in m.tris()))
check("office: district materials only", m.materials() <= DISTRICT_MATS, m.materials())
banded = sum(1 for i in range(40) if massing.h01(f"b{i}", "bands") < 0.4)
check("office: about two in five banded", 8 <= banded <= 24, banded)

m = Mesh()
massing.office(m, "o", "M_Wall_Concrete", "h1", slab_foot, 6, cnt, facing=np.array([0.0, -1.0]), porte=True)
check("hotel: a porte-cochere 6 m out", any(min(p[1] for p in t[:3]) < -5.5 for t in m.tris()))

m = Mesh()
massing.bank(m, "o", "M_Wall_Concrete", "b1", orient(box(0, 0, 20, 12), 1.0), 2, cnt, facing=np.array([0.0, 1.0]))
check("bank: a glazed ground floor (CURTAIN) under an OFFICE storey", any(f & massing.CURTAIN for f in flags(m))
      and any(f & massing.OFFICE for f in flags(m)))
check("bank: its canopy on the road's side", any(max(p[1] for p in t[:3]) > 13.0 for t in m.tris()))

m = Mesh()
cnt = Counter()
massing.ministry(m, "o", "M_Wall_Concrete", "m1", orient(box(0, 0, 60, 16), 1.0), 5, cnt)
check("ministry: sun-breakers every 1.5 m", cnt["ministry fins"] >= 2 * (60 // 1.5) - 4, cnt["ministry fins"])
check("ministry: the fins carry no openings", any(f & massing.NO_OPENINGS for f in flags(m)))
check("ministry: OFFICE walls", any(f & massing.OFFICE for f in flags(m)))
check("ministry: district materials only", m.materials() <= DISTRICT_MATS, m.materials())
check("ministry: under 3,000 triangles for a 60 m block", len(m.tris()) < 3000, len(m.tris()))

m = Mesh()
cnt = Counter()
massing.veranda_block(m, "o", "M_Wall_Ochre", "s1", orient(box(0, 0, 36, 10), 1.0), 1, cnt, "T14", facing=np.array([0.0, 1.0]))
check("school: a classroom block with a veranda and its posts", cnt["school blocks"] == 1 and cnt["veranda posts"] >= 10, dict(cnt))
m = Mesh()
cnt = Counter()
massing.veranda_block(m, "o", "M_Wall_Ochre", "c1", orient(box(0, 0, 20, 14), 1.0), 1, cnt, "T6")
check("bungalow: a veranda all round under a hipped roof", cnt["bungalows"] == 1 and cnt["veranda posts"] >= 16, dict(cnt))
m = Mesh()
cnt = Counter()
massing.veranda_block(m, "o", "M_Wall_Ochre", "c2", orient(Polygon([(0, 0), (12, 0), (12, 4), (4, 4), (4, 12), (0, 12)]), 1.0), 1, cnt, "T6")
check("bungalow: an L-shaped footprint falls back on the low block", cnt["bungalows"] == 0 and cnt["low blocks flat"] + cnt["low blocks pitched"] == 1)

m = Mesh()
cnt = Counter()
massing.low_block(m, "o", "M_Wall_Grey", "h7", orient(box(0, 0, 60, 12), 1.0), 1, cnt)
check("hospital block: long and low under a pitched sheet roof", cnt["low blocks pitched"] == 1
      and m.materials() & {"M_Roof", "M_RoofSheet_Rust"} and top(m) < 7.0)

m = Mesh()
cnt = Counter()
massing.car_park(m, "o", "M_Wall_Grey", "cp", orient(box(0, 0, 60, 40), 1.0), 6, cnt)
check("car park: six open decks, no window walls", top(m) >= 18.0 and not any(f & massing.OFFICE for f in flags(m))
      and all(f & massing.NO_OPENINGS for f in flags(m) if f), flags(m))
check("car park: columns inside", cnt["car park columns"] >= 20, cnt["car park columns"])

m = Mesh()
massing.volume(m, "o", "M_Wall_Concrete", "e1", orient(box(0, 0, 25, 18), 1.0), 3, Counter())
check("volume: plain, no shops, in a yard (its wall built round it)", any(f & massing.OFFICE and f & massing.YARD for f in flags(m)))

check("the avenue's flags sit above kit2's", massing.OFFICE > massing.HALL and massing.BANDS > massing.OFFICE
      and massing.HOME > massing.BANDS and len({massing.OFFICE, massing.BANDS, massing.HOME}) == 3)

# compounds() with a district's own paints draws its walls in those only.
m = Mesh()
road = LineString([(-50, -6), (100, -6)])
yards = [("y1", orient(box(0, 0, 12, 10), 1.0)), ("y2", orient(box(20, 0, 32, 10), 1.0))]
massing.compounds(m, lambda p, x, y: "c", yards, [], [("residential", road)], {"residential": 6.0}, Counter(),
                  box(-60, -40, 120, 60), paints=[("M_Wall_Ochre", 1.0)])
walls_m = {mm for mm in m.materials() if mm.startswith("M_Wall_")}
check("compounds: walls in the district's paints only", walls_m == {"M_Wall_Ochre"}, walls_m)

print("avenue kit: " + ("all checks passed" if not failures else f"{failures} FAILED"))
sys.exit(1 if failures else 0)
