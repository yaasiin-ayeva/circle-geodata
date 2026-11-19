"""Which kind of building a footprint is: the typology rules that pick massing.py's builder for it.

Used by build_district.py. Most of Accra will never be modelled by hand, and most of its footprints carry no tag at all
(fewer than one in ten has an OpenStreetMap building class), so a footprint's kind comes from what is known of it:
its OpenStreetMap or Overture class (`building=*`), its name, the places mapped inside it (OSM `amenity`, `shop`,
`office`, Overture's categories), its storeys (tagged, or drawn by the district's rules), its area, the class of the
road it fronts and its district. Each rule is deterministic per building (`h01` seeds): the same city at every build.

Kinds (the first rule that holds wins, in this order):
  worship    churches and mosques: a class of worship, `amenity=place_of_worship`, or a name that says so. A mosque
             when its class or names say mosque or masjid; a church otherwise. massing.church() and massing.mosque().
  warehouse  sheds and big boxes: `building=warehouse|industrial|manufacture|hangar|factory|storage`, or a retail or
             supermarket footprint of 1,200 m2 or more without mapped floors. One tall storey under a low gable of sheet
             with roller doors (massing.warehouse()).
  tower      glass office towers (Airport City, Ridge): 22 m or more, or seven storeys or more, of an office,
             commercial or hotel class or no class, and offices of five storeys or more. A curtain wall (the game draws
             the glass, massing.CURTAIN), over a podium on large footprints (massing.tower()).
  apartments mid-rise flats (East Legon, Airport Residential, Cantonments): four storeys or more of a residential,
             apartments or hotel class, or of no class outside the old centre. Balconies on most bays, glass rails
             (massing.BALCONIES).
  storey     the "storey building" of the commercial streets: two to four storeys, shops below and flats above, on the
             frontage of a main road (or of any street in the old centre and Kaneshie): a concrete canopy slab over the
             shops, balconies above, a painted name band (massing.SHOPFRONT).
  kiosk      kiosks and container shops on the verges: footprints under 20 m2 of one storey on a street's frontage
             (the small footprints behind, in the yards, are outbuildings and stay generic; massing.kiosk()).
  generic    everything else: massing.building() as before.
"""
import re

import numpy as np
import shapely
import shapely.ops

from massing import h01

WORSHIP_CLASSES = {"church", "cathedral", "chapel", "mosque", "temple", "religious", "shrine", "synagogue"}
MOSQUE_CLASSES = {"mosque"}
WORSHIP_NAME = re.compile(r"\b(church|chapel|cathedral|tabernacle|mosque|masjid|temple|assembl(y|ies) of god|"
                          r"pentecost|methodist|presbyterian|anglican|baptist|catholic|adventist|parish)\b", re.I)
# Names that say a church's or a mosque's business, not a place of worship ("Methodist Book Shop", "Cathedral Clinic").
NOT_WORSHIP = re.compile(r"\b(shop|book\w*|clinic|lab|laborator\w*|school|institute|hospital|college|office|"
                         r"secretariat|headquarters|hq|guest ?house|hotel|bank|pharmacy|store|centre for|academy)\b", re.I)
MOSQUE_NAME = re.compile(r"\b(mosque|masjid|islam\w*|ahmadiyy?a|jama'?a|muslim)\b", re.I)
WAREHOUSE_CLASSES = {"warehouse", "industrial", "manufacture", "hangar", "factory", "storage"}
BIGBOX_CLASSES = {"retail", "supermarket", "commercial"}
OFFICE_CLASSES = {"office", "commercial", "hotel"}
HOME_CLASSES = {"apartments", "residential", "hotel", "dormitory"}
# Classes the storey building and the apartment rules may take over: none, or the vague residential and commercial.
OPEN_CLASSES = {None, "", "yes", "residential", "commercial", "retail", "apartments"}
# Classes that are not a kiosk however small (a canopy, a toilet block, a hut in a village, a transformer).
NOT_KIOSK = {"roof", "toilets", "hut", "transformer_tower", "service", "house", "garage", "carport"}
MAIN_ROADS = ("motorway", "trunk", "primary", "secondary", "tertiary")
# The old centre and the market districts: every street is a commercial street, and large untagged blocks are offices
# and market buildings, not flats.
DENSE = {"central", "jamestown", "kaneshie"}


def front_index(roads, width):
    """A spatial index of the roads for frontage(): roads (class, line in metres), width(class) the carriageway's."""
    lines = [l for _, l in roads]
    return shapely.STRtree(lines), [c for c, _ in roads], [width(c) / 2 for c, _ in roads], lines


def frontage(index, poly, reach=6.0):
    """The best road class the footprint fronts: the main road classes first, any street after, None when no road's
    edge comes within `reach` metres of it."""
    tree, classes, halves, lines = index
    best = None
    for i in tree.query(poly, predicate="dwithin", distance=reach + 12.0):
        if poly.distance(lines[i]) - halves[i] > reach:
            continue
        c = classes[i]
        if best is None or (c in MAIN_ROADS and (best not in MAIN_ROADS or MAIN_ROADS.index(c) < MAIN_ROADS.index(best))):
            best = c
    return best


