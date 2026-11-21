"""Checks the interchanges' pieces (scripts/elevated.py) and what keeps off them on the ground (scripts/build_district.py)
on synthetic data: roundabouts driven one way, footbridges over and onto decks, twin carriageways fitted to the room
between them, slip roads levelled with the road they leave where the two overlap, the roads and the solids taken off
the structures' ground.

    python3 tests/test_elevated.py
"""
import os
import sys

from shapely.geometry import LineString, Point, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402
import elevated as el  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


def piece(pid, pts, off, w=7.6, oneway=True, kind=None, lanes=None):
    p = {"id": pid, "osm": 1, "cls": "primary", "name": "", "w": w, "pts": [list(q) for q in pts], "off": list(off),
         "kind": kind or "".join("F" if o > el.EPS else "C" if o < -el.EPS else "G" for o in off)}
    if oneway:
        p["oneway"] = True
    if lanes:
        p["lanes"] = lanes
    return p


# Roundabouts: one way, as OpenStreetMap's junction=roundabout says, whatever the oneway tag.
check("a roundabout's ring is one way", el.is_oneway({"highway": "primary", "junction": "roundabout"}))
check("a two-way primary is not", not el.is_oneway({"highway": "primary"}))
check("a slip road and a motorway are", el.is_oneway({"highway": "primary_link"}) and el.is_oneway({"highway": "motorway"}))
check("a roundabout's ring takes a one-way carriageway's width",
      el.width({"highway": "primary", "junction": "roundabout"}) == el.ONE_WAY_W["primary"])

# Footbridges: over a road at the ground, FOOT_M; across a deck 7 m up, FOOT_M over the deck; ending on a deck's edge,
# stopped there at the deck's level.
deck = piece("d", [(0, 0), (200, 0)], [7.0] * 2, oneway=False)
road = piece("g", [(0, 50), (200, 50)], [0.0] * 2, oneway=False)
h, line = el.over_decks(LineString([(100, -20), (100, 20)]), [deck])
check("a footbridge across a deck stands FOOT_M over it", h == 7.0 + el.FOOT_M, f"{h}")
h, line = el.over_decks(LineString([(100, 30), (100, 70)]), [deck, road])
check("a footbridge over a road at the ground stands FOOT_M over the ground", h == el.FOOT_M and line.length == 40, f"{h}")
h, line = el.over_decks(LineString([(100, 30), (100, 2.4)]), [deck])
reach = deck["w"] / 2 + 2.15
check("a footbridge ending on a deck stops at its edge, at its level",
      h == 7.0 and abs(min(y for _, y in line.coords) - reach) < 0.05, f"{h}, ends at {min(y for _, y in line.coords):.2f} m")

h, line = el.over_decks(LineString([(20, 3.0), (180, 3.0)]), [deck])
check("a footway along a deck, on it, is its sidewalk: no footbridge", line is None)

# Twin carriageways 7.2 m apart drawn 11.1 m wide (three lanes each): narrowed to the room between them.
east = piece("e", [(x, 0) for x in range(0, 401, 20)], [7.0] * 21, w=11.1, lanes=3)
west = piece("w", [(x, 7.2) for x in range(400, -1, -20)], [7.0] * 21, w=11.1, lanes=3)
n = el.fit_twins([east, west])
room = 7.2 - el.TWIN_BARRIER_M
check("both carriageways of a pair are narrowed to the room between them", n == 2 and east["w"] == west["w"] == round(max(el.TWIN_MIN_W, room), 1),
      f"{east['w']}, {west['w']}")
