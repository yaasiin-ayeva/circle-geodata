"""Build the world's relief: the ground's real heights, bare earth, with the roads laid into the slopes.

    python3 scripts/relief.py --world data/work/web/osu-world.json --out data/work/web/relief

Until now the game was flat (every area at y = 0). Accra is not: the coastal plain rises from the sea to about 60 m at
Legon and 110 m at Adenta, then the Akuapem ridge climbs to about 430 m at Aburi in 6 km. This writes the heights the
game drapes its world on and drives on (circle-game src/world/relief.ts), in the world's frame (glTF x east, z south,
metres from home's origin; heights in metres above the geoid).

Source: the Copernicus DEM GLO-30 (30 m, 1 arc second; data/dem/, fetched from its AWS open-data bucket the first time).
Licence: free of charge for any lawful use, commercial included, with its attribution kept (licenses/ in circle-game):
"produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018 provided
under COPERNICUS by the European Union and ESA; all rights reserved".

GLO-30 is a surface model: it carries the roofs and the tree tops. The steps:
  1. bare earth: a grey opening of 150 m (5 pixels) removes what stands on the ground and is smaller than that (houses,
     blocks, trees, Accra's whole low-rise fabric), then a Gaussian of 45 m smooths the 1-2 m noise. A forest canopy
     wider than 150 m stays as a smooth lift of its tree height, which reads as ground;
  2. resampled bicubically onto the world's grid, 8 m;
  3. the roads laid in: each road of every area (the scenes' "roads") is sampled along its centreline, its height
     profile smoothed along its length (sigma 70 m on the trunk roads and motorways, 45 m on the primaries and
     secondaries, 25 m on the rest), and the ground within its half width plus 2 m takes that height; beyond, the
     ground returns to the terrain over a bank (25 m on the big roads, 12 m on the rest), a cut uphill and a fill
     downhill. So a road climbs and falls with the land and never leans across: the crossfall is that of the road,
     not of the hillside. Where roads meet, the nearest one sets the height (a Euclidean distance transform), and a
     last 8 m smoothing takes the seam out. The interchanges' cuts (and a tunnel with no road over it) are laid in at
     their own heights, their floors then held under their roads to 12 m beyond the asphalt, which the smoothing
     would otherwise lift over a narrow cut; a tunnel under the ground's roads is left under the ground;
  4. written as tiles of 1024 m (129 x 129 heights, 8 m apart, edges shared) wherever the world has a road within
     1.5 km, and two grids over the whole world and 14 km beyond it: coarse.bin (256 m, heights to 0.1 m, 37 KB),
     loaded before the drive for the ground beyond the loaded tiles, and far.bin (128 m, to 0.05 m, about 130 KB),
     loaded after the start for the far view: from the Aburi Road the plain and the city are seen 20 km away.

The land cover (cover.bin, on the far grid's nodes): what covers the ground round each far node, as
three shares 0-255 (built up, trees, water), from ESA WorldCover 2021 v200 (10 m, data/worldcover/, fetched from its
AWS open-data bucket the first time; CC BY 4.0, attribution in relief.json "cover"). Each share is counted on 64
samples 16 m apart across the node's 128 m cell. The game colours the far land with it (the city's grey where Greater
Accra is built over, the ridge's forest where the trees are) and scatters the city's far lights over the built land
beyond the world's roads: from the ridge at night, the whole conurbation is lit, not only the mapped areas. The three
planes are zlib'd one after another, built then trees then water.

Encoding (relief.json "enc"): each height as uint16 q = round((h + 20) / 0.01) (0.01 m from -20 to 635 m; the coarse
and far grids' own step is their "q"), row by row;
the residual of the planar predictor (left + up - up-left, mod 2^16) zigzagged, its low bytes then its high bytes,
then zlib. The game inflates it with DecompressionStream("deflate").
"""
import argparse
import json
import math
import os
import sys
import urllib.request
import zlib

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString
from scipy import ndimage

DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"
ATTRIBUTION = ("Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH "
               "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.")
COVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{n}_Map.tif"
COVER_ATTRIBUTION = ("ESA WorldCover 2021 v200 (c) ESA WorldCover project 2021, contains modified Copernicus Sentinel data "
                     "(2021) processed by the ESA WorldCover consortium; CC BY 4.0.")
STEP = 8.0            # fine grid, m
TILE = 1024.0         # fine tile, m (128 steps)
COARSE = 256.0        # coarse grid, m: in the first load, for the ground beyond the loaded tiles
FAR = 128.0           # far grid, m: loaded after the start, for the far view, with the land cover on its nodes
COARSE_Q, FAR_Q = 0.1, 0.05   # their height steps, m (the tiles' is Q)
REACH = 14000.0       # the coarse and far grids' reach beyond the world, m
NEAR = 1500.0         # fine tiles within this of a road, m
H0, Q = -20.0, 0.01   # encoding
# Per road class: profile smoothing sigma (m), bank width (m).
PROFILE = {"motorway": (70, 25), "trunk": (70, 25), "primary": (45, 25), "secondary": (45, 18)}
PROFILE_DEFAULT = (25, 12)


def dem_name(lat, lon):
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"


def load_dem(folder, lat0, lat1, lon0, lon1):
    """The DEM over a lat/lon box as one array (rows north to south) and its pixel grid: the 1-degree tiles it spans,
    fetched into `folder` when missing. Copernicus tiles are pixel-is-area, 3600 x 3600 at this latitude, their
    corner at the tile's north-west."""
    import tifffile
    os.makedirs(folder, exist_ok=True)
    rows = []
    for lat in range(math.floor(lat1 - 1e-9), math.floor(lat0) - 1, -1):
        row = []
        for lon in range(math.floor(lon0), math.floor(lon1 - 1e-9) + 1):
            n = dem_name(lat, lon)
            path = os.path.join(folder, n + ".tif")
            if not os.path.exists(path):
                print(f"fetching {n}")
                urllib.request.urlretrieve(DEM_URL.format(n=n), path + ".part")
                os.replace(path + ".part", path)
            a = tifffile.imread(path).astype(np.float32)
            if a.shape != (3600, 3600):
                sys.exit(f"{n}: {a.shape}, expected 3600 x 3600")
            row.append(a)
        rows.append(np.hstack(row))
    dem = np.vstack(rows)
    north = math.floor(lat1 - 1e-9) + 1
    west = math.floor(lon0)
    return dem, north, west, 1 / 3600


def load_cover(folder, lat0, lat1, lon0, lon1):
    """ESA WorldCover classes over a lat/lon box (rows north to south) and its pixel grid: the 3-degree tiles it
    spans, fetched into `folder` when missing, each cropped to the box. Pixel-is-area, 36000 x 36000, 1/12000 degree,
    corner at the tile's north-west."""
    import tifffile
    os.makedirs(folder, exist_ok=True)
    px = 1 / 12000
    north = math.ceil(lat1 / px) * px
    west = math.floor(lon0 / px) * px
    rows = int(round((north - lat0) / px)) + 1
    cols = int(round((lon1 - west) / px)) + 1
    out = np.zeros((rows, cols), np.uint8)
    for tlat in range(math.floor(lat0 / 3) * 3, math.floor(lat1 / 3) * 3 + 1, 3):
        for tlon in range(math.floor(lon0 / 3) * 3, math.floor(lon1 / 3) * 3 + 1, 3):
            ns = f"N{tlat:02d}" if tlat >= 0 else f"S{-tlat:02d}"
            ew = f"E{tlon:03d}" if tlon >= 0 else f"W{-tlon:03d}"
            n = ns + ew
            path = os.path.join(folder, n + ".tif")
            if not os.path.exists(path):
                print(f"fetching WorldCover {n}")
                urllib.request.urlretrieve(COVER_URL.format(n=n), path + ".part")
                os.replace(path + ".part", path)
            a = tifffile.imread(path)
            if a.shape != (36000, 36000):
                sys.exit(f"{n}: {a.shape}, expected 36000 x 36000")
            # The tile's rows and columns within the box, and where they land in it.
            tn, tw = tlat + 3, tlon
            r0 = max(0, int(round((tn - north) / px))); r1 = min(36000, int(round((tn - lat0) / px)) + 1)
            c0 = max(0, int(round((west - tw) / px))); c1 = min(36000, int(round((lon1 - tw) / px)) + 1)
            if r1 <= r0 or c1 <= c0:
                continue
            orow = int(round((north - tn) / px)) + r0
            ocol = int(round((tw - west) / px)) + c0
            out[orow:orow + r1 - r0, ocol:ocol + c1 - c0] = a[r0:r1, c0:c1]
            del a
    return out, north, west, px


