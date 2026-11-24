"""Checks Starlets '91 Road's kit (circle-game docs/starlets-91-plan.md, stage 2): its data file
(aoi/starlets-91-kit.json, scripts/starlets_kit_data.py) and the rules of scripts/avenue_kit.py it adds (a ministry's
grounds, a compound's sheds, the corridor's shop segments), and that the avenue's kit is unchanged by them.

    python3 tests/test_starlets_kit.py
"""
import os
import sys
from collections import Counter

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import avenue_kit  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# --- The data -----------------------------------------------------------------------------------------------------
data = avenue_kit.load(avenue_kit.STARLETS)
corr = shapely.union_all([Polygon(r) for r in data["corridor"]])
check("the corridor is one valid area", corr.is_valid and not corr.is_empty)
km2 = corr.area * (111_320 * 110_574) / 1e6 * 0.995
check("the corridor's area about the plan's 0.66 km2", 0.6 < km2 < 0.75, f"{km2:.2f}")
line = LineString(data["line"])
check("the line runs from Independence Avenue (west) to Castle Road (north-east)",
      line.coords[0][0] < -0.203 and line.coords[-1][0] > -0.1905 and line.coords[-1][1] > 5.5525)
check("four segments A to D", [k for k, *_ in data["segments"]] == list("ABCD"))
check("its shop segments: A and D (the plan's Appendix C)", data["shop_segments"] == ["A", "D"])
names = {b["osm"]: b for b in data["buildings"]}
treats = Counter(b["treat"] for b in data["buildings"])
check("the plan's OSM buildings named with its types", len(names) >= 300 and {"T1", "T2", "T5", "T9", "T10", "VOL"} <= set(treats),
      dict(treats))
check("every named building has a point and an area", all(len(b["at"]) == 2 and b["area_m2"] > 0 for b in names.values()))
check("the Ministry of Food and Agriculture: a ministry block", names["way/206834992"]["treat"] == "T5")
check("the police station: a plain volume", names["way/157017164"]["treat"] == "VOL")
check("Premier Towers waits for the tower generator", names["way/156362145"]["treat"] == "T3")
check("the stadium's bowl and main stand are kept (#271)",
      names["way/169866131"]["treat"] == "KEEP" and names["way/169866120"]["treat"] == "KEEP")
check("the Civil Service Polyclinic keeps OSM's two levels", names["way/439346942"]["levels"] == 2)
lots = {l["lot"]: l for l in data["lots"]}
check("the Ministries' grounds: a ministry's grounds (T5)", lots["B25"]["treat"] == "T5" and lots["B25"]["tags"].get("landuse") == "civic_admin")
check("the stadium's compound: its sheds (T10), its model kept by name", lots["B42"]["treat"] == "T10")
check("no lot of the avenue's plan is here", not any(l["lot"].startswith("F") for l in data["lots"]))

# --- The rules it adds --------------------------------------------------------------------------------------------
c = avenue_kit.choose
ministries = {"treat": "T5", "tags": {"landuse": "civic_admin"}}
check("a block on the Ministries' grounds: a ministry block", c(300, None, None, [], lot=ministries) == "T5")
check("an OSM shed there under 40 m2: a kiosk", c(25, None, None, [], lot=ministries) == "T10")
check("a machine-learnt shed there under 40 m2: as drawn", c(25, None, None, [], lot=ministries, ml=True) is None)
check("a hospital on the grounds stays a hospital", c(600, None, "hospital", [], lot=ministries) == "T7")
check("on the stadium's compound: a shed", c(150, None, None, [], lot={"treat": "T10", "tags": {"leisure": "stadium"}}) == "T10")
check("Osu's blocks at the east end (D): shop blocks", c(180, None, None, [], segment="D", shop=data["shop_segments"]) == "T9")
check("the old centre's (A): shop blocks", c(180, None, None, [], segment="A", shop=data["shop_segments"]) == "T9")
check("by the Ministries (B): a villa", c(180, None, None, [], segment="B", shop=data["shop_segments"]) == "T1")
check("a building the plan names takes its type", c(500, None, None, [], named={"treat": "T2"}) == "T2")
check("one it keeps for its own stage keeps the generic massing", c(500, None, None, [], named={"treat": "T3"}) is None
      and c(500, None, None, [], named={"treat": "KEEP"}) is None)
