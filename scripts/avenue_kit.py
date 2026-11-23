"""The Independence Avenue kit: the typology each generic building of the avenue's corridor gets (circle-game
docs/independence-avenue-plan.md, stage 2, the director's decisions of 22 Nov 2026).

Used by build_district.py --avenue-kit. The corridor, the avenue's reference line and segments, the plan's OSM lots
with their tags, and the buildings the plan names with their typology are in aoi/independence-avenue-kit.json (from the
plan's survey of 22 Nov 2026). Only footprints with a point in the corridor (or that the plan names) change; everything else is built
as before, so the other districts do not change.

The typologies are the plan's (Appendix C): T1 villa behind a wall, T2 office slab, T3 glass office tower, T4 bank
branch, T5 ministry block, T6 colonial bungalow, T7 hospital or barracks low block, T8 apartment block, T9 shop and
office block, T10 kiosk, T11 church, T12 hotel slab, T13 multi-storey car park, T14 school block, T15 trotro station
yard, T16 filling station; and VOL, the plain volume behind a wall of the sensitive sites. How a footprint's is chosen
(choose(), the first rule that holds wins, the plan's own rules in its survey):

  1. A building the plan names (its "buildings"): the plan's typology. T3 (the tower generator, stages 3 and 4), T15
     (the stations' yard kit, stage 4), the hero models (stages 3 and 4), the modelled landmarks and the generic
     buildings on Jubilee House's grounds keep the generic massing (None).
  2. A machine-learnt footprint (Google or Microsoft, not OpenStreetMap) under 40 m2: as drawn today (None). The plan
     proposed kiosks; the director kept them as they are (22 Nov 2026).
  3. In a station's lot (the plan's T15 lots): its sheds under 300 m2 wait for the yard kit (None), larger buildings are
     shop blocks (T9). On Jubilee House's grounds: None.
  4. By the uses OpenStreetMap and Overture give the footprint (its class, the places inside it) and its lot's tags:
     an embassy, a military site: VOL; a hospital: T7; a place of worship: T11; a school or college: T14; a hotel:
     T12; a government office: T5; under 40 m2: T10; a bank or an ATM: T4 (T2 from 1,200 m2); nine levels or more:
     None (T3); flats outside Tudu: T8; on a car park's or a park's lot, under 100 m2: T10; in segment F (the
     Ministries to Tudu): T9; offices, commerce, three levels or more or 700 m2 or more: T2; else T1.

The storeys: OpenStreetMap's (or Overture's) where mapped; otherwise the district's rule by area, held to the
typology's range (STOREYS) and said to be estimated.
"""
import json
import os

import shapely
from shapely.geometry import LineString, Point, Polygon

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aoi", "independence-avenue-kit.json")

NAMES = {"T1": "villa behind a wall", "T2": "office slab", "T3": "glass office tower", "T4": "bank branch",
         "T5": "ministry block", "T6": "colonial bungalow", "T7": "hospital or barracks low block",
         "T8": "apartment block", "T9": "shop and office block", "T10": "kiosk, container or ATM booth",
         "T11": "church", "T12": "hotel slab", "T13": "multi-storey car park", "T14": "school block",
         "T15": "trotro station yard", "T16": "filling station", "VOL": "plain volume behind a wall"}