def cover_shares(cover, north, west, px, origin_utm, grid):
    """Built, tree and water shares (0-255) round each node of `grid`, from 8 x 8 samples 16 m apart."""
    to_ll = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True)
    off = (np.arange(8) - 3.5) * (grid.step / 8)
    built = np.zeros((grid.nz, grid.nx), np.float32)
    tree = np.zeros_like(built)
    water = np.zeros_like(built)
    x, z = grid.xz()
    for oz in off:
        for ox in off:
            lon, lat = to_ll.transform(origin_utm[0] + (x + ox).ravel(), origin_utm[1] - (z + oz).ravel())
            r = np.clip(((north - np.asarray(lat)) / px).astype(np.int64), 0, cover.shape[0] - 1)
            c = np.clip(((np.asarray(lon) - west) / px).astype(np.int64), 0, cover.shape[1] - 1)
            k = cover[r, c].reshape(grid.nz, grid.nx)
            built += k == 50
            tree += k == 10
            water += (k == 80) | (k == 90) | (k == 95)
    return [np.round(a * (255 / 64)).astype(np.uint8) for a in (built, tree, water)]


def bare_earth(dem):
    """Bare ground from the surface model (step 1)."""
    g = ndimage.grey_opening(dem, size=(5, 5))
    return ndimage.gaussian_filter(g, 1.5)


def world_roads(world_path):
    """Every area's roads in the world frame: (class, width, points)."""
    world = json.load(open(world_path))
    folder = os.path.dirname(world_path)
    out = []
    for a in world["areas"]:
        s = json.load(open(os.path.join(folder, a["scene"])))
        ox, oz = a["offset"]
        for r in s.get("roads") or []:
            pts = np.array(r["pts"], dtype=np.float64) + (ox, oz)
            if len(pts) > 1:
                out.append((r["cls"], float(r["w"]), pts))
    return world, out


# The structures (scripts/elevated.py): the natural ground under them is the bare earth smoothed over NATURAL_SIGMA_M,
# one field for all of them, so two pieces that meet share their height there. A cut is laid into the ground like a
# road at its own level, with banks by its site's style: grassed slopes at Tetteh Quarshie, walls elsewhere.
NATURAL_SIGMA_M = 40.0
CUT_BANK_M = {"tetteh-quarshie": 16.0}
CUT_BANK_DEFAULT = 2.0
CUT_UNDER_M = 0.5     # a cut's floor under its road: under the 8 m grid's error on a sloping road, and the ground drawn over it
CUT_FLOOR_M = 12.0    # its floor held flat this far beyond the road's half-width: over a cell's diagonal (8 m grid)
UNDER_M = 0.3         # the ground under a deck or a ramp, under the natural ground
LEVEL_UNDER_M = 0.1   # under a road at the ground's level beside a cut (elevated.py beside_cuts): no gap under its edge
MOUTH_DEPTH_M = 1.5   # a cut this deep under the natural ground is held its full CUT_FLOOR_M beyond its road


def cut_floor(y, off):
    """A cut's floor along its road: CUT_UNDER_M under it where the cut is MOUTH_DEPTH_M deep or more, rising to the
    road itself at a mouth. Held under the road at the mouth too, the floor sank the mouth into the ground it was then
    brought down to, a step of up to 1 m up onto the road on the ground beyond (Tetteh Quarshie's, Ako Adjei's)."""
    return np.asarray(y, dtype=np.float64) - CUT_UNDER_M * np.clip(-np.asarray(off, dtype=np.float64) / MOUTH_DEPTH_M, 0, 1)


def world_structures(world, folder, elevated_path):
    """The structures the world's areas name (scenes' "structures"), from elevated.json, in the world frame:
    (pieces, footbridges), each with "pts" as x east, z south from the world's origin."""
    ids = set()
    for a in world["areas"]:
        s = json.load(open(os.path.join(folder, a["scene"])))
        ids.update(s.get("structures") or [])
    if not ids or not elevated_path or not os.path.exists(elevated_path):
        return [], []
    d = json.load(open(elevated_path))
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    o = world["origin_wgs84"]
    oe, on = to_utm.transform(o["lon"], o["lat"])
    move = lambda pts: [[round(x - oe, 2), round(on - y, 2)] for x, y in pts]
    return ([{**{k: v for k, v in p.items() if k != "drawn"}, "pts": move(p["pts"])} for p in d["pieces"] if p["id"] in ids],
            [{**f, "pts": move(f["pts"])} for f in d["footbridges"] if f["id"] in ids])


def structure_heights(pieces, natural, carved, grid):
    """Each piece's absolute heights: the natural ground plus its offset, and, at an end on the ground (kind G), the
    laid road's height there, the difference spread linearly along the piece."""
    def at(field, pts):
        fi = (pts[:, 0] - grid.x0) / grid.step
        fj = (pts[:, 1] - grid.z0) / grid.step
        return ndimage.map_coordinates(field, [fj.clip(0, grid.nz - 1), fi.clip(0, grid.nx - 1)], order=1, mode="nearest")
    for p in pieces:
        pts = np.array(p["pts"], dtype=np.float64)
        y = at(natural, pts) + np.array(p["off"])
        seg = np.hypot(*np.diff(pts, axis=0).T)
        s = np.concatenate([[0], np.cumsum(seg)])
        lv = p.get("level")         # at the natural ground till onto_shelves() gives it the ground's
        d0 = float(at(carved, pts[:1])[0] - y[0]) if p["kind"][0] == "G" and not lv else 0.0
        d1 = float(at(carved, pts[-1:])[0] - y[-1]) if p["kind"][-1] == "G" and not lv else 0.0
        t = s / max(s[-1], 1e-6)
        y = y + d0 * (1 - t) + d1 * t
        p["y"] = [round(float(v), 2) for v in y]


MOUTH_SPAN_M = 30.0   # a cut's road comes to the ground at its mouth over this
SEAT_MAX_M = 1.5      # an end further than this off the ground there stands over a hole (a bridge's end over a cut)


def mouths_to_ground(pieces, h, grid):
    """Each piece's end at the ground (a cut's mouth, a ramp's foot, a bridge at the ground's level) brought to the
    ground's final height there, the difference spread over MOUTH_SPAN_M of the road (never further than its other
    end). Only the cuts were, and a bridge at the ground's level kept the natural ground's height over the carved road
    it meets: a kerb of up to 0.7 m across the motorway at Tetteh Quarshie, where the car stopped. Returns how many
    ends moved."""
    moved = 0
    for p in pieces:
        if p.get("level"):
            continue            # its heights are the ground's beside the cut's shelf (onto_shelves)
        pts = np.array(p["pts"], dtype=np.float64)
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        y = np.array(p["y"], dtype=np.float64)
        for e in (0, len(pts) - 1):
            # An end on the ground (G: the ground's height there, which the carving may have moved since), or at its level.
            if p["kind"][e] != "G" and abs(p["off"][e]) > 0.3:
                continue
            fi, fj = (pts[e, 0] - grid.x0) / grid.step, (pts[e, 1] - grid.z0) / grid.step
            g = float(ndimage.map_coordinates(h, [[np.clip(fj, 0, grid.nz - 1)], [np.clip(fi, 0, grid.nx - 1)]], order=1, mode="nearest")[0])
            d = g - y[e]
            if abs(d) < 0.05 or abs(d) > SEAT_MAX_M:
                continue
            span = min(MOUTH_SPAN_M, s[-1])
            k = d * np.clip(1 - np.abs(s - s[e]) / span, 0, 1)
            y += k
            # A cut's offset over the natural ground with it: the game stands the cut's walls on the ground it gives.
            # Not a bridge's or a ramp's: at the ground's level, its end is one the game joins to the road there.
            if "C" in p["kind"] or "T" in p["kind"]:
                p["off"] = [round(float(o + dk), 2) for o, dk in zip(p["off"], k)]
            moved += 1
        p["y"] = [round(float(v), 2) for v in y]
    return moved


