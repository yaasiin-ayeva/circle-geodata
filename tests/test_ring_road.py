"""Checks the pieces that build the Ring Road area (scripts/build_district.py) and tile it (scripts/export_glb.py) on a
synthetic dual carriageway: the corridor outline less a neighbour's box, the ground that does not overlap the
neighbour's, roads cut only at shared vertices, the route lap that keeps to one-way carriageways, the one-way pieces of
the traffic network, and the tile each cell goes to.

    python3 tests/test_ring_road.py
"""
import os
import sys

import numpy as np
from shapely.geometry import LineString, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402
import export_glb  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# Metres, x east and y north; "lon/lat" here are the same metres (local() is the identity).
ident = lambda g: g
# A dual carriageway along y = 0 from x = -600 to 600: eastbound at y = -5, westbound at y = +5, each a one-way segment
# in its direction, with a U-turn link at each end inside the corridor, and a side street crossing the westbound
# carriageway at x = 100 through a shared vertex. A flyover crosses both at x = -200 without sharing a vertex.
E, W = "Test Road Eastbound", "Test Road Westbound"
roads = [
    ("e", "primary", E, LineString([(-600, -5), (100, -5), (600, -5)])),
    ("w", "primary", W, LineString([(600, 5), (100, 5), (-600, 5)])),
    ("uw", "primary", "", LineString([(-560, 5), (-565, 0), (-560, -5)])),        # west U-turn: westbound to eastbound
    ("ue", "primary", "", LineString([(560, -5), (565, 0), (560, 5)])),           # east U-turn: eastbound to westbound
    ("side", "residential", "Side Street", LineString([(100, 5), (100, 200)])),
    ("fly", "primary", "Flyover", LineString([(-200, -100), (-200, 100)])),
]
# The joins at the U-turns: the carriageways carry those points as vertices.
roads[0] = ("e", "primary", E, LineString([(-600, -5), (-560, -5), (100, -5), (560, -5), (600, -5)]))
roads[1] = ("w", "primary", W, LineString([(600, 5), (560, 5), (100, 5), (-560, 5), (-600, 5)]))

# The corridor less a neighbour's box (x 0..200, y -50..50) registered as an area.
bd.AREAS["test-box"] = {"west": 0.0, "east": 200.0, "south": -50.0, "north": 50.0}
corridor = {"corridor": {"roads": (E, W), "half_width_m": 100.0, "within": {"west": -500.0, "east": 500.0, "south": -300.0, "north": 300.0}},
            "minus": ["test-box"]}
clip, region, minus = bd.area_outline(corridor, roads, ident)
check("the corridor is the roads buffered, inside its box", abs(region.bounds[0] + 500) < 1e-6 and abs(region.bounds[3] - 105) < 1.5,
      f"bounds {[round(v) for v in region.bounds]}")
check("the content leaves the neighbour's box out", clip.intersection(box(0, -50, 200, 50)).area < 1e-6 and
      abs(clip.area + 200 * 100 - region.area) < 1.0, f"{clip.area:.0f} + 20000 vs {region.area:.0f}")
check("a box area is its own box and lap region", bd.area_outline(bd.AREAS["test-box"], roads, ident)[0].equals(box(0, -50, 200, 50)))

ground = bd.ground_region(clip, minus, ident, 60.0)
check("the ground stays off the neighbour's ground (its box widened by the margin)",
      ground.intersection(box(-60, -110, 260, 110)).area < 1e-6)
check("the ground covers the content, but where the neighbour lays its own", clip.difference(box(-60, -110, 260, 110)).difference(ground).area < 1e-6)
mesh = bd.Mesh()
bd.lay_ground(mesh, ground)
objs = {k[0] for k in mesh.groups}
tri_area = sum(abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2 for t in mesh.groups.values() for a, b, c, _ in t)
check("the ground is cut on the 250 m grid into Ground_<i>_<j>", all(o.startswith("Ground_") for o in objs) and "Ground_-1_0" in objs,
      f"{len(objs)} cells")
check("its triangles cover the ground once", abs(tri_area - ground.area) < 1.0, f"{tri_area:.0f} vs {ground.area:.0f}")
up = [n for t in mesh.groups.values() for a, b, c, _ in t
      for n in [np.cross(np.subtract(b, a), np.subtract(c, a))[2]]]
check("every ground triangle faces up", all(n > 0 for n in up))

pieces = bd.split_at_shared_vertices([l for *_, l in roads])
west = [p for whole, p in pieces if whole is roads[1][3]]
check("roads are cut where another road shares a vertex", len(west) == 4, f"{len(west)} pieces of the westbound carriageway")
carriageways = [p for whole, p in pieces if whole is roads[0][3] or whole is roads[1][3]]
check("a flyover sharing no vertex cuts nothing", not any(abs(c[0] + 200) < 1e-6 for p in carriageways for c in (p.coords[0], p.coords[-1])))

# The lap: out west on the westbound carriageway, back east on the eastbound one, round and back to the start.
class Flat:
    """Stands in for the WGS84 to UTM transformer: the route points are already metres."""
    @staticmethod
    def transform(lon, lat):
        return lon, lat


# Route points (lat, lon) = (y, x): westbound at the side street, eastbound past the west U-turn, westbound past the
# east one; the lap comes back to the first.
spec = {"route": [(5, 100), (-5, -560), (5, 560)], "one_way": (E, W)}
lap = bd.route_lap(roads, box(-1000, -1000, 1000, 1000), spec, Flat, (0.0, 0.0))
path = list(zip(lap["path"][0::2], lap["path"][1::2]))
check("the route lap is closed through the U-turns", 2200 < lap["length_m"] < 2350, f"{lap['length_m']:.0f} m")
wrong = sum(1 for (x0, y0), (x1, y1) in zip(path, path[1:])
            if (abs(y0 - 5) < 0.5 and abs(y1 - 5) < 0.5 and x1 > x0 + 0.1) or (abs(y0 + 5) < 0.5 and abs(y1 + 5) < 0.5 and x1 < x0 - 0.1))
check("the lap never drives a one-way carriageway against its direction", wrong == 0, f"{wrong} steps against")
check("the spawn faces west on the westbound carriageway", abs(lap["spawn"]["z"] - 5) < 0.5 and abs(lap["spawn"]["yaw"] + 90) < 1,
      str(lap["spawn"]))
check("route points snap to the network", max(lap["snapped_m"]) < 0.01, str(lap["snapped_m"]))

net = bd.traffic_network(roads, box(-1000, -1000, 1000, 1000), (E, W))
one = [r for r in net if r.get("oneway")]
check("one-way pieces are flagged, the others not", one and all(r["name"] in (E, W) for r in one) and
      not any(r.get("oneway") for r in net if r["name"] not in (E, W)))
check("each one-way piece runs its carriageway's way", all((r["pts"][2] - r["pts"][0] > 0) == (r["name"] == E) for r in one))

# Tiles: a cell named Buildings_<i>_<j> (250 m) goes to the 500 m tile holding it; an unnamed object by its vertices.
V = np.array([[-1200.0, 0.0, 300.0], [-1210.0, 0.0, 310.0], [-1190.0, 3.0, 320.0]], dtype=np.float32)   # OBJ: X west
check("cells go to the tile that holds them", export_glb.tile_of("Buildings_-3_1", [], V, 500) == (-2, 0) and
      export_glb.tile_of("Roads_2_-3", [], V, 500) == (1, -2))
check("an object without a cell goes by its vertices", export_glb.tile_of("Landmarks", [(1, 0, 0), (2, 0, 0), (3, 0, 0)], V, 500) == (2, 0))

print("ring road: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