check("their lanes as many as fit at 3 m", east["lanes"] == 2 and west["lanes"] == 2, f"{east['lanes']}")
check("their asphalt no longer overlaps", east["w"] / 2 + west["w"] / 2 <= 7.2 - el.TWIN_BARRIER_M + 1e-6)
# Not narrowed: the pair 30 m apart; two carriageways the same way (a slip road beside its road); the wrong side
# (left-hand traffic); a cut under a deck.
far_e = piece("fe", [(x, 100) for x in range(0, 401, 20)], [7.0] * 21, w=11.1)
far_w = piece("fw", [(x, 130) for x in range(400, -1, -20)], [7.0] * 21, w=11.1)
same_a = piece("sa", [(x, 200) for x in range(0, 401, 20)], [7.0] * 21, w=11.1)
same_b = piece("sb", [(x, 207) for x in range(0, 401, 20)], [7.0] * 21, w=11.1)
wrong_e = piece("we", [(x, 307) for x in range(0, 401, 20)], [7.0] * 21, w=11.1)
wrong_w = piece("ww", [(x, 300) for x in range(400, -1, -20)], [7.0] * 21, w=11.1)
low_e = piece("le", [(x, 400) for x in range(0, 401, 20)], [7.0] * 21, w=11.1)
low_w = piece("lw", [(x, 407) for x in range(400, -1, -20)], [-7.0] * 21, w=11.1)
n = el.fit_twins([far_e, far_w, same_a, same_b, wrong_e, wrong_w, low_e, low_w])
check("no pair far apart, the same way, the wrong way round or at another level is narrowed", n == 0, f"{n}")

# Twin carriageways the record gives three lanes each (LANE_FACTS: Circle's viaduct), drawn 7.2 m apart: they keep their
# width and move apart, each half the room it lacks; a slip road ending on one comes with it at its end, not 60 m on;
# a foot at the ground stays on its drawn line.
se = piece("se", [(x, 0) for x in range(0, 401, 20)], [0.0] + [7.0] * 19 + [0.0], w=11.1, lanes=3)
sw = piece("sw", [(x, 7.2) for x in range(400, -1, -20)], [0.0] + [7.0] * 19 + [0.0], w=11.1, lanes=3)
se["surveyed"] = sw["surveyed"] = True
slip_s = piece("ss", [(200 + d, -d * 0.5) for d in range(0, 101, 10)], [7.0 - 0.06 * d for d in range(0, 101, 10)], w=6.5)
check("a surveyed pair is not narrowed", el.fit_twins([se, sw]) == 0 and se["w"] == 11.1 == sw["w"])
moved = el.separate_twins([se, sw, slip_s])
gap = abs(se["pts"][10][1] - sw["pts"][10][1])
check("it moves apart to its widths and the barrier between", moved > 0 and abs(gap - (11.1 + el.TWIN_BARRIER_M)) < 0.3, f"{gap:.2f} m apart")
check("each away from the other (Ghana drives on the right: the other on its left)", se["pts"][10][1] < 0 and sw["pts"][10][1] > 7.2)
check("its feet at the ground stay where the ground's roads meet them", se["pts"][0] == [0, 0] and sw["pts"][-1] == [0, 7.2])
check("a slip road ending on it comes with it there, and not 60 m on",
      abs(slip_s["pts"][0][1] - se["pts"][10][1]) < 0.01 and slip_s["pts"][6] == [260, -30.0], f"{slip_s['pts'][0]}, {slip_s['pts'][6]}")
check("a deck the record names takes its lanes", el.lane_fact({"name": "Tema Motorway Interchange"}, 1, {"highway": "trunk", "oneway": "yes", "name": "Harbour Road"}, True, 7.0) == (2, 3.65))
check("a ramp at another level, or a way the record does not name, does not",
      el.lane_fact({"name": "Tema Motorway Interchange"}, 1, {"highway": "trunk", "oneway": "yes", "name": "Harbour Road"}, True, 14.0) is None and
      el.lane_fact({"name": "Tema Motorway Interchange"}, 1, {"highway": "trunk", "oneway": "yes", "name": "Aflao Road"}, True, 7.0) is None)