BRIDGE_REACH_M = 30.0   # how far a bridge at the ground's level is carried on over the hole beyond its end


def bridges_over_holes(pieces, h, grid, roads):
    """A bridge at the ground's level over a cut (the motorway's at Tetteh Quarshie) runs on from its end's height to
    the ground's, along the road on the ground that goes on from its end (its own line where none does), until the
    ground under its line is back at its road: OpenStreetMap's bridge stops at the cut's edge, but the cut's floor is
    held CUT_FLOOR_M beyond its road (the ground drawn over an 8 m cell never rises over the asphalt) and its banks
    beyond that, and the road beyond the bridge stood over a hole up to 3 m deep, where the car stopped at the deck's
    edge. Never past a junction: Ako Adjei's roundabout, whose roads leave its bridges in that hole, stays as it was.
    Returns how many ends were carried on."""
    def at(x, z):
        fi, fj = (x - grid.x0) / grid.step, (z - grid.z0) / grid.step
        return float(ndimage.map_coordinates(h, [[np.clip(fj, 0, grid.nz - 1)], [np.clip(fi, 0, grid.nx - 1)]], order=1, mode="nearest")[0])
    ground_roads = [np.asarray(rp, dtype=np.float64) for cls, w, rp, *own in roads if cls not in ("cut", "under") and len(rp) > 1]

    def degree(cx, cz):
        return sum(math.hypot(rp[k][0] - cx, rp[k][1] - cz) < 1.5 for rp in ground_roads for k in (0, -1))

    def way_on(x, z, ux, uz):
        """The roads' points on from (x, z), heading (ux, uz), chained end to end through the points where only two
        meet: never past a junction, where the roads leaving it would stand under the bridge carried on."""
        pts, cx, cz, hx, hz, used, length = [], x, z, ux, uz, set(), 0.0
        while length < BRIDGE_REACH_M:
            if pts and degree(cx, cz) > 2:
                break
            best, bd = None, 0.5         # on within 60 degrees: never back along the bridge
            for n, rp in enumerate(ground_roads):
                if n in used:
                    continue
                for seq in (rp, rp[::-1]):
                    if math.hypot(seq[0][0] - cx, seq[0][1] - cz) < 1.5:
                        d = seq[1] - seq[0]
                        c = (d[0] * hx + d[1] * hz) / (math.hypot(*d) or 1.0)
                        if c > bd:
                            best, bd = (n, seq), c
            if best is None:
                break
            used.add(best[0])
            for q in best[1][1:]:
                length += math.hypot(q[0] - cx, q[1] - cz)
                hx, hz = (q[0] - cx), (q[1] - cz)
                l = math.hypot(hx, hz) or 1.0
                hx, hz, cx, cz = hx / l, hz / l, float(q[0]), float(q[1])
                pts.append(np.array([cx, cz]))
        return pts or [np.array([x + ux * BRIDGE_REACH_M, z + uz * BRIDGE_REACH_M])]

    ends = [(q["pts"][k][0], q["pts"][k][1], q["y"][k], n) for n, q in enumerate(pieces) for k in (0, -1)]
    carried = 0
    for n, p in enumerate(pieces):
        for first in (True, False):
            e = 0 if first else len(p["pts"]) - 1     # after the start has grown
            if p["kind"][e] != "D" or abs(p["off"][e]) > 0.3:
                continue
            x, z, y = p["pts"][e][0], p["pts"][e][1], p["y"][e]
            # Not where another piece carries the road on (Ako Adjei's roundabout, at the ground's level beside its cut).
            if any(m != n and math.hypot(ex - x, ez - z) < 1.5 and abs(ey - y) < 0.6 for ex, ez, ey, m in ends):
                continue
            o = p["pts"][1 if first else -2]
            l = math.hypot(x - o[0], z - o[1]) or 1.0
            ux, uz = (x - o[0]) / l, (z - o[1]) / l
            # The ground back under its road's line.
            back = lambda c, vx, vz: at(c[0], c[1]) > y - 0.15
            if back((x, z), ux, uz):
                continue
            # Walked a metre at a time to the first point where the ground is back, a metre on.
            way, prev, walked, found = [], np.array([x, z]), 0.0, False
            for q in way_on(x, z, ux, uz):
                seg = math.hypot(*(q - prev))
                if seg < 0.05:
                    continue
                vx, vz = (q - prev) / seg
                for s_ in np.arange(1.0, seg + 0.01, 1.0):
                    c = prev + (q - prev) * (s_ / seg)
                    if back(c, vx, vz):
                        way.append([round(float(c[0] + vx), 2), round(float(c[1] + vz), 2)])
                        found = True
                        break
                if found or walked + seg > BRIDGE_REACH_M:
                    break
                walked += seg
                way.append([round(float(q[0]), 2), round(float(q[1]), 2)])
                prev = q
            if not found or math.hypot(way[-1][0] - x, way[-1][1] - z) < 1.0:
                continue
            # Its height from the end's to the ground's where it comes down, evenly along the way.
            n, g = len(way), at(*way[-1])
            d = np.concatenate([[0], np.cumsum([math.hypot(q[0] - r[0], q[1] - r[1]) for r, q in zip([[x, z]] + way[:-1], way)])])
            ys = [round(float(y + (g - y) * t / d[-1]), 2) for t in d[1:]]
            if first:
                p["pts"][:0] = way[::-1]; p["y"][:0] = ys[::-1]; p["off"][:0] = [p["off"][0]] * n; p["kind"] = "D" * n + p["kind"]
            else:
                p["pts"] += way; p["y"] += ys; p["off"] += [p["off"][-1]] * n; p["kind"] += "D" * n
            carried += 1
    return carried


SHELF_FROM_M = 1.15    # a cut's wall and its coping beyond its asphalt (circle-game interchanges.ts CUT_WALL + 0.35)
SHELF_MAX_M = 30.0     # the furthest a shelf reaches from the wall: over the floor held and the cell beyond it
SHELF_STEP_M = 0.5
SHELF_SEAM_M = 0.05    # the ground this near the banks' top meets the shelf
SHELF_DEPTH_M = 0.6    # a cut shallower than this under the ground has no wall (interchanges.ts CUT_FROM), nor a shelf
SHELF_UNDER_CUT_M = 0.3   # a road in a cut this far under a shelf's top stops it (the game lays it under one less deep)


