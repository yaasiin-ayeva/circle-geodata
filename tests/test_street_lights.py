"""Checks the street-survey lighting rules on synthetic roads and on a synthetic exported scene.

    python3 tests/test_street_lights.py
"""
import os
import sys

from shapely.geometry import LineString

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district  # noqa: E402
import relight_scene  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# Oxford Street, 900 m running north to south (UTM: x east, y north), 11 m wide.
oxford = LineString([(0, 900), (0, 0)])
lamps = build_district.place_lights("r1", "primary", "Oxford Street", oxford, 11.0)
check("Oxford Street: every pole on the east kerb", all(l["x"] > 0 for l in lamps), f"{len(lamps)} poles")
ys = sorted(l["z"] for l in lamps)
gaps = [b - a for a, b in zip(ys, ys[1:])]
check("Oxford Street: gaps between 35 and 145 m", all(35 <= g <= 145.01 for g in gaps), f"{[round(g) for g in gaps]}")
mean = sum(gaps) / len(gaps)
check("Oxford Street: mean gap near the measured 88 m", 70 <= mean <= 100, f"{mean:.0f} m")
check("Oxford Street: no sodium", all(l["kind"] != "sodium" for l in lamps))

ring = LineString([(0, 0), (1200, 0)])
lamps = build_district.place_lights("r2", "trunk", "Ring Road East", ring, 7.0)
xs = sorted(l["x"] for l in lamps)
gaps = [b - a for a, b in zip(xs, xs[1:])]
check("Ring Road East: poles 40 m apart", all(abs(g - 40) < 0.5 for g in gaps), f"{len(lamps)} poles")
check("Ring Road East: no sodium", all(l["kind"] != "sodium" for l in lamps))

other = LineString([(0, 0), (3000, 0)])
lamps = build_district.place_lights("r3", "primary", "Cantonments Road", other, 9.0)
check("off the lap: some sodium survives", any(l["kind"] == "sodium" for l in lamps),
      f"{sum(l['kind'] == 'sodium' for l in lamps)} of {len(lamps)}")


def lamp(road, x, z, fx, kind="led"):
    return {"kind": kind, "road": road, "foot": [x, 0.0, z], "forward": [fx, 0.0, 0.0],
            "head": [x + fx * 2, 7.88, z], "light": [x + fx * 2, 7.75, z], "direction": [0, -1, 0]}


scene = {"streetLights":
         [lamp("Oxford Street", 6, z, -1, "sodium" if z % 48 == 0 else "led") for z in range(0, 600, 48)] +
         [lamp("Oxford Street", -6, z + 24, 1) for z in range(0, 600, 48)] +
         [lamp("Ring Road East", x, -300, 0, "sodium") for x in range(0, 400, 17)]}
relight_scene.relight(scene)
ox = [l for l in scene["streetLights"] if l["road"] == "Oxford Street"]
check("relight: Oxford Street keeps the east side only", all(l["foot"][0] > 0 for l in ox), f"{len(ox)} kept")
zs = sorted(l["foot"][2] for l in ox)
check("relight: Oxford Street gaps of at least 35 m", all(b - a >= 35 for a, b in zip(zs, zs[1:])))
ring_lamps = sorted(l["foot"][0] for l in scene["streetLights"] if l["road"] == "Ring Road East")
check("relight: Ring Road East at least 38 m apart", all(b - a >= 38 for a, b in zip(ring_lamps, ring_lamps[1:])))
check("relight: no sodium on the lap", all(l["kind"] != "sodium" for l in scene["streetLights"]))
check("relight: heads at 10 m on a 1.75 m arm",
      all(abs(l["head"][1] - 10) < 1e-6 and abs(abs(l["head"][0] - l["foot"][0]) - 1.75 * abs(l["forward"][0])) < 1e-3
          for l in scene["streetLights"]))

print("street lights: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