# A slip road leaving a deck's point at 15 degrees, drawn ramping down at once (at GRADE): over the deck's asphalt it runs at the
# deck's level, then comes down to its own.
main = piece("m", [(x, 0) for x in range(0, 201, 5)], [7.0] * 41, kind="D" * 41, w=7.6)
import math  # noqa: E402
slip_pts = [(100 + d * math.cos(math.radians(15)), -d * math.sin(math.radians(15))) for d in range(0, 121, 5)]
slip_off = [max(0.0, 7.0 - el.GRADE * d) for d in range(0, 121, 5)]
slip = piece("s", slip_pts, slip_off, w=6.5)
moved = el.level_merges([main, slip])
over = [k for k, q in enumerate(slip["pts"]) if 0 < k < len(slip_pts) - 1 and LineString(main["pts"]).distance(Point(q)) <= (7.6 + 6.5) / 2 - 0.3]
check("the slip road runs at the deck's level where the two overlap", moved > 0 and over and all(abs(slip["off"][k] - 7.0) < 0.01 for k in over),
      f"{len(over)} points over the deck, {moved} moved")
grades = [abs(slip["off"][k + 1] - slip["off"][k]) / 5 for k in range(len(slip_pts) - 1)]
check("and comes down to its own level at a grade a car drives", max(grades) <= el.MERGE_MAX + 1e-6, f"{max(grades):.3f}")
check("its far end keeps its own level", slip["off"][-1] == slip_off[-1])
# Two roads parting at a shallow angle bound for levels far apart (a scissors): left as they are.
up = piece("u", [(x, 0) for x in range(0, 41, 5)], [4.0 + 0.09 * x for x in range(0, 41, 5)], w=7.6)
down_pts = [(x, x * math.tan(math.radians(10))) for x in range(0, 31, 5)]
down = piece("dn", down_pts, [max(0.0, 4.0 - 0.09 * x) for x in range(0, 31, 5)], w=7.0)
before = list(down["off"])
el.level_merges([up, down])
check("a scissors is not levelled into a grade no car drives", down["off"] == before, f"{down['off']}")

ramp = piece("r", [(0, 0), (200, 0)], [3.0, 3.0], w=7.6)

# What stands on the ground keeps off the structures' roads where they are low: on a ramp and in a cut, under a deck
# lower than it; not under a deck high over it, nor beside the road.
ground = bd.structure_ground([ramp, piece("c", [(0, 100), (200, 100)], [-6.0, -6.0], w=7.6),
                              piece("h", [(0, 200), (200, 200)], [14.0, 14.0], kind="DD", w=7.6)])
check("a building on a ramp stands in its road", bd.under_structure(box(90, -2, 100, 2), ground, 6.0))
check("a parked car in a cut stands in its road", bd.under_structure(Point(50, 101).buffer(2.3), ground, bd.CAR_TOP_M))
check("a street light under a deck 14 m up does not", not bd.under_structure(Point(50, 200), ground, bd.LAMP_TOP_M - 3.0))
check("a 10 m street light under it does", bd.under_structure(Point(50, 200), ground, bd.LAMP_TOP_M + 3.0))
check("a building beside the ramp does not", not bd.under_structure(box(90, 10, 100, 20), ground, 9.0))

check("no car is parked by a ramp's foot", bd.FOOT_CLEAR_M >= 30)

# A carriageway moved apart from its twin is taken off the ground's roads by OpenStreetMap's line, where Overture's
# road (the same way) still runs.
moved_p = piece("mv", [(0, 3.6), (200, 3.6)], [7.0, 7.0], kind="DD")
moved_p["drawn"] = [[0, 0], [200, 0]]
kept, gone = bd.cut_structures([("same", "primary", "", LineString([(10, 0.2), (190, 0.2)]))], [moved_p])
check("a carriageway moved apart is taken off the ground by its drawn line", not kept and gone > 150, f"{gone:.0f} m")