def cut_shelves(pieces, h, grid, roads=()):
    """Each walled cut's shelf: per point, how far beyond its wall's coping, on its left and on its right, the ground
    lies under the banks' top (the cut's natural ground, y - off): its floor held beyond its road (hold_cut_floors)
    and the cell over which the 8 m grid comes back up, a pit up to 7 m deep and 25 m wide beside Ako Adjei's walls,
    where the roundabout's and the service roads' asphalt lay (the director, 18 Nov 2026). The game lays a slab there
    at the banks' top, which the roads beside the cut (elevated.py beside_cuts) and what stands by them stand on. Up to
    SHELF_MAX_M; never over another piece's road lower down (the other carriageway's cut, a slip road down into it),
    nor over a road on the ground left in the pit (one elevated.py did not carry, which would be roofed over).
    In p["shelf"] as [left, right, left top, right top] per point, a width 0 where none; not at a site with grassed
    banks. Returns the shelves' area in m^2."""
    def at(x, z):
        fi, fj = (x - grid.x0) / grid.step, (z - grid.z0) / grid.step
        return ndimage.map_coordinates(h, [np.clip(fj, 0, grid.nz - 1), np.clip(fi, 0, grid.nx - 1)], order=1, mode="nearest")
    lines = [LineString(p["pts"]) if len(p["pts"]) > 1 else None for p in pieces]
    cums = [np.concatenate([[0], np.cumsum(np.hypot(*np.diff(np.array(p["pts"], dtype=np.float64), axis=0).T))]) for p in pieces]
    tree = shapely.STRtree([l for l in lines if l is not None])
    index = [n for n, l in enumerate(lines) if l is not None]
    ground = [(LineString(rp), w) for cls, w, rp, *own in roads if cls not in ("cut", "under") and len(rp) > 1]
    gtree = shapely.STRtree([l for l, _ in ground]) if ground else None
    area = 0.0
    for n, p in enumerate(pieces):
        if "C" not in p["kind"] or p.get("style") in ("tetteh-quarshie", "tema"):   # elevated.py NO_SHELF
            continue
        P = np.array(p["pts"], dtype=np.float64)
        y, off = np.array(p["y"]), np.array(p["off"])
        shelf = []
        # A piece that carries this one on from an end (the next piece of the same carriageway, going on the other way
        # from their shared point) lies across the walk from that end point, which stopped the shelf at its first
        # step: a wedge 20 m wide was missing at each joint between two pieces of a cut. Such a piece counts only off
        # this cut's own road and wall; any other, wherever it runs: a cut drawn within a few metres of this one, or
        # one that forks from the same point the same way (the connector from Nsawam Road's cut at Circle, whose
        # shelf ran 28 m over Nsawam Road's lanes, 1.3 m over them).
        own = lines[n].buffer(p["w"] / 2 + SHELF_FROM_M)
        inward = [(P[0], P[1] - P[0]), (P[-1], P[-2] - P[-1])]
        clipped = {}
        for k in range(len(P)):
            if -off[k] < SHELF_DEPTH_M:
                shelf.append([0.0, 0.0, round(float(y[k] - off[k]), 2), round(float(y[k] - off[k]), 2)])
                continue
            a, b = P[max(0, k - 1)], P[min(len(P) - 1, k + 1)]
            t = b - a
            left = np.array([t[1], -t[0]]) / (np.hypot(*t) or 1.0)
            bank = y[k] - off[k]
            s = p["w"] / 2 + SHELF_FROM_M + np.arange(0.0, SHELF_MAX_M + 1e-6, SHELF_STEP_M)
            # Lower roads near this line: where the walk stops before them.
            reach = shapely.Point(P[k]).buffer(p["w"] / 2 + SHELF_FROM_M + SHELF_MAX_M + 15)
            near = [index[m] for m in tree.query(reach)]
            gnear = [ground[m] for m in gtree.query(reach)] if gtree is not None else []
            pair = []
            for side in (1.0, -1.0):
                q = P[k] + np.outer(s * side, left)
                g = at(q[:, 0], q[:, 1])
                stop = len(s)
                pts = shapely.points(q)
                for line, w in gnear:
                    on = np.nonzero(shapely.distance(line, pts) <= w / 2 + 0.3)[0]
                    if len(on):
                        stop = min(stop, int(on[0]))
                for m in near:
                    if m == n or pieces[m].get("level"):
                        continue
                    o = pieces[m]
                    if m not in clipped:
                        Q = np.array(pieces[m]["pts"], dtype=np.float64)
                        carries = any(np.hypot(*(e - q)) < 0.5 and float(np.dot(u, w)) < 0
                                      for e, u in inward for q, w in ((Q[0], Q[1] - Q[0]), (Q[-1], Q[-2] - Q[-1])))
                        clipped[m] = lines[m].difference(own) if carries else lines[m]
                    if clipped[m].is_empty:
                        continue
                    on = np.nonzero(shapely.distance(clipped[m], pts) <= o["w"] / 2 + SHELF_FROM_M)[0]
                    if not len(on):
                        continue
                    d = shapely.line_locate_point(lines[m], pts[on])
                    oy = np.interp(d, cums[m], o["y"])
                    # A road in a cut or a tunnel there (a ramp's foot that came down into the pit is lifted onto
                    # the shelf after, onto_shelves()), SHELF_UNDER_CUT_M or more under the banks' top: the other
                    # carriageway's cut near the mouths, 1.1 m deep at Ako Adjei's eastern one, which a shelf held
                    # to roads 1 m down covered, 0.3 m over its lanes. Shallower, the game lays the shelf under it.
                    kc = np.array([o["kind"][int(np.argmin(np.abs(cums[m] - v)))] in "CT" for v in d])
                    low = on[(oy < bank - SHELF_UNDER_CUT_M) & kc]
                    if len(low):
                        stop = min(stop, int(low[0]))
                up = np.nonzero(g[:stop] >= bank - SHELF_SEAM_M)[0]
                top = bank
                if len(up):
                    width = s[up[0]] - s[0] + SHELF_STEP_M if up[0] > 0 else 0.0
                else:
                    # The ground beyond lower than the banks' top (Circle's, in the Odaw's valley: the natural ground
                    # smoothed over 40 m stood 1 m over the streets beside Nsawam Road's cut): the shelf at the level
                    # the ground comes back up to, where it stops rising.
                    flat = [c for c in range(1, stop - 4) if g[c] - g[0] > 0.5 and np.max(g[c:c + 5]) - g[c] < 0.05]
                    if flat:
                        top = min(bank, float(g[flat[0]]))
                        width = s[flat[0]] - s[0] + SHELF_STEP_M
                    else:
                        width = max(0.0, s[stop - 1] - s[0]) if stop else 0.0
                        if stop < len(s):
                            width = max(0.0, width - 0.6)      # short of the other road's own wall
                if top < g[0] + 0.3:
                    width = 0.0                             # no pit worth a shelf
                pair.append((round(float(width), 1), round(float(top), 2)))
            shelf.append([pair[0][0], pair[1][0], pair[0][1], pair[1][1]])
        # Its tops along the cut without the ground's steps: the median of five points.
        for c in (2, 3):
            t = np.array([e[c] for e in shelf])
            m = ndimage.median_filter(t, size=5, mode="nearest")
            for k, e in enumerate(shelf):
                e[c] = round(float(min(m[k], y[k] - off[k])), 2)
        for k in range(len(P) - 1):
            area += (shelf[k][0] + shelf[k][1] + shelf[k + 1][0] + shelf[k + 1][1]) / 2 * float(np.hypot(*(P[k + 1] - P[k])))
        if any(e[0] or e[1] for e in shelf):
            p["shelf"] = shelf
    return area


SHELF_SINK_M = 0.03    # the shelf's top under the banks' top (interchanges.ts SHELF_SINK)
LEVEL_OVER_M = 0.05     # a road beside a cut lowered to meet others stays this far over the ground
SPIKE_M = 0.5          # a lone point this far off both its neighbours is a sampling's (onto_shelves)
END_SPAN_M = 15.0      # a road beside a cut comes to its end on the ground over this
JOIN_SPAN_M = 20.0     # a road beside a cut comes to the height of a piece it meets over this


def shelf_ground(pieces, h, grid):
    """The ground a road stands on beside the cuts: the relief's, or a cut's shelf over it (cut_shelves, laid as the game
    lays it, interchanges.ts layShelves()) where that is higher. A function of (x, z) arrays."""
    polys, tops = [], []
    for p in pieces:
        if "shelf" not in p:
            continue
        P = np.array(p["pts"], dtype=np.float64)
        n, inner = len(P), p["w"] / 2 + SHELF_FROM_M
        L = []
        for i in range(n):
            t = P[min(n - 1, i + 1)] - P[max(0, i - 1)]
            L.append(np.array([t[1], -t[0]]) / (np.hypot(*t) or 1.0))
        for i in range(n - 1):
            for side, si in ((1.0, 0), (-1.0, 1)):
                wi, wj = p["shelf"][i][si], p["shelf"][i + 1][si]
                if wi < 0.3 and wj < 0.3:
                    continue
                q = [P[i] + L[i] * side * inner, P[i + 1] + L[i + 1] * side * inner,
                     P[i + 1] + L[i + 1] * side * (inner + wj), P[i] + L[i] * side * (inner + wi)]
                g = shapely.Polygon(q)
                if g.is_valid and g.area > 0.01:
                    polys.append(g)
                    tops.append((P[i], P[i + 1], p["shelf"][i][2 + si] - SHELF_SINK_M, p["shelf"][i + 1][2 + si] - SHELF_SINK_M))
    tree = shapely.STRtree(polys) if polys else None

    def at(x, z):
        x, z = np.atleast_1d(np.asarray(x, dtype=np.float64)), np.atleast_1d(np.asarray(z, dtype=np.float64))
        fi, fj = (x - grid.x0) / grid.step, (z - grid.z0) / grid.step
        g = ndimage.map_coordinates(h, [np.clip(fj, 0, grid.nz - 1), np.clip(fi, 0, grid.nx - 1)], order=1, mode="nearest").astype(np.float64)
        if tree is None:
            return g
        pts = shapely.points(np.column_stack([x, z]))
        pi, gi = tree.query(pts, predicate="intersects")
        for k, m in zip(pi, gi):
            a, b, ya, yb = tops[m]
            ab = b - a
            t = float(np.clip(((x[k] - a[0]) * ab[0] + (z[k] - a[1]) * ab[1]) / (ab @ ab or 1.0), 0, 1))
            g[k] = max(g[k], ya + (yb - ya) * t)
        return g
    return at


