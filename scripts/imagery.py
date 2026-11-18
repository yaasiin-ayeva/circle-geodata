"""What the satellite sees: each building's roof, read from the Maxar 2019 Accra mosaic, instead of a draw.

    python3 scripts/imagery.py roofs --slice data/work/slice-east --out data/work/imagery/roofs-slice-east.json
    python3 scripts/build_district.py --slice data/work/slice-east --name legon --roofs data/work/imagery/roofs-slice-east.json ...

Only the buildings in the leafy neighbourhoods (BOXES) are read unless --everywhere is given.

The mosaic: "Accra 2019 Mosaic", Maxar Technologies, 0.3 m, published on OpenAerialMap for the Open Cities Africa
programme under CC BY-NC 4.0 (SOURCES.md, ATTRIBUTION.md). It is a cloud-optimised GeoTIFF in UTM 30N (EPSG:32630),
read here by HTTP range requests (rasterio's /vsicurl/): only the windows over the buildings are fetched, never the
whole file. --mosaic takes a local crop of it instead. NC: anything built from it may not be sold; the game's credits
name it, and a commercial release has to take these roofs out (build without --roofs) or license the imagery.

For each Overture footprint (30 m2 and more, in the mosaic) the pixels inside the footprint, 0.6 m in from its edge,
are read. Pixels of trees over the roof (green, ExG) and of the roof's shaded side (the darker 40%) are left out, and
the median of the rest is the roof's colour (a mostly green roof is a painted one only when it is bluer or lighter than
the canopy). classify_roof() turns it into one of massing.py's roof materials, or
none when the roof cannot be seen (under trees, in shadow). The output is a JSON of building id to
{"roof": material, "rgb": [r, g, b], "green": share of tree pixels}; massing.building() takes the material in place of
its draw from ROOF_PALETTES, and builds a hipped roof wherever the satellite saw a clay tile, rusted or painted sheet
(those are never flat). Nothing is invented: a roof the satellite does not see keeps the district's draw.
"""
import argparse
import colorsys
import json
import os
import sys

import numpy as np
import shapely
from pyproj import Transformer

MOSAIC = ("/vsicurl/https://oin-hotosm-temp.s3.amazonaws.com/5ea05b54411bed0005680396/0/"
          "5ea05b54411bed0005680397.tif")
MOSAIC_CREDIT = "Imagery: Maxar Technologies, Accra 2019 Mosaic (Open Cities Africa, OpenAerialMap), CC BY-NC 4.0"
INSET_M = 0.6             # in from the footprint's edge: its walls, gutters and the footprint's error
MIN_AREA = 30.0           # m2: smaller footprints have too few pixels at 0.3 m
MIN_PIXELS = 40
LIT_SHARE = 0.6           # the brighter part of the roof's pixels: the side facing the sun
TREES_MAX = 0.5           # a roof more than half under trees is not seen
CELL_M = 200.0            # buildings are read by cells of the mosaic, one window each

# Where the roofs are read: the leafy neighbourhoods of circle-game's render/neighbourhoods.ts (docs/luxury.md), their
# outlines' boxes, (west, south, east, north). The whole city would be about 1 GB of the mosaic to read; these are
# about 150 MB. --everywhere reads every building of the slice.
BOXES = {
    "east-legon": (-0.1782, 5.6229, -0.1427, 5.6485),
    "airport-residential": (-0.1923, 5.5887, -0.1758, 5.6186),
    "roman-ridge": (-0.2064, 5.5952, -0.1875, 5.6113),
    "cantonments": (-0.1811, 5.5689, -0.1579, 5.5911),
    "labone": (-0.1761, 5.5589, -0.1579, 5.5761),
    "ridge": (-0.2023, 5.5593, -0.1902, 5.5745),
    "trasacco": (-0.1159, 5.6520, -0.1085, 5.6597),
}


def in_boxes(g, boxes):
    """Whether a lon/lat geometry's centroid lies in one of the boxes."""
    c = g.centroid
    return any(w <= c.x <= e and s <= c.y <= n for w, s, e, n in boxes)


# The roof materials of massing.py.
GREY, RUST, RED, PAINTED = "M_Roof", "M_RoofSheet_Rust", "M_RoofTile_Red", "M_RoofSheet_Painted"
PITCHED = {RUST, RED, PAINTED}


def tree_pixels(px):
    """Which pixels (N x 3, 0-255) are leaves: green over red and blue (ExG = 2G - R - B)."""
    r, g, b = px[:, 0].astype(float), px[:, 1].astype(float), px[:, 2].astype(float)
    return (2 * g - r - b > 18) & (g > r) & (g > b)


def roof_colour(px):
    """The roof's colour (r, g, b) from its pixels, and the share of them under trees; None when too few are seen."""
    px = np.asarray(px, dtype=float).reshape(-1, 3)
    if len(px) < MIN_PIXELS:
        return None, 0.0
    leaves = tree_pixels(px)
    green = float(leaves.mean())
    seen = px[~leaves]
    if green > TREES_MAX or len(seen) < MIN_PIXELS // 2:
        # Mostly green: leaves, or a roof painted green. Accra's canopy is a dull yellow-green (hue 80 to 130, value
        # under 0.7); mint, teal and pale green sheet are bluer or lighter. In between, nothing is told.
        m = np.median(px[leaves], axis=0)
        h, _, v = colorsys.rgb_to_hsv(*(m / 255))
        if h * 360 >= 135 or v >= 0.72:
            return tuple(float(c) for c in m), green
        return None, green
    lum = seen @ np.array([0.299, 0.587, 0.114])
    lit = seen[lum >= np.quantile(lum, 1 - LIT_SHARE)]
    return tuple(float(v) for v in np.median(lit, axis=0)), green


