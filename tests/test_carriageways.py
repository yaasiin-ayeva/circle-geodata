"""Checks the detection of dual carriageways (scripts/build_district.py find_carriageways) on synthetic roads.

    python3 tests/test_carriageways.py
"""
import os
import sys

from shapely.geometry import LineString

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# A boulevard along x, 400 m: Ghana drives on the right, so the eastbound carriageway (drawn west to east) runs on the
# south side, the westbound one (drawn east to west) 20 m north of it, across the median.
east = ("e", "trunk", "Liberation Road", LineString([(0, 0), (400, 0)]))
west = ("w", "trunk", "Liberation Road", LineString([(400, 20), (0, 20)]))
found = bd.find_carriageways([east, west])
check("both carriageways of a boulevard are found", set(found) == {"e", "w"}, str(found))
check("each is about 55% of the 20 m between them, within 6.5-10.5 m", all(10.5 >= w >= 6.5 for w in found.values())
      and abs(found["e"] - 11.0) <= 0.6, str(found))

# An unnamed link alongside counts as the other carriageway.
link = ("l", "trunk", "", LineString([(400, 20), (0, 20)]))
check("an unnamed carriageway pairs with a named one", set(bd.find_carriageways([east, link])) == {"e", "l"})

# Not carriageways: one two-way road; two parallel streets drawn the same way; the pair the wrong way round (that is
# left-hand traffic); two roads of different names; residential streets; a pair 80 m apart.
single = ("s", "trunk", "A Road", LineString([(0, 100), (400, 100)]))
same = [("a", "primary", "B Road", LineString([(0, 200), (400, 200)])),
        ("b", "primary", "B Road", LineString([(0, 220), (400, 220)]))]
wrong = [("c", "primary", "C Road", LineString([(400, 300), (0, 300)])),
         ("d", "primary", "C Road", LineString([(0, 320), (400, 320)]))]
names = [("f", "primary", "D Road", LineString([(0, 400), (400, 400)])),
         ("g", "primary", "E Road", LineString([(400, 420), (0, 420)]))]
small = [("h", "residential", "F Street", LineString([(0, 500), (400, 500)])),
         ("i", "residential", "F Street", LineString([(400, 515), (0, 515)]))]
far = [("j", "primary", "G Road", LineString([(0, 600), (400, 600)])),
       ("k", "primary", "G Road", LineString([(400, 680), (0, 680)]))]
none = bd.find_carriageways([single] + same + wrong + names + small + far)
check("no false carriageways", not none, str(none))

# The width wins over the class's in road_width, and the Ring Road's surveyed width over both.
bd.CARRIAGEWAY_W.clear()
bd.CARRIAGEWAY_W.update(found)
check("road_width: a found carriageway is narrower than its class", bd.road_width("trunk", "Liberation Road", rid="e") == found["e"]
      and bd.road_width("trunk", "Liberation Road") == bd.ROAD_WIDTH["trunk"])
check("road_width: the surveyed Ring Road keeps 7 m", bd.road_width("primary", "Ring Road East", rid="e") == bd.CARRIAGEWAY_M)

# The traffic drives each found carriageway one way, along its geometry.
area = LineString([(-10, -50), (410, -50), (410, 70), (-10, 70)]).convex_hull
net = bd.traffic_network([east, west], area)
check("the traffic drives them one way", net and all(e.get("oneway") for e in net), str([(e["name"], e.get("oneway")) for e in net]))

# The median: one line down the middle (z = 10), its width the 20 m less the two half carriageways, cut where a street
# crosses at x = 200 (a 6 m residential street: 3 m and 2 m of margin either side), and to the area.
cross = ("x", "residential", "Cross Street", LineString([(200, -40), (200, 60)]))
med = bd.find_medians([east, west, cross], area)
pts = [p for m in med for p in m["pts"]]
check("one median each side of the crossing, drawn once", len(med) == 2, str([(m["pts"][0], m["pts"][-1]) for m in med]))
check("down the middle", pts and all(abs(z - 10) < 0.2 for _, z in pts), str(pts[:4]))
check("its width is the asphalt's gap", all(abs(m["w"] - (20 - found["e"])) < 0.2 for m in med) and all(m["h"] == bd.MEDIAN_H for m in med),
      str([m["w"] for m in med]))
gap = (bd.ROAD_WIDTH["residential"] / 2 + 2)
check("open where the street crosses", not any(200 - gap + 0.5 < x < 200 + gap - 0.5 for x, _ in pts)
      and any(abs(x - (200 - gap)) < 4.5 for x, _ in pts) and any(abs(x - (200 + gap)) < 4.5 for x, _ in pts), str(sorted(x for x, _ in pts)))
check("named after its road", all(m.get("name") == "Liberation Road" for m in med))
check("no median for a two-way road", not bd.find_medians([single], area))
bd.CARRIAGEWAY_W.clear()

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