def onto_shelves(pieces, h, grid):
    """The roads beside the cuts (elevated.py beside_cuts) at the ground they stand on, a shelf's top over the pit
    (shelf_ground()), at every point; and another piece's end at the ground's level that lands on a shelf (a ramp's foot
    beside Circle's Nsawam Road cut), brought to the shelf's top over MOUTH_SPAN_M. Without, their heights were the
    natural ground's, spread to their feet: two of them meeting stood up to 1 m apart, and a road at the ground's level
    met Ako Adjei's bridges 7.5 m under them. Returns how many points moved."""
    at = shelf_ground(pieces, h, grid)
    moved = 0
    for p in pieces:
        P = np.array(p["pts"], dtype=np.float64)
        if p.get("level"):
            g = at(P[:, 0], P[:, 1])
            moved += int(np.sum(np.abs(g - np.array(p["y"])) > 0.05))
            p["y"] = [round(float(v), 2) for v in g]
            continue
        if "C" in p["kind"] or "T" in p["kind"]:
            continue
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(P, axis=0).T))])
        y = np.array(p["y"], dtype=np.float64)
        for e in (0, len(P) - 1):
            if p["kind"][e] != "G" and abs(p["off"][e]) > 0.3:
                continue
            g = float(at(P[e, 0], P[e, 1])[0])
            fi, fj = (P[e, 0] - grid.x0) / grid.step, (P[e, 1] - grid.z0) / grid.step
            bare = float(ndimage.map_coordinates(h, [[np.clip(fj, 0, grid.nz - 1)], [np.clip(fi, 0, grid.nx - 1)]], order=1, mode="nearest")[0])
            d = g - y[e]
            if g < bare + 0.01 or abs(d) < 0.05:
                continue            # on no shelf: mouths_to_ground's
            span = min(MOUTH_SPAN_M, s[-1])
            y += d * np.clip(1 - np.abs(s - s[e]) / span, 0, 1)
            moved += 1
        p["y"] = [round(float(v), 2) for v in y]
    # Where a road beside a cut meets another piece (a bridge's end, a ramp's foot), at that piece's height, the
    # difference spread over JOIN_SPAN_M of it.
    fixed = [(np.array(q["pts"], dtype=np.float64), np.array(q["y"])) for q in pieces if not q.get("level")]
    for p in pieces:
        if not p.get("level"):
            continue
        P = np.array(p["pts"], dtype=np.float64)
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(P, axis=0).T))])
        y = np.array(p["y"], dtype=np.float64)
        for k in range(len(P)):
            # The piece at its level there (a ramp's foot; not the cut under it at the same point in plan).
            best = None
            for Q, qy in fixed:
                d2 = np.hypot(Q[:, 0] - P[k, 0], Q[:, 1] - P[k, 1])
                m = int(np.argmin(d2))
                if d2[m] < 1.0 and abs(qy[m] - y[k]) < 1.5 and (best is None or abs(qy[m] - y[k]) < abs(best - y[k])):
                    best = float(qy[m])
            if best is not None and abs(best - y[k]) > 0.02:
                y += (best - y[k]) * np.clip(1 - np.abs(s - s[k]) / JOIN_SPAN_M, 0, 1)
                moved += 1
        # A point alone over or under both its neighbours (a shelf's edge sampled: Ring Road Central 4 m down for one
        # point), at theirs.
        for k in range(1, len(y) - 1):
            a, b = y[k] - y[k - 1], y[k] - y[k + 1]
            if a * b > 0 and min(abs(a), abs(b)) > SPIKE_M:
                y[k] = (y[k - 1] + y[k + 1]) / 2
        # An end on the ground off the shelf's edge (up to 0.9 m under the point before): the road comes down to it
        # over END_SPAN_M, not in one step.
        for e in (0, len(y) - 1):
            if p["kind"][e] == "G" and len(y) > 2:
                w = np.clip(np.abs(s - s[e]) / min(END_SPAN_M, s[-1]), 0, 1)
                y = y[e] + (y - y[e]) * w
        p["y"] = [round(float(v), 2) for v in y]
    # Where roads beside a cut meet each other on their own (Nsawam Road's side road and two links at Circle's south
    # corner: one 7.8 m at the end of its run along the wall, the others' next points 1.4 m under it, a hump), at the
    # median of their next points' heights, spread over JOIN_SPAN_M of each; not where another piece meets them.
    level = [p for p in pieces if p.get("level") and len(p["pts"]) > 1]
    seen = set()
    for p in level:
        for e in (0, len(p["pts"]) - 1):
            if (id(p), e) in seen:
                continue
            x, z = p["pts"][e]
            ends = [(q, f) for q in level for f in (0, len(q["pts"]) - 1) if math.hypot(q["pts"][f][0] - x, q["pts"][f][1] - z) < 1.0]
            for q, f in ends:
                seen.add((id(q), f))
            if len(ends) < 2 or any(np.min(np.hypot(Q[:, 0] - x, Q[:, 1] - z)) < 1.5 for Q, _ in fixed):
                continue
            target = float(np.median([q["y"][1 if f == 0 else f - 1] for q, f in ends]))
            for q, f in ends:
                Q = np.array(q["pts"], dtype=np.float64)
                sq = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(Q, axis=0).T))])
                span = min(JOIN_SPAN_M, sq[-1])
                d = target - q["y"][f]
                if abs(d) > 0.02:
                    yq = np.array(q["y"]) + d * np.clip(1 - np.abs(sq - sq[f]) / span, 0, 1)
                    # Never under the ground drawn there (the relief's own, which the game draws under the shelves).
                    fi, fj = (Q[:, 0] - grid.x0) / grid.step, (Q[:, 1] - grid.z0) / grid.step
                    bare = ndimage.map_coordinates(h, [np.clip(fj, 0, grid.nz - 1), np.clip(fi, 0, grid.nx - 1)], order=1, mode="nearest")
                    q["y"] = [round(float(v), 2) for v in np.maximum(yq, bare + LEVEL_OVER_M)]
                    moved += 1
    return moved


def world_bounds(world):
    b = None
    for a in world["areas"]:
        m = a["bounds_m"]
        ox, oz = a["offset"]
        box = (m["xmin"] + ox, m["zmin"] + oz, m["xmax"] + ox, m["zmax"] + oz)
        b = box if b is None else (min(b[0], box[0]), min(b[1], box[1]), max(b[2], box[2]), max(b[3], box[3]))
    return b


def densify(pts, step):
    """Points every `step` m along a polyline, with their distance along it."""
    seg = np.diff(pts, axis=0)
    ln = np.hypot(seg[:, 0], seg[:, 1])
    s = np.concatenate([[0], np.cumsum(ln)])
    if s[-1] < 1e-6:
        return pts[:1], np.zeros(1)
    d = np.linspace(0, s[-1], max(2, int(math.ceil(s[-1] / step)) + 1))
    return np.column_stack([np.interp(d, s, pts[:, 0]), np.interp(d, s, pts[:, 1])]), d


def smooth_profile(h, d, sigma):
    """A Gaussian along the road's length (d not quite uniform at the ends: the samples are)."""
    if len(h) < 3:
        return h
    step = d[1] - d[0]
    s = max(0.5, sigma / max(step, 1e-3))
    return ndimage.gaussian_filter1d(h, s, mode="nearest")


class Grid:
    """A regular grid in the world frame: node (i, j) at x = x0 + i * step, z = z0 + j * step."""
    def __init__(self, x0, z0, nx, nz, step):
        self.x0, self.z0, self.nx, self.nz, self.step = x0, z0, nx, nz, step

    def xz(self):
        x = self.x0 + np.arange(self.nx) * self.step
        z = self.z0 + np.arange(self.nz) * self.step
        return np.meshgrid(x, z)