POOL = "pool"             # not a roof: Overture holds some swimming pools as buildings
POOL_MAX_M2 = 150.0


def classify_roof(rgb, area=None):
    """massing.py's roof material for a roof's colour (0-255), or None when it cannot be told (in deep shadow).
    Thresholds set on 3,000 East Legon roofs against the mosaic, 18 Nov 2026: grey and white sheet, concrete and
    beige are M_Roof; red and orange clay tile M_RoofTile_Red; brown (rusted or brown-painted sheet) M_RoofSheet_Rust;
    blue, green and purple sheet M_RoofSheet_Painted. A small footprint of bright cyan (area under POOL_MAX_M2) is a
    swimming pool, which some of Overture's footprints are."""
    if rgb is None:
        return None
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    h *= 360
    if v < 0.16:
        return None
    if 172 <= h < 205 and s >= 0.35 and v >= 0.55 and (area is None or area <= POOL_MAX_M2):
        return POOL
    if s < 0.16:
        return GREY
    if h >= 330 or h < 22:
        return RED if s >= 0.38 or (s >= 0.30 and v >= 0.55) else RUST     # dull and dark: brown sheet
    if h < 48:
        return RUST if v < 0.62 or s >= 0.35 else GREY      # light tan: beige sheet or bare concrete
    if h < 75:
        return GREY                                         # yellowed concrete, dust
    return PAINTED


def roofs(buildings, read_window, to_utm):
    """{id: {"roof", "rgb", "green"}} for buildings [(id, shapely lon/lat)], reading the mosaic with read_window(
    minx, miny, maxx, maxy) -> (array 3 x H x W, affine transform) or None outside it."""
    from rasterio.features import geometry_mask
    cells = {}
    for bid, g in buildings:
        g = shapely.transform(g, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))
        polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        poly = max(polys, key=lambda p: p.area)
        if poly.geom_type != "Polygon" or poly.area < MIN_AREA:
            continue
        c = poly.centroid
        cells.setdefault((int(c.x // CELL_M), int(c.y // CELL_M)), []).append((bid, poly))
    out = {}
    for n, members in enumerate(cells.values()):
        minx, miny, maxx, maxy = shapely.union_all([p for _, p in members]).bounds
        got = read_window(minx - 1, miny - 1, maxx + 1, maxy + 1)
        if got is None:
            continue
        a, tr = got
        for bid, poly in members:
            inner = poly.buffer(-INSET_M)
            if inner.is_empty:
                continue
            mask = ~geometry_mask([inner], a.shape[1:], tr)
            px = a[:3, mask].T
            if len(px) and (px.max(axis=1) == 0).mean() > 0.2:
                continue                                    # the mosaic's no-data edge
            rgb, green = roof_colour(px)
            roof = classify_roof(rgb, poly.area)
            out[bid] = {"roof": roof, "rgb": [round(v) for v in rgb] if rgb else None, "green": round(green, 2)}
        if n % 20 == 0:
            print(f"  {n + 1}/{len(cells)} cells, {len(out)} roofs", file=sys.stderr)
    return out


def mosaic_reader(path):
    """read_window() over the mosaic (or a crop of it): the window's pixels at full resolution, None outside."""
    import rasterio
    from rasterio.windows import from_bounds
    env = rasterio.Env(GDAL_HTTP_MULTIRANGE="YES", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                       CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                       VSI_CACHE="TRUE", VSI_CACHE_SIZE=str(256 << 20))
    env.__enter__()
    src = rasterio.open(path)
    b = src.bounds

    def read_window(minx, miny, maxx, maxy):
        if minx < b.left or maxx > b.right or miny < b.bottom or maxy > b.top:
            return None
        win = from_bounds(minx, miny, maxx, maxy, src.transform).round_offsets().round_lengths()
        return src.read([1, 2, 3], window=win), src.window_transform(win)
    return read_window


def summary(found):
    """Counts of each roof material, and of the roofs not seen."""
    out = {}
    for v in found.values():
        out[v["roof"] or "unseen"] = out.get(v["roof"] or "unseen", 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def load_roofs(path):
    """{building id: material or "pool"} from a roofs file, what the satellite saw; {} without the file."""
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {bid: v["roof"] for bid, v in data.get("roofs", {}).items() if v.get("roof")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["roofs"])
    ap.add_argument("--slice", required=True, help="folder from overture_slice.py export (buildings.parquet)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mosaic", default=MOSAIC, help="the mosaic's URL (/vsicurl/...) or a local crop")
    ap.add_argument("--everywhere", action="store_true", help="every building of the slice, not only those in BOXES")
    args = ap.parse_args()
    import duckdb
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    rows = con.execute(f"SELECT id, geometry::BLOB FROM '{os.path.join(args.slice, 'buildings.parquet')}'").fetchall()
    buildings = [(bid, shapely.from_wkb(bytes(blob))) for bid, blob in rows]
    if not args.everywhere:
        buildings = [(bid, g) for bid, g in buildings if in_boxes(g, BOXES.values())]
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    found = roofs(buildings, mosaic_reader(args.mosaic), to_utm)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"source": MOSAIC_CREDIT, "buildings": len(buildings), "counts": summary(found), "roofs": found}, f,
                  separators=(",", ":"))
    print(f"{len(found)} of {len(buildings)} buildings read: {summary(found)}")


if __name__ == "__main__":
    main()