check("OSM's levels are built as mapped", avenue_kit.storeys_for("T5", 2, 7) == 7)
check("an estimate is held to the ministry block's range", avenue_kit.storeys_for("T5", 1, None) == 3
      and avenue_kit.storeys_for("T5", 12, None) == 6)

# The avenue's kit is unchanged: segment D of the avenue is no shop segment, and its lots carry neither new treatment.
avenue = avenue_kit.load()
check("the avenue's segment D: no shop block", c(180, None, None, [], segment="D") == "T1")
check("the avenue's lots carry no T5 or T10 lot treatment", not any(l["treat"] in ("T5", "T10") for l in avenue["lots"]))
check("the avenue's data has no shop segments (its F by default)", "shop_segments" not in avenue)

# --- The Avenue class on the data, in metres east and north of the road's west end --------------------------------
LON0, LAT0 = line.coords[0]
KX, KY = 111_320 * np.cos(np.radians(LAT0)), 110_574
to_m = lambda g: shapely.transform(g, lambda xy: np.column_stack(((xy[:, 0] - LON0) * KX, (xy[:, 1] - LAT0) * KY)))
st = avenue_kit.Avenue(data, to_m, "starlets")
check("the line measures the plan's 1.76 km", 1700 < st.line.length < 1800, f"{st.line.length:.0f} m")
check("its shop segments are read", st.shop == ("A", "D") and avenue_kit.Avenue(avenue, to_m).shop == ("F",))
check("the west end is segment A, Castle Road segment D", st.segment(Point(st.line.coords[0]).buffer(5)) == "A"
      and st.segment(Point(st.line.coords[-1]).buffer(5)) == "D")
mofa = names["way/206834992"]
found = st.named_of(to_m(Point(mofa["at"])).buffer(np.sqrt(mofa["area_m2"] / np.pi)))
check("the Ministry of Food and Agriculture is found by its point", found is not None and found["lot"] == "B16")
check("a footprint on the Ministries' grounds is in lot B25 or a ministry's own",
      (st.lot_of(to_m(Point(-0.1980288, 5.5526332)).buffer(3)) or {}).get("treat") == "T5")
mid = st.line.interpolate(0.5, normalized=True)
check("a footprint 1 km off is not in the corridor", not st.inside(Point(mid.x, mid.y + 1000).buffer(5)))

# The ministry blocks' brise-soleil: on the walls facing the road within its reach; on every wall on the avenue.
check("the avenue's ministries keep their fins on every wall", avenue_kit.Avenue(avenue, to_m).fin_wall is None)
reach = data["fins"]["reach_m"]
q = np.array([mid.x, mid.y])
seen = st.fin_wall
check("a wall facing the road 30 m off carries fins", seen(q + [-5, -30], q + [5, -30], np.array([0.0, 1.0]))
      or seen(q + [-5, 30], q + [5, 30], np.array([0.0, -1.0])))
check("its back wall does not", not (seen(q + [-5, -30], q + [5, -30], np.array([0.0, -1.0]))
                                     and seen(q + [-5, 30], q + [5, 30], np.array([0.0, 1.0]))))
check("nor a wall facing the road beyond the reach", not seen(q + [-5, -(reach + 40)], q + [5, -(reach + 40)], np.array([0.0, 1.0]))
      and not seen(q + [-5, reach + 40], q + [5, reach + 40], np.array([0.0, -1.0])))

print("starlets kit: " + ("all checks passed" if not failures else f"{failures} FAILED"))
sys.exit(1 if failures else 0)