def sample_dem(bare, north, west, px, origin_utm, x, z):
    """Bicubic heights of the bare DEM at world points (step 2)."""
    to_ll = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(origin_utm[0] + x.ravel(), origin_utm[1] - z.ravel())
    col = (np.asarray(lon) - west) / px - 0.5
    row = (north - np.asarray(lat)) / px - 0.5
    h = ndimage.map_coordinates(bare, [row, col], order=3, mode="nearest")
    return h.reshape(x.shape).astype(np.float32)


def carve(h, grid, roads):
    """Lays the roads into the ground (step 3), in place."""
    target = np.full(h.shape, np.nan, np.float32)
    half = np.zeros(h.shape, np.float32)
    bank = np.zeros(h.shape, np.float32)
    other = np.zeros(h.shape, bool)                # the ground's roads, which keep their own heights beside a cut
    rank = {"under": -1, "motorway": 4, "trunk": 4, "primary": 3, "secondary": 2, "cut": 5}
    order = sorted(roads, key=lambda r: rank.get(r[0], 0))   # the big roads written last: they win their nodes
    n = 0
    for cls, w, pts, *own in order:
        p, d = densify(pts, 2.0)
        fi = (p[:, 0] - grid.x0) / grid.step
        fj = (p[:, 1] - grid.z0) / grid.step
        inside = (fi >= 0) & (fi <= grid.nx - 1) & (fj >= 0) & (fj <= grid.nz - 1)
        if inside.sum() < 2:
            continue
        sigma, b = PROFILE.get(cls, PROFILE_DEFAULT)
        if own:
            # A cut: its own heights along its own points, and its own banks.
            seg = np.hypot(*np.diff(pts, axis=0).T)
            prof = np.interp(d, np.concatenate([[0], np.cumsum(seg)]), own[0])
            b = own[1]
        else:
            hb = ndimage.map_coordinates(h, [fj.clip(0, grid.nz - 1), fi.clip(0, grid.nx - 1)], order=1, mode="nearest")
            prof = smooth_profile(hb, d, sigma)
        i = np.rint(fi[inside]).astype(int)
        j = np.rint(fj[inside]).astype(int)
        target[j, i] = prof[inside]
        half[j, i] = w / 2 + (CUT_FLOOR_M if cls == "cut" else 2.0)
        bank[j, i] = b
        if cls not in ("cut", "under"):
            other[j, i] = True
        n += 1
    road = ~np.isnan(target)
    dist, (jj, ii) = ndimage.distance_transform_edt(~road, sampling=grid.step, return_indices=True)
    t = target[jj, ii]
    hw = half[jj, ii]
    bw = bank[jj, ii]
    # The nearest node of a centreline is up to half a step off the true line: count from the line, not the node.
    d = np.maximum(dist - grid.step * 0.5, 0)
    u = np.clip((d - hw) / np.maximum(bw, 1), 0, 1)
    wgt = 1 - u * u * (3 - 2 * u)
    near = np.isfinite(t)
    h[near] = h[near] + (t[near] - h[near]) * wgt[near]
    h[:] = ndimage.gaussian_filter(h, 1.0)
    hold_cut_floors(h, grid, roads, other)
    return n, other


def under_roads(pts, roads):
    """Whether a ground road crosses over this line."""
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    def cross(a, b, c, d):
        o = lambda p, q, r: np.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))
        return o(a, b, c) != o(a, b, d) and o(c, d, a) != o(c, d, b)
    for cls, w, rp, *own in roads:
        if cls in ("cut", "under") or (rp.max(axis=0) < lo).any() or (rp.min(axis=0) > hi).any():
            continue
        for i in range(len(rp) - 1):
            for k in range(len(pts) - 1):
                if cross(rp[i], rp[i + 1], pts[k], pts[k + 1]):
                    return True
    return False