# relief.py's cut floors: a cut from its mouth at x = 0 (on the ground, where a road on the ground meets it) down at
# 9 % to 7 m under the ground, on flat ground at 10 m. The floor is held under the cut's road inside the cut, and the
# ground beyond its mouth stays where the road on the ground lies (no trough to step out of onto the cut's road).
import numpy as np  # noqa: E402
import relief as rl  # noqa: E402
grid = rl.Grid(-80.0, -40.0, 31, 11, 8.0)
h = np.full((grid.nz, grid.nx), 10.0, np.float32)
xs = np.arange(0.0, 121.0, 5.0)
off = -np.minimum(7.0, 0.09 * xs)
cutpts = np.column_stack([xs, np.zeros_like(xs)])
other = np.zeros(h.shape, bool)
other[5, :10] = True                                   # the road on the ground, x -80 to -8, z 0
rl.hold_cut_floors(h, grid, [("cut", 7.6, cutpts, 10.0 + off - rl.CUT_UNDER_M, None, off)], other)
node = lambda x, z: h[int(round((z - grid.z0) / grid.step)), int(round((x - grid.x0) / grid.step))]
check("a cut's floor is held under its road inside it", node(64, 0) <= 10.0 - 0.09 * 64 - rl.CUT_UNDER_M + 1e-3, f"{node(64, 0):.2f}")
check("the ground beyond its mouth stays at the road on the ground", all(abs(node(-8, z) - 10.0) < 1e-3 for z in (-8, 0, 8)),
      ", ".join(f"{node(-8, z):.2f}" for z in (-8, 0, 8)))

# The same cut's floor at its mouth: at the road itself (a floor held under it there sank the mouth into a kerb).
check("a cut's floor rises to its road at the mouth", abs(rl.cut_floor([10.0], [0.0])[0] - 10.0) < 1e-6 and
      abs(rl.cut_floor([3.0], [-7.0])[0] - (3.0 - rl.CUT_UNDER_M)) < 1e-6)
h2 = np.full((grid.nz, grid.nx), 10.0, np.float32)
rl.hold_cut_floors(h2, grid, [("cut", 7.6, cutpts, rl.cut_floor(10.0 + off, off), None, off)], other)
node2 = lambda x, z: h2[int(round((z - grid.z0) / grid.step)), int(round((x - grid.x0) / grid.step))]
check("the ground at a cut's mouth is at its road, no kerb", abs(node2(0, 0) - 10.0) < 0.05, f"{node2(0, 0):.2f}")

# A bridge at the ground's level (off 0) over a carved ground 0.7 m under it: its end brought to the ground, its
# offset kept, so the game still joins it to the road there (Tetteh Quarshie's motorway).
flat = np.full((grid.nz, grid.nx), 9.3, np.float32)
br = {"pts": [[-40.0, 0.0], [-20.0, 0.0], [0.0, 0.0]], "y": [10.0, 10.0, 10.0], "off": [0.0, 0.0, 0.0], "kind": "DDD"}
rl.mouths_to_ground([br], flat, grid)
check("a bridge's end at the ground's level is brought to the ground", abs(br["y"][0] - 9.3) < 0.01 and br["off"] == [0.0, 0.0, 0.0],
      f"{br['y']}, off {br['off']}")

# Carried on over a hole beyond its end, along the road there, to where the ground is back; never past a junction.
hole = np.full((grid.nz, grid.nx), 10.0, np.float32)
hole[:, 9:11] = 3.0                                   # x -8 to 8: a pit 7 m deep beyond the bridge's end at x = -10
for junction in (False, True):
    b2 = {"pts": [[-60.0, 0.0], [-30.0, 0.0], [-10.0, 0.0]], "y": [10.0] * 3, "off": [0.0] * 3, "kind": "DDD"}
    roads2 = [("primary", 7.0, np.array([[-10.0, 0.0], [10.0, 0.0], [40.0, 0.0]]))]
    if junction:                                      # a side road leaves in the pit, at x = 0
        roads2 = [("primary", 7.0, np.array(r)) for r in ([[-10.0, 0.0], [0.0, 0.0]], [[0.0, 0.0], [40.0, 0.0]], [[0.0, 0.0], [0.0, 30.0]])]
    n = rl.bridges_over_holes([b2], hole, grid, roads2)
    if junction:
        check("a bridge is never carried past a junction", n == 0 and len(b2["pts"]) == 3)
    else:
        check("a bridge at the ground's level is carried on over a hole beyond its end", n == 1 and b2["pts"][-1][0] > 8.0 and
              abs(b2["y"][-1] - 10.0) < 0.2, f"to x {b2['pts'][-1][0]:.1f}")

