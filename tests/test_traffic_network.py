"""Checks the traffic network export (scripts/build_district.py traffic_network) on a synthetic crossing.

    python3 tests/test_traffic_network.py
"""
import os
import sys

from shapely.geometry import LineString, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# A primary road running straight through a residential crossing, a footway and a service road beside them.
roads = [("a", "primary", "High Street", LineString([(-100, 0), (100, 0)])),
         ("b", "residential", "Side Street", LineString([(0, -80), (0, 80)])),
         ("c", "footway", "", LineString([(-50, 10), (50, 10)])),
         ("d", "service", "", LineString([(-50, -10), (50, -10)]))]
net = bd.traffic_network(roads, box(-90, -70, 90, 70))
check("only traffic classes", {r["cls"] for r in net} == {"primary", "residential"}, sorted({r["cls"] for r in net}))
check("noded at the crossing: four pieces", len(net) == 4, len(net))
ends = {(r["pts"][0], r["pts"][1]) for r in net} | {(r["pts"][-2], r["pts"][-1]) for r in net}
check("the pieces meet at the crossing", (0.0, 0.0) in ends, sorted(ends)[:6])
check("clipped to the district", all(abs(c) <= 90 for r in net for c in r["pts"]))
high = [r for r in net if r["name"] == "High Street"]
check("pieces keep their road's class, width and name", len(high) == 2 and all(r["w"] == 13.0 for r in high))

print("traffic network: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