def hold_cut_floors(h, grid, roads, other):
    """A cut is narrower than the carving's smoothing: without this its floor came out 1 to 4 m over its road, and the
    ground covered the road. The floor is held at the road's height to over a step beyond its edge, so that the ground
    drawn between two nodes never rises over the asphalt; but not nearer a road on the ground than the cut (the Ring
    Road's side roads along Ako Adjei's walls), which keeps its own height. In place."""
    floor = np.full(h.shape, np.nan, np.float32)
    fhalf = np.zeros(h.shape, np.float32)
    rhalf = np.zeros(h.shape, np.float32)    # the road itself and its shoulders: held even beside another road
    for cls, w, pts, *own in roads:
        if cls != "cut":
            continue
        p, d = densify(pts, 2.0)
        fi = (p[:, 0] - grid.x0) / grid.step
        fj = (p[:, 1] - grid.z0) / grid.step
        inside = (fi >= 0) & (fi <= grid.nx - 1) & (fj >= 0) & (fj <= grid.nz - 1)
        seg = np.hypot(*np.diff(pts, axis=0).T)
        prof = np.interp(d, np.concatenate([[0], np.cumsum(seg)]), own[0])
        # Held as far beyond the road as the cut is deep, CUT_FLOOR_M where it is MOUTH_DEPTH_M deep or more: at a
        # mouth, where the cut is at the ground, the roads on the ground merging into it (Tetteh Quarshie's slip road
        # to J.J. Rawlings Avenue) were held half a metre under its road inside its footprint, a step across the lanes.
        if len(own) > 2 and own[2] is not None:
            depth = -np.interp(d, np.concatenate([[0], np.cumsum(seg)]), own[2])
            reach = np.clip(CUT_FLOOR_M * depth / MOUTH_DEPTH_M, 2.0, CUT_FLOOR_M)
        else:
            reach = np.full(len(d), CUT_FLOOR_M)
        # Each sample holds the four nodes of the cell it is in, not only its nearest: the ground drawn over a cell is
        # mixed from its four corners, and a corner left at the bank's height lifted it over the road between them
        # (6 to 20 cm over 43 of the cuts' points in the game's check). Each corner takes the sample nearest it, the
        # road's height there: the lowest sample round it, on a sloping road, sank a mouth's corners up to 0.7 m under
        # the road meeting it on the ground (Tetteh Quarshie's, where the car stopped at the kerb).
        px, pz, pv, pr = p[inside, 0], p[inside, 1], prof[inside], reach[inside]
        ci, cj, cd, cv, cr = [], [], [], [], []
        for i in (np.floor(fi[inside]).astype(int), np.minimum(np.floor(fi[inside]).astype(int) + 1, grid.nx - 1)):
            for j in (np.floor(fj[inside]).astype(int), np.minimum(np.floor(fj[inside]).astype(int) + 1, grid.nz - 1)):
                ci.append(i); cj.append(j); cv.append(pv); cr.append(pr)
                cd.append(np.hypot(grid.x0 + i * grid.step - px, grid.z0 + j * grid.step - pz))
        ci, cj, cd, cv, cr = (np.concatenate(a) for a in (ci, cj, cd, cv, cr))
        order = np.argsort(-cd)                  # the nearest written last, and kept
        own_floor = np.full(h.shape, np.nan, np.float32)
        own_floor[cj[order], ci[order]] = cv[order]
        np.fmin(floor, own_floor, out=floor)
        np.fmax.at(fhalf, (cj, ci), w / 2 + cr)
        rhalf[cj, ci] = w / 2 + 2.0
    cut = ~np.isnan(floor)
    if not cut.any():
        return
    # Only inside the cuts' own footprints, square at their ends: held to its nearest cut node as far as
    # CUT_FLOOR_M, the ground beyond a cut's mouth was a trough up to 0.7 m under the road on the ground that meets
    # it there (Tetteh Quarshie's mouths, where the car stopped at the step onto the cut's road).
    within = np.zeros(h.shape, bool)
    for cls, w, pts, *own in roads:
        if cls != "cut":
            continue
        poly = LineString(pts).buffer(w / 2 + CUT_FLOOR_M, cap_style="flat")
        # Round where the cut runs on into a tunnel or another cut (an end under the ground): only a mouth is square.
        offs = own[2] if len(own) > 2 and own[2] is not None else None
        for e in (0, -1):
            if offs is None or abs(offs[e]) > 0.3:
                poly = poly.union(shapely.Point(pts[e]).buffer(w / 2 + CUT_FLOOR_M))
        x0, z0, x1, z1 = poly.bounds
        i0, i1 = max(0, int((x0 - grid.x0) // grid.step)), min(grid.nx - 1, int((x1 - grid.x0) // grid.step) + 1)
        j0, j1 = max(0, int((z0 - grid.z0) // grid.step)), min(grid.nz - 1, int((z1 - grid.z0) // grid.step) + 1)
        if i1 < i0 or j1 < j0:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        inside_poly = shapely.contains_xy(poly, grid.x0 + ii * grid.step, grid.z0 + jj * grid.step)
        within[jj[inside_poly], ii[inside_poly]] = True
    dc, (jc, ic) = ndimage.distance_transform_edt(~cut, sampling=grid.step, return_indices=True)
    do = ndimage.distance_transform_edt(~other, sampling=grid.step) if other.any() else np.full(h.shape, np.inf)
    d = np.maximum(dc - grid.step * 0.5, 0)
    inside = (((d <= fhalf[jc, ic]) & (dc < do)) | (d <= rhalf[jc, ic])) & within
    f = floor[jc, ic]
    h[inside] = np.minimum(h[inside], f[inside])


def encode(h, q_step=None):
    """See the module's docstring: quantised, predicted, zigzagged, byte planes, zlib."""
    q = np.clip(np.rint((h - H0) / (q_step or Q)), 0, 65535).astype(np.int32)
    pred = np.zeros_like(q)
    pred[0, 1:] = q[0, :-1]
    pred[1:, 0] = q[:-1, 0]
    pred[1:, 1:] = q[1:, :-1] + q[:-1, 1:] - q[:-1, :-1]
    r = (q - pred) & 0xFFFF
    r = np.where(r >= 0x8000, r - 0x10000, r)   # signed residual
    z = ((r << 1) ^ (r >> 31)) & 0xFFFF          # zigzag
    z = z.astype(np.uint16).ravel()
    planes = np.concatenate([(z & 0xFF).astype(np.uint8), (z >> 8).astype(np.uint8)])
    return zlib.compress(planes.tobytes(), 9)


def decode(buf, nx, nz, q_step=None):
    """The inverse of encode(), as the game does it (for the round-trip check)."""
    planes = np.frombuffer(zlib.decompress(buf), np.uint8)
    n = nx * nz
    z = planes[:n].astype(np.int32) | (planes[n:].astype(np.int32) << 8)
    r = (z >> 1) ^ -(z & 1)
    r = r.reshape(nz, nx)
    q = np.zeros((nz, nx), np.int32)
    for j in range(nz):
        for i in range(nx):
            a = q[j, i - 1] if i else 0
            b = q[j - 1, i] if j else 0
            c = q[j - 1, i - 1] if i and j else 0
            p = a if not j else (b if not i else a + b - c)
            q[j, i] = (r[j, i] + p) & 0xFFFF
    return q * (q_step or Q) + H0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", required=True, help="the world manifest (export_world.py)")
    ap.add_argument("--out", required=True, help="folder for relief.json, coarse.bin and the tiles")
    ap.add_argument("--dem", default="data/dem", help="folder of the Copernicus GLO-30 tiles (fetched when missing)")
    ap.add_argument("--cover", default="data/worldcover", help="folder of the ESA WorldCover tiles (fetched when missing)")
    ap.add_argument("--elevated", default="data/work/elevated.json",
                    help="the interchanges and flyovers (elevated.py): their heights in structures.json, their cuts laid in")
    ap.add_argument("--preview", help="a PNG of the relief and the tiles, for a look")
    args = ap.parse_args()

    world, roads = world_roads(args.world)
    o = world["origin_wgs84"]
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True)
    origin = to_utm.transform(o["lon"], o["lat"])
    wx0, wz0, wx1, wz1 = world_bounds(world)
    # The coarse grid's box, on its own step; the DEM box around it.
    cx0 = math.floor((wx0 - REACH) / TILE) * TILE
    cz0 = math.floor((wz0 - REACH) / TILE) * TILE
    cx1 = math.ceil((wx1 + REACH) / TILE) * TILE
    cz1 = math.ceil((wz1 + REACH) / TILE) * TILE
    lons, lats = to_ll.transform([origin[0] + cx0, origin[0] + cx1, origin[0] + cx0, origin[0] + cx1],
                                 [origin[1] - cz0, origin[1] - cz0, origin[1] - cz1, origin[1] - cz1])
    dem, north, west, px = load_dem(args.dem, min(lats) - 0.01, max(lats) + 0.01, min(lons) - 0.01, max(lons) + 0.01)
    # Crop to the box (plus a margin for the filters) before the filters.
    r0 = max(0, int((north - max(lats)) / px) - 40)
    r1 = min(dem.shape[0], int((north - min(lats)) / px) + 40)
    c0 = max(0, int((min(lons) - west) / px) - 40)
    c1 = min(dem.shape[1], int((max(lons) - west) / px) + 40)
    dem = dem[r0:r1, c0:c1]
    north -= r0 * px
    west += c0 * px
    print(f"DEM {dem.shape[1]} x {dem.shape[0]} px, {dem.min():.1f} to {dem.max():.1f} m")
    bare = bare_earth(dem)

    # Fine tiles: those within NEAR of a road.
    samples = np.vstack([densify(p, 50.0)[0] for _, _, p in roads])
    ti = np.floor((samples[:, 0] - cx0) / TILE).astype(int)
    tj = np.floor((samples[:, 1] - cz0) / TILE).astype(int)
    reach = int(math.ceil(NEAR / TILE))
    tiles = set()
    for i, j in set(zip(ti.tolist(), tj.tolist())):
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                tiles.add((i + di, j + dj))
    tiles = sorted(tiles)
    i0 = min(t[0] for t in tiles); i1 = max(t[0] for t in tiles) + 1
    j0 = min(t[1] for t in tiles); j1 = max(t[1] for t in tiles) + 1
    per = int(TILE / STEP)
    # One fine grid over the tiles' box (with a margin of 64 nodes for the carving's reach), carved once.
    pad = 64
    fine = Grid(cx0 + i0 * TILE - pad * STEP, cz0 + j0 * TILE - pad * STEP, (i1 - i0) * per + 1 + 2 * pad, (j1 - j0) * per + 1 + 2 * pad, STEP)
    print(f"fine grid {fine.nx} x {fine.nz} nodes, {len(tiles)} tiles of {TILE:.0f} m, {len(roads)} roads")
    x, z = fine.xz()
    h = sample_dem(bare, north, west, px, origin, x, z)
    del x, z
    raw = h.copy()
    pieces, feet = world_structures(world, os.path.dirname(args.world), args.elevated)
    natural = ndimage.gaussian_filter(raw, NATURAL_SIGMA_M / STEP) if pieces else None
    pieces = pieces or []
    cuts = []
    if pieces:
        # First heights from the natural ground alone, for the cuts' floors.
        structure_heights(pieces, natural, natural, fine)
        for p in pieces:
            pts = np.array(p["pts"], dtype=np.float64)
            if "T" in p["kind"] and "C" not in p["kind"] and under_roads(pts, roads):
                # A tunnel under the ground's roads (the Tema Motorway roundabout): the ground stays over it; the game
                # lowers only its physics' ground there, under a roof. One under nothing is laid in like a cut.
                continue
            if "C" in p["kind"] or "T" in p["kind"]:
                # The cut's floor a little under its road, which the game draws at the piece's own heights.
                cuts.append(("cut", float(p["w"]), pts, cut_floor(p["y"], p["off"]),
                             CUT_BANK_M.get(p["style"], CUT_BANK_DEFAULT), np.array(p["off"])))
            else:
                # Under a deck or a ramp the ground is the natural ground, levelled a little under it so no bump of the
                # DEM shows through the ramp's foot; the roads that pass under are laid after, and win.
                cuts.append(("under", float(p["w"]), pts, np.array(p["y"]) - np.array(p["off"]) - (LEVEL_UNDER_M if p.get("level") else UNDER_M), 6.0))
    carved, other = carve(h, fine, roads + cuts)
    moved = np.abs(h - raw)
    print(f"{carved} roads laid in; ground moved {np.percentile(moved, 99):.2f} m at the 99th percentile, {moved.max():.1f} m at most")
    del raw, moved
    if pieces or feet:
        structure_heights(pieces, natural, h, fine)
        # The cuts' floors again, under their roads' final heights (their ends meet the carved ground).
        held = [p for p in pieces if "C" in p["kind"] or ("T" in p["kind"] and not under_roads(np.array(p["pts"]), roads))]
        hold = lambda: hold_cut_floors(h, fine, [("cut", float(p["w"]), np.array(p["pts"], dtype=np.float64), cut_floor(p["y"], p["off"]),
                                                  None, np.array(p["off"])) for p in held], other)
        hold()
        # A cut's mouth meets the ground where the roads on the ground were carved, which can lie half a metre off the
        # height its road was given before them (two roads meeting there, at Tetteh Quarshie): the road comes to the
        # ground at its mouth, over MOUTH_SPAN_M, and its floor is held again under it; so do the other pieces' ends
        # on the ground (a ramp's foot, a bridge at the ground's level).
        if mouths_to_ground(pieces, h, fine):
            hold()
        # Then the bridges at the ground's level carried on over what the cuts' floors and banks took.
        print(f"bridges carried on over a cut's floor: {bridges_over_holes(pieces, h, fine, roads)} ends")
        print(f"cuts' shelves at the banks' top: {cut_shelves(pieces, h, fine, roads):.0f} m2")
        print(f"onto the shelves: {onto_shelves(pieces, h, fine)} points of the roads beside the cuts, ends of others")
        # A footbridge's ground at its points (its deck stands its height over the highest of them). One at the
        # ground's level over a cut (Ako Adjei's, elevated.py FOOT_AT_GROUND_M) lies wholly over the cut's held floor:
        # its ground is the natural ground there, the cut's banks' top, at every point.
        for f in feet:
            pts = np.array(f["pts"], dtype=np.float64)
            fi = (pts[:, 0] - fine.x0) / fine.step
            fj = (pts[:, 1] - fine.z0) / fine.step
            field = natural if f["h"] < 2.0 and natural is not None else h
            g = ndimage.map_coordinates(field, [fj.clip(0, fine.nz - 1), fi.clip(0, fine.nx - 1)], order=1, mode="nearest")
            f["g"] = [round(float(g.max()), 2)] * len(f["pts"]) if f["h"] < 2.0 else [round(float(v), 2) for v in g]
        del natural
        with open(os.path.join(args.out, "structures.json"), "w") as f:
            json.dump({"axes": world["axes"], "level_m": 7.0, "pieces": pieces, "footbridges": feet,
                       "credits": "Interchanges, flyovers and footbridges: OpenStreetMap contributors (ODbL)."},
                      f, separators=(",", ":"))
        print(f"structures: {len(pieces)} pieces, {sum(c[0] == 'cut' for c in cuts)} laid in as cuts, {len(feet)} footbridges")

    os.makedirs(os.path.join(args.out, "t"), exist_ok=True)
    total = 0
    for (i, j) in tiles:
        a = pad + (i - i0) * per
        b = pad + (j - j0) * per
        t = h[b:b + per + 1, a:a + per + 1]
        buf = encode(t)
        with open(os.path.join(args.out, "t", f"{i}_{j}.bin"), "wb") as f:
            f.write(buf)
        total += len(buf)

    # Coarse and far: the carved fine grid where it reaches, the bare DEM beyond.
    def grid_heights(step):
        g = Grid(cx0, cz0, int((cx1 - cx0) / step) + 1, int((cz1 - cz0) / step) + 1, step)
        x, z = g.xz()
        hg = sample_dem(bare, north, west, px, origin, x, z)
        fi = (x - fine.x0) / STEP
        fj = (z - fine.z0) / STEP
        inside = (fi >= 0) & (fi <= fine.nx - 1) & (fj >= 0) & (fj <= fine.nz - 1)
        hg[inside] = ndimage.map_coordinates(h, [fj[inside], fi[inside]], order=1)
        return g, np.maximum(hg, -5.0)    # the sea stays at sea level
    coarse, hc = grid_heights(COARSE)
    cbuf = encode(hc, COARSE_Q)
    with open(os.path.join(args.out, "coarse.bin"), "wb") as f:
        f.write(cbuf)
    far, hf = grid_heights(FAR)
    fbuf = encode(hf, FAR_Q)
    with open(os.path.join(args.out, "far.bin"), "wb") as f:
        f.write(fbuf)

    # The land cover on the far grid's nodes.
    cov, cnorth, cwest, cpx = load_cover(args.cover, min(lats) - 0.01, max(lats) + 0.01, min(lons) - 0.01, max(lons) + 0.01)
    shares = cover_shares(cov, cnorth, cwest, cpx, origin, far)
    del cov
    parts = [zlib.compress(a.tobytes(), 9) for a in shares]
    vbuf = b"".join(parts)
    vlens = [len(b) for b in parts]
    with open(os.path.join(args.out, "cover.bin"), "wb") as f:
        f.write(vbuf)
    print(f"cover: built {shares[0].mean() / 2.55:.0f}%, trees {shares[1].mean() / 2.55:.0f}%, water "
          f"{shares[2].mean() / 2.55:.0f}% of the box; {len(vbuf) / 1024:.0f} KB")

    # Round trip on one tile.
    t0 = tiles[len(tiles) // 2]
    back = decode(open(os.path.join(args.out, "t", f"{t0[0]}_{t0[1]}.bin"), "rb").read(), per + 1, per + 1)
    a = pad + (t0[0] - i0) * per
    b = pad + (t0[1] - j0) * per
    err = np.abs(back - h[b:b + per + 1, a:a + per + 1]).max()
    if err > Q:
        sys.exit(f"round trip off by {err} m")

    meta = {
        "version": 1,
        "axes": world["axes"],
        "source": "Copernicus DEM GLO-30, bare earth by a 150 m grey opening, roads laid in (circle-geodata scripts/relief.py)",
        "attribution": ATTRIBUTION,
        "enc": {"h0": H0, "q": Q, "predictor": "planar", "zigzag": True, "planes": "lo,hi", "zlib": True},
        "tile": {"m": TILE, "step": STEP, "n": per + 1, "x0": cx0, "z0": cz0, "folder": "t"},
        "tiles": [[i, j] for i, j in tiles],
        "coarse": {"file": "coarse.bin", "x0": cx0, "z0": cz0, "nx": coarse.nx, "nz": coarse.nz, "step": COARSE,
                   "q": COARSE_Q, "bytes": len(cbuf)},
        "far": {"file": "far.bin", "x0": cx0, "z0": cz0, "nx": far.nx, "nz": far.nz, "step": FAR, "q": FAR_Q,
                "bytes": len(fbuf)},
        "cover": {"file": "cover.bin", "planes": ["built", "trees", "water"], "bytes": vlens,
                  "source": "ESA WorldCover 2021 v200, shares of 64 samples per far node", "attribution": COVER_ATTRIBUTION},
        "range_m": [round(float(hf.min()), 1), round(float(hf.max()), 1)],
        "tile_bytes": total,
    }
    with open(os.path.join(args.out, "relief.json"), "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    print(f"relief: {len(tiles)} tiles, {total / 1048576:.2f} MB ({total / len(tiles) / 1024:.1f} KB each); coarse "
          f"{coarse.nx} x {coarse.nz} at {COARSE:.0f} m, {len(cbuf) / 1024:.0f} KB; far {far.nx} x {far.nz} at {FAR:.0f} m, "
          f"{len(fbuf) / 1024:.0f} KB; {hf.min():.0f} to {hf.max():.0f} m")

    if args.preview:
        from PIL import Image
        v = np.clip((hf - hf.min()) / max(1, hf.max() - hf.min()), 0, 1)
        gy, gx = np.gradient(hf, FAR)
        shade = np.clip(0.75 - (gx - gy) * 1.5, 0.2, 1)
        rgb = np.stack([v * 0.6 + 0.3, v * 0.5 + 0.35, 0.4 - v * 0.2], -1) * shade[..., None]
        img = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
        k = int(TILE / FAR)
        for (i, j) in tiles:
            img[j * k, i * k:(i + 1) * k] = (255, 255, 255)
            img[j * k:(j + 1) * k, i * k] = (255, 255, 255)
        Image.fromarray(img).save(args.preview)


if __name__ == "__main__":
    main()