def classify(bid, area, storeys, tagged, height, cls, name, places, front, district, tower_zone=False):
    """The kind of one footprint (see the module's docstring).

    storeys: its storeys, tagged or drawn; tagged: they come from OpenStreetMap (num_floors) or a height; height: the
    mapped height in metres or None; cls: its building class or None; name: its own name; places: ("key=value", name)
    of the places inside it (OSM amenity, shop, office..., Overture category=...); front: frontage()'s road class;
    district: the area's name (build_district AREAS); tower_zone: the footprint stands in a district of office towers
    (build_district TOWER_ZONES), where large untagged footprints are towers."""
    cls = (cls or "").lower() or None
    tags = {t for t, _ in places}
    # Worship: by class, by OSM's tag, or by name: the building's own, or a religious organisation's inside it.
    holy = [n for n in [name or ""] + [n for t, n in places if t in ("amenity=place_of_worship", "category=religious_organization")]
            if n and not NOT_WORSHIP.search(n)]
    text = " ".join(holy)
    worship = (cls in WORSHIP_CLASSES or "amenity=place_of_worship" in tags
               or (cls in (None, "yes") and WORSHIP_NAME.search(text) is not None))
    if worship and 80 <= area <= 4000 and storeys <= 3:
        return "mosque" if cls in MOSQUE_CLASSES or MOSQUE_NAME.search(text) else "church"
    if (cls in WAREHOUSE_CLASSES and area >= 150) or (cls in ("retail", "supermarket") and area >= 1200 and not tagged):
        return "warehouse"
    tall = (height or 0) >= 22 or storeys >= 7
    # Offices: mapped as such, or an office, a bank or a ministry inside.
    business = cls in ("office", "commercial") or any(t.startswith(("office=", "amenity=bank")) for t in tags)
    if tower_zone and not tagged and area >= 700 and cls in (None, "yes", "office", "commercial"):
        return "tower"
    if area >= 250 and tagged and ((tall and (cls in OFFICE_CLASSES or cls is None)) or (business and storeys >= 5)):
        return "tower"
    # Flats: mapped as homes, or four storeys or more with nothing else known of them, outside the old centre.
    homely = cls in HOME_CLASSES or (cls in (None, "yes") and not business and district not in DENSE
                                     and not any(t.startswith(("amenity=", "office=", "shop=")) for t in tags))
    if storeys >= 4 and area >= 150 and homely:
        return "apartments"
    if (2 <= storeys <= 4 and 60 <= area <= 900 and cls in OPEN_CLASSES
            and (front in MAIN_ROADS or (front is not None and district in DENSE)) and h01(bid, "storey") < 0.85):
        return "storey"
    if area < 20 and storeys == 1 and cls not in NOT_KIOSK and front is not None:
        return "kiosk"
    return "generic"


def place_hits(rows, polys):
    """For each footprint, the places that lie inside it: rows (point, "key=value", name) in metres; polys the
    footprints. Returns a list of [("key=value", name)] per footprint."""
    out = [[] for _ in polys]
    if not rows or not polys:
        return out
    tree = shapely.STRtree(polys)
    a, b = tree.query([r[0] for r in rows], predicate="within")
    for ri, pi in zip(a, b):
        out[pi].append((rows[ri][1], rows[ri][2]))
    return out


def slice_places(slice_dir, local, con):
    """The places of a slice as place_hits() rows, in metres: OpenStreetMap's (osm.parquet, "key=value") and Overture's
    (places.parquet, "category=..."), each with its name. Either file may be missing (an older slice): none then."""
    import os

    import pyarrow.parquet as pq
    from shapely.geometry import Point

    rows = []
    osm = os.path.join(slice_dir, "osm.parquet")
    if os.path.exists(osm):
        for r in pq.read_table(osm, columns=["key", "value", "name", "lon", "lat"]).to_pylist():
            rows.append((local(Point(r["lon"], r["lat"])), f"{r['key']}={r['value']}", r["name"] or ""))
    places = os.path.join(slice_dir, "places.parquet")
    if os.path.exists(places):
        for cat, name, blob in con.execute(f"SELECT category, name, geometry::BLOB FROM '{places}'").fetchall():
            rows.append((local(shapely.from_wkb(bytes(blob))), f"category={cat}", name or ""))
    return rows


class Places:
    """The places of a slice indexed by position, for inside(): rows (point, "key=value", name) in metres."""

    def __init__(self, rows):
        self.rows = rows
        self.tree = shapely.STRtree([r[0] for r in rows]) if rows else None

    def inside(self, poly):
        if self.tree is None:
            return []
        return [(self.rows[i][1], self.rows[i][2]) for i in self.tree.query(poly, predicate="contains")]


def facing(index, poly):
    """The unit plan vector from the footprint's middle towards the nearest road (the facade a name is painted on), or
    None without a road within 40 m."""
    tree, _, _, lines = index
    c = poly.centroid
    near = tree.query(c, predicate="dwithin", distance=40.0)
    if not len(near):
        return None
    line = min((lines[i] for i in near), key=c.distance)
    p = shapely.ops.nearest_points(line, c)[0]
    d = np.array([p.x - c.x, p.y - c.y])
    n = float(np.hypot(*d))
    return None if n < 1e-6 else d / n


def worship_name(name, places):
    """A church's or mosque's real name, from OpenStreetMap or Overture: the place of worship's mapped inside it first,
    then the building's own, then a religious organisation's; never a name that says another business (NOT_WORSHIP),
    and None rather than a guess."""
    ranked = ([n for t, n in places if t == "amenity=place_of_worship"] + [name]
              + [n for t, n in places if t == "category=religious_organization"])
    for n in ranked:
        if n and WORSHIP_NAME.search(n) and not NOT_WORSHIP.search(n):
            return n.strip()
    for n in ranked[:2]:
        if n and not NOT_WORSHIP.search(n) and len(n.strip()) >= 3:
            return n.strip()
    return None


def shop_name(name, places):
    """A storey building's name band: its own name, else the first named shop, office or business inside it."""
    if name and 3 <= len(name.strip()) <= 40:
        return name.strip()
    for t, n in places:
        if n and t.startswith(("shop=", "office=", "amenity=bank", "amenity=pharmacy", "category=")) and 3 <= len(n.strip()) <= 40:
            return n.strip()
    return None