# A road on the ground 12 m beside a walled cut 6 m deep (Ako Adjei's service roads): a piece at the ground's level
# over the run the cut's floor takes, a point on; not a road 60 m off, nor the cut's road on beyond its mouth, nor
# beside a cut with grassed banks.
def beside(style):
    xs5 = np.arange(0.0, 101.0, 5.0)
    cut = piece("c", [(x, 0.0) for x in xs5], [-6.0 if x < 60 else -6.0 * (100 - x) / 40 for x in xs5])
    cut.update(site="s", style=style)
    pos, wverts, wby = {}, {}, {}
    for wid, z, x0, x1 in ((1, 12.0, -100.0, 200.0), (2, 60.0, 0.0, 100.0), (3, 0.0, 100.0, 160.0)):
        vs = []
        for x in np.arange(x0, x1 + 0.1, 5.0):
            v = (wid, float(x)); pos[v] = (float(x), z); vs.append(v)
        wverts[wid] = vs
        wby[wid] = {"tags": {"highway": "primary", "oneway": "yes", "name": f"w{wid}"}}
    pieces = [cut]
    n = el.beside_cuts(pieces, wverts, pos, wby, [], set())
    return n, pieces[1:]
n, lv = beside("ako-adjei")
side = [p for p in lv if p["osm"] == 1]
check("a road on the ground beside a walled cut is carried at the ground's level over the cut's floor", n == 1 and len(side) == 1 and
      side[0]["level"] and set(side[0]["off"]) == {0.0} and side[0]["kind"].strip("G") == "F" * len(side[0]["kind"].strip("G")),
      f"{n} pieces, {[p['kind'] for p in lv]}")
xs_ = [q[0] for q in side[0]["pts"]] if side else [0]
check("from a point before the cut is deep to past where it comes up", min(xs_) <= 0.0 and 90.0 <= max(xs_) <= 100.0,
      f"x {min(xs_):.0f} to {max(xs_):.0f}")
check("not beside a cut with grassed banks", beside("tetteh-quarshie")[0] == 0)

