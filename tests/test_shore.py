"""Osu's shore laid into the relief (scripts/relief.py shore_drop), on a synthetic grid: no network, no DEM.

    python3 tests/test_shore.py

A flat ground 6 m high, a coastline running east along z = 0 (in latitude and longitude, through a UTM frame of our
own): landward of it (north, z < 0) nothing moves; seaward it falls to the sea floor over SHORE_RAMP_M and stays under
the game's sea level (0.8 m) to near the band's edge, then rises back to meet the natural ground with no step.
"""
import os
import sys

import numpy as np
from pyproj import Transformer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import relief  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if detail else ""))
    fails += 0 if ok else 1


to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
to_ll = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True)
origin = to_utm.transform(-0.1809, 5.5669)
# The line: world z = 0 from x = -400 to 400, west to east (the sea on its right: south, z > 0).
xs = np.array([-400.0, 0.0, 400.0])
lon, lat = to_ll.transform(origin[0] + xs, origin[1] - 0.0 * xs)
shore = {"features": [{"geometry": {"type": "LineString", "coordinates": [[float(a), float(b)] for a, b in zip(lon, lat)]}}]}
grid = relief.Grid(-200.0, -200.0, 51, 76, 8.0)          # x from -200 to 200, z from -200 to 400
h = np.full((grid.nz, grid.nx), 6.0)
n = relief.shore_drop(h, grid, shore, origin, to_utm)
x, z = grid.xz()
check("some nodes lowered", n > 100, f"{n}")
check("landward of the line nothing moves", np.allclose(h[z < -0.5], 6.0))
at = lambda zz: float(h[(np.abs(z - zz) < 0.1) & (np.abs(x) < 50)].mean())
check("half way down the face at half the ramp", abs(at(8.0) - (6.0 + (relief.SHORE_FLOOR_M - 6.0) * 8.0 / relief.SHORE_RAMP_M)) < 0.05, f"{at(8.0):.2f} m")
check("the sea floor under the game's sea level (0.8 m) from the ramp to the band's last 60 m",
      all(at(zz) <= relief.SHORE_FLOOR_M + 0.01 for zz in (16.0, 40.0, 160.0)), f"{at(16.0):.2f}, {at(40.0):.2f} m")
check("the floor rises to SHORE_EDGE_M at the band's edge", abs(at(relief.SHORE_BAND_M - 2) - relief.SHORE_EDGE_M) < 0.5, f"{at(relief.SHORE_BAND_M - 2):.2f} m")
# Inside the band only: past it the synthetic ground is 6 m, where the real offshore ground is near sea level.
inside = z[:, 25] < relief.SHORE_BAND_M - 1
steps = np.abs(np.diff(h[:, 25][inside]))
check("no step over 6 m between two nodes inside the band (the face drops 8.5 m over 12 m on an 8 m grid)", steps.max() < 6.0, f"{steps.max():.2f} m")
print("shore: all checks passed" if not fails else f"{fails} check(s) failed")
sys.exit(1 if fails else 0)