# The typologies built here; the others keep the generic massing for their own stage (T3 and T15: stages 3 and 4;
# T16: the game's fuel-station module draws the forecourt and canopy already).
BUILT = ("T1", "T2", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11", "T12", "T13", "T14", "VOL")

# Each typology's storeys where none are mapped (the plan's Appendix C): the area rule's estimate held to this range.
STOREYS = {"T1": (1, 2), "T2": (3, 8), "T4": (1, 2), "T5": (3, 6), "T6": (1, 2), "T7": (1, 3), "T8": (3, 6),
           "T9": (2, 5), "T10": (1, 1), "T11": (1, 1), "T12": (4, 8), "T13": (4, 6), "T14": (1, 2), "VOL": (1, 4)}

WORSHIP = ("church", "cathedral", "chapel", "temple", "place_of_worship", "religious", "mosque")
SCHOOL = ("school", "college", "university", "kindergarten", "education")
GOVERNMENT = ("government", "townhall", "public_building", "civic", "courthouse", "ministry")


def load(path=DATA):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def uses(cls, places):
    """The text the rules read: the footprint's class and its places' "key=value" and Overture categories, lower case."""
    return " ".join([cls or ""] + [t for t, _ in places]).lower()


def choose(area, levels, cls, places, lot=None, named=None, segment=None, ml=False):
    """The typology of one footprint (see the module's docstring), or None to keep the generic massing.

    area: m2; levels: mapped storeys or None; cls: its building class (OSM's building=*, through Overture) or None;
    places: ("key=value", name) of the places inside it (osm.parquet's "amenity=bank", places.parquet's "category=");
    lot: the plan's OSM lot it stands in ({"treat", "tags"}) or None; named: the plan's entry for this very building
    ({"treat"}) or None; segment: "A" to "F" by chainage; ml: a machine-learnt footprint (Google, Microsoft)."""
    if named is not None:
        t = named.get("treat")
        return t if t in BUILT else None
    if ml and area < 40:
        return None
    lt = (lot or {}).get("tags") or {}
    lot_treat = (lot or {}).get("treat")
    if lot_treat == "T15":
        return "T9" if area >= 300 else None
    if lot_treat in ("KEEP", "MASK", "HERO"):
        return None
    u = uses(cls, places)
    if "diplomatic" in u or "embassy" in u or lt.get("diplomatic"):
        return "VOL"
    if lt.get("military") or lt.get("landuse") == "military" or cls == "military":
        return "VOL"
    if "hospital" in u or lt.get("amenity") == "hospital":
        return "T7"
    if any(k in u for k in WORSHIP):
        return "T11"
    if any(k in u for k in SCHOOL) or lt.get("amenity") in ("school", "college"):
        return "T14"
    if "hotel" in u:
        return "T12"
    if any(k in u for k in GOVERNMENT) or lt.get("government") or lt.get("office") == "government":
        return "T5"
    if area < 40:
        return "T10"
    if "bank" in u or "atm" in u:
        return "T4" if area < 1200 else "T2"
    if (levels or 0) >= 9:
        return None
    if "apartments" in u and segment != "F":
        return "T8"
    if (lt.get("amenity") == "parking" or lt.get("leisure") in ("park", "garden")) and area < 100:
        return "T10"
    if segment == "F":
        return "T9"
    if any(k in u for k in ("commercial", "office", "retail")) or (levels or 0) >= 3 or area >= 700:
        return "T2"
    return "T1"


def storeys_for(kind, estimate, mapped):
    """The storeys a typology builds: the mapped ones, else the area rule's estimate held to STOREYS[kind]."""
    if mapped:
        return int(mapped)
    lo, hi = STOREYS.get(kind, (1, 99))
    return max(lo, min(hi, int(estimate)))


class Avenue:
    """The kit's data in the area's metres: local(geom) turns a lon/lat geometry into them."""

    def __init__(self, data, local):
        self.data = data
        self.corridor = shapely.union_all([local(Polygon(r)) for r in data["corridor"]])
        shapely.prepare(self.corridor)
        self.line = local(LineString(data["line"]))
        self.segments = data["segments"]
        self.lots = []
        for lot in data["lots"]:
            g = local(Polygon(lot["outline"])).buffer(0)
            if not g.is_empty:
                self.lots.append((g, lot))
        self.lot_tree = shapely.STRtree([g for g, _ in self.lots]) if self.lots else None
        self.named = [(local(Point(b["at"])), b) for b in data["buildings"]]
        self.named_tree = shapely.STRtree([p for p, _ in self.named]) if self.named else None

    def touches(self, region):
        return self.corridor.intersects(region)

    def inside(self, poly):
        """In the corridor as the plan counts it: some point of the footprint lies in it (its survey, Appendix D), or
        the plan names the building (the towers set back beyond the band)."""
        return bool(self.corridor.intersects(poly)) or self.named_of(poly) is not None

    def segment(self, poly):
        s = self.line.project(poly.centroid)
        for k, a, b in self.segments:
            if a <= s < b:
                return k
        return self.segments[-1][0]

    def lot_of(self, poly):
        """The smallest of the plan's OSM lots holding the footprint's representative point, or None."""
        if self.lot_tree is None:
            return None
        p = poly.representative_point()
        hits = [self.lots[i] for i in self.lot_tree.query(p, predicate="within")]
        return min(hits, key=lambda h: h[0].area)[1] if hits else None

    def named_of(self, poly):
        """The plan's entry for this footprint: a named building whose point it holds, its area within a factor of two
        of the plan's (another release may have split or merged it otherwise), or None."""
        if self.named_tree is None:
            return None
        for i in self.named_tree.query(poly, predicate="contains"):
            b = self.named[i][1]
            if 0.5 <= poly.area / max(1.0, b.get("area_m2") or poly.area) <= 2.0:
                return b
        return None
