"""Checks the satellite reading (scripts/imagery.py) on synthetic pixels and a synthetic mosaic, offline.

    python3 tests/test_imagery.py
"""
import os
import sys

import numpy as np
from shapely.geometry import box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import imagery  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# Colours medians of East Legon roofs in the mosaic (18 Nov 2026), and what they are.
cases = [((150, 150, 148), "M_Roof", "grey sheet"), ((200, 200, 196), "M_Roof", "white sheet"),
         ((196, 120, 90), "M_RoofTile_Red", "orange clay tile"), ((170, 80, 72), "M_RoofTile_Red", "red tile"),
         ((120, 90, 80), "M_RoofSheet_Rust", "brown sheet"), ((70, 90, 170), "M_RoofSheet_Painted", "blue sheet"),
         ((90, 150, 130), "M_RoofSheet_Painted", "teal sheet"), ((200, 180, 140), "M_Roof", "beige"),
         ((80, 175, 205), "pool", "a pool"), ((25, 25, 28), None, "deep shadow")]
for rgb, want, what in cases:
    got = imagery.classify_roof(rgb, 40.0)
    check(f"{what} {rgb}: {want}", got == want, got)
check("a large cyan roof is no pool", imagery.classify_roof((80, 175, 205), 400.0) == "M_RoofSheet_Painted")

rng = np.random.default_rng(3)
# A red tile roof half in its own shade: the lit side's colour, not the mean.
lit = np.tile([190, 85, 70], (300, 1)) + rng.integers(-6, 6, (300, 3))
shade = np.tile([90, 40, 35], (200, 1)) + rng.integers(-6, 6, (200, 3))
rgb, green = imagery.roof_colour(np.vstack([lit, shade]))
check("the roof's lit side", abs(rgb[0] - 190) < 10 and green == 0, rgb)
# A roof under a mango tree: not seen; a few leaves over it: left out of the colour.
leaves = np.tile([85, 105, 70], (400, 1)) + rng.integers(-15, 15, (400, 3))
check("a roof under trees is not seen", imagery.roof_colour(np.vstack([leaves, lit[:100]]))[0] is None)
rgb, green = imagery.roof_colour(np.vstack([lit, leaves[:60]]))
check("a few leaves left out", imagery.classify_roof(rgb) == "M_RoofTile_Red" and 0.1 < green < 0.2, (rgb, green))
mint = np.tile([150, 200, 170], (300, 1))
check("a mint-green roof is a roof, not a tree", imagery.classify_roof(imagery.roof_colour(mint)[0]) == "M_RoofSheet_Painted")
check("too few pixels: not seen", imagery.roof_colour(lit[:10])[0] is None)


# A synthetic mosaic: 0.3 m pixels, a red roof, a pool, and a building outside the image.
class Utm:
    """Lon/lat taken as metres already."""
    @staticmethod
    def transform(x, y):
        return x, y


from affine import Affine  # noqa: E402  (rasterio's)
img = np.zeros((3, 400, 400), np.uint8)
img[:] = np.array([120, 110, 95], np.uint8)[:, None, None]            # bare ground
img[:, 50:90, 50:120] = np.array([185, 85, 70], np.uint8)[:, None, None]   # rows = y down from 120, cols = x
img[:, 200:230, 200:250] = np.array([80, 175, 205], np.uint8)[:, None, None]
tr = Affine(0.3, 0, 0, 0, -0.3, 120)


def read_window(minx, miny, maxx, maxy):
    if minx < 0 or maxx > 120 or miny < 0 or maxy > 120:
        return None
    return img, tr


roof = box(50 * 0.3, 120 - 90 * 0.3, 120 * 0.3, 120 - 50 * 0.3)
pool = box(200 * 0.3, 120 - 230 * 0.3, 250 * 0.3, 120 - 200 * 0.3)
far = box(500, 500, 510, 510)
found = imagery.roofs([("r", roof), ("p", pool), ("far", far), ("small", box(1, 1, 3, 3))], read_window, Utm)
check("the red roof read from the mosaic", found.get("r", {}).get("roof") == "M_RoofTile_Red", found.get("r"))
check("the pool read as a pool", found.get("p", {}).get("roof") == "pool", found.get("p"))
check("outside the mosaic and too small: not read", "far" not in found and "small" not in found)
check("summary", imagery.summary(found) == {"M_RoofTile_Red": 1, "pool": 1}, imagery.summary(found))

print("imagery: " + ("all checks passed" if not failures else f"{failures} failed"))
sys.exit(1 if failures else 0)