# relief.py's shelves: a cut 6.5 m deep in flat ground at 10 m, the floor and the pit beside it at 3 m to 16 m off the
# line (the 8 m grid back at the banks' top at 24 m): a shelf from its wall's coping to the ground, both sides; one
# that meets a lower road (the other carriageway's cut) stops short of it.
g3 = rl.Grid(-80.0, -40.0, 31, 11, 8.0)
pit = np.full((g3.nz, g3.nx), 10.0, np.float32)
pit[3:8, :] = 3.0                                     # z -16 to 16
xs3 = np.arange(-40.0, 41.0, 5.0)
c3 = {"pts": [[float(x), 0.0] for x in xs3], "w": 7.6, "y": [3.5] * len(xs3), "off": [-6.5] * len(xs3), "kind": "C" * len(xs3), "style": "ako-adjei"}
rl.cut_shelves([c3], pit, g3)
mid = c3.get("shelf", [[0, 0, 0, 0]] * len(xs3))[len(xs3) // 2]
want = 24.0 - 3.8 - rl.SHELF_FROM_M
check("a walled cut's shelf reaches from its coping to the ground beyond the pit", all(want - 1.5 < w < want + 1.0 for w in mid[:2]) and
      mid[2:] == [10.0, 10.0], f"{mid}, about {want:.1f}")
# The ground beyond 1 m under the banks' top (a valley the natural ground smooths over): the shelf at its level.
low_pit = pit - 1.0
low_pit[3:8, :] = 3.0
c5 = {**c3, "pts": [[float(x), 0.0] for x in xs3]}
rl.cut_shelves([c5], low_pit, g3)
m5 = c5["shelf"][len(xs3) // 2]
check("where the ground beyond lies under the banks' top, the shelf is at the ground's level", all(abs(t - 9.0) < 0.05 for t in m5[2:]) and
      all(w < want + 1.0 for w in m5[:2]), f"{m5}")
c4 = {**c3, "pts": [[float(x), 0.0] for x in xs3]}
low = {"pts": [[-40.0, 13.0], [40.0, 13.0]], "w": 7.6, "y": [3.5, 3.5], "off": [-6.5, -6.5], "kind": "CC", "style": "ako-adjei"}
rl.cut_shelves([c4, low], pit, g3)
m4 = c4["shelf"][len(xs3) // 2]
# Game's left is (tz, -tx): along +x, the left is -z; the lower road is at +z, on the right.
check("a shelf stops short of a lower road beside it", m4[1] < 13.0 - 3.8 - 3.8 - rl.SHELF_FROM_M + 0.01 and m4[0] > want - 1.5, f"{m4}")

# A zigzag at a piece's end (Graphic Road's slip road off Obetsebi Lamptey's fork): straightened from the end.
import math as _m  # noqa: E402
zz = piece("zz", [(0, 0), (0.6, 5.2), (-6.0, 12.0), (-10.0, 16.0), (-14.0, 20.0), (-18.0, 24.0)], [4.0, 3.9, 3.8, 3.7, 3.6, 3.5])
el.unzig([zz])


def max_turn(pts, upto):
    t, s = 0.0, 0.0
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        s += _m.dist(a, b)
        if s > upto:
            break
        u, v = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        t = max(t, abs(_m.degrees(_m.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))))
    return t


check("a zigzag at a piece's end is straightened, its end and OpenStreetMap's line kept",
      max_turn(zz["pts"], el.ZIG_M) < el.ZIG_DEG and zz["pts"][0] == [0, 0] and len(zz["drawn"]) == 6
      and len(zz["pts"]) == len(zz["off"]) == len(zz["kind"]), f"{zz['pts']}")

# A slip road that leaves its road at a shallow angle bound for another level (Dr. Busia High Street's up to
# Obetsebi Lamptey): moved aside from the start of the road leading in, its road left where it is.
up = piece("up", [(0, -40 + 5 * k) for k in range(9)], [0.0] * 9, w=11.1)
up["osm"] = 10
main = piece("main", [(0.25 * k, 5 * k) for k in range(9)], [0.0] * 9, w=11.1)
main["osm"] = 11
ramp = piece("ramp", [(0.6 * k, 5 * k) for k in range(17)], [0.0] + [0.4 * k for k in range(1, 17)])
ramp["osm"] = 12
main0 = [list(q) for q in main["pts"]]
moved = el.part_forks([up, main, ramp], [])
gaps = [LineString(main["pts"]).distance(Point(q)) for q, o in zip(ramp["pts"], ramp["off"]) if 20 <= q[1] <= 40 and o > 0.3]
check("a slip road over its road's lanes at another level is moved aside, from the road leading in",
      moved == 1 and ramp["pts"][0] == up["pts"][0] and main["pts"] == main0 and min(gaps) > 11.1 / 2,
      f"start {ramp['pts'][0]}, gaps {[round(g, 1) for g in gaps]}")
edge = piece("edge", [(1.2 * k, 5 * k) for k in range(9)], [0.0] + [0.4 * k for k in range(1, 9)])
edge["osm"] = 13
main2 = piece("main2", [(0, 5 * k) for k in range(9)], [0.0] * 9, w=11.1)
main2["osm"] = 14
check("one that only meets its road's edge, a gore's length, is left as drawn",
      el.part_forks([main2, edge], []) == 0)

print(f"{failures} failure(s)")
sys.exit(1 if failures else 0)
