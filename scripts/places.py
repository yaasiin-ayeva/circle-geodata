"""The named places the game's maps show (the minimap and the big map): hospitals, police, fuel, banks, markets,
schools, churches and mosques, hotels, transport stations, landmarks, food and night life, with an icon category and a
rank. Chosen from the slice's places.parquet (scripts/overture_slice.py export): Overture places, and
OpenStreetMap's named land use and stations from Overture's base theme.

build_district.py calls select_places() for each area and writes the result as the district's "places";
export_scene.py passes them to the game. The data is as current as the Overture release the city is built from
(OVERTURE_RELEASE, monthly): rebuilding the city with a newer release refreshes them. The game never asks a service
for places while it runs, so no player's position leaves the machine.

    python3 tests/test_places.py
"""
import math
import re

# The game's icon categories (circle-game src/ui/minimap-places.ts draws one icon each), in the order of their weight:
# how much a place of the kind matters to a driver finding their way in Accra. Each lists the Overture places'
# categories (basic_category, else taxonomy.primary) and the base theme's land use and infrastructure classes it takes.
CATEGORIES = {
    "hospital": (1.00, {"hospital", "emergency_department", "emergency_or_urgent_care_facility", "clinic"}),
    "landmark": (0.95, {"monument", "museum", "stadium_arena", "stadium", "lighthouse", "fort", "castle", "public_plaza",
                        "plaza", "park", "beach", "golf_course", "zoo", "amusement_park", "cultural_center"}),
    "transit": (0.90, {"bus_station", "railway_station", "international_airport", "airport", "ferry_terminal"}),
    "police": (0.85, {"police_station"}),
    "fuel": (0.80, {"gas_station"}),
    "market": (0.75, {"market", "farmers_market", "shopping_mall", "department_store", "marketplace", "retail"}),
    "bank": (0.70, {"bank_or_credit_union"}),
    "university": (0.70, {"college_university", "university", "college", "military_school"}),
    "embassy": (0.60, {"embassy"}),
    "hotel": (0.55, {"hotel", "resort", "beach_resort"}),
    "school": (0.50, {"high_school", "elementary_school", "middle_school", "school"}),
    "worship": (0.50, {"christian_place_of_worship", "muslim_place_of_worship", "place_of_worship",
                       "hindu_place_of_worship"}),
    "pharmacy": (0.45, {"pharmacy_and_drug_store"}),
    "nightlife": (0.40, {"bar", "dance_club", "lounge", "music_venue"}),
    "food": (0.35, {"restaurant", "fast_food_restaurant", "casual_eatery", "cafe"}),
}
CATEGORY_OF = {c: name for name, (_, cats) in CATEGORIES.items() for c in cats}
# Stations and airports come from OpenStreetMap only: Overture places of those kinds in Accra are mostly offices and
# agents named after them (a "Tamale Airport" pinned in Osu).
BASE_ONLY = {"bus_station", "railway_station", "international_airport", "airport", "ferry_terminal"}

# Overture places below this confidence are left out (its own score that the place exists as named), and below
# MIN_CONFIDENCE_SAFETY for the kinds a driver may look for in an emergency, which are fewer.
MIN_CONFIDENCE, MIN_CONFIDENCE_SAFETY = 0.65, 0.55
SAFETY = {"hospital", "police", "fuel"}
# Two places of the same category closer than this (m) whose names share most of their words are one place; a land
# use area and a place of the same name merge within MERGE_AREA_M.
MERGE_M, MERGE_AREA_M = 80.0, 300.0
# Names longer than this (characters) are cut at a separator or dropped; at most MAX_PLACES per area, best first.
MAX_NAME, MAX_PLACES = 34, 600
CLOSED = {"closed", "permanently_closed", "temporarily_closed"}
# Names that are no place a driver would look for: a person's practice (no real private people in the game), or an
# address.
PERSONAL = re.compile(r"^(dr|doctor|prof|rev|pastor|apostle|bishop|mr|mrs|ms|madam|hon)\.?\s", re.I)
CUT = re.compile(r"\s+(?:[-|–—(/]|\|\|\|).*$")
WORD = re.compile(r"[a-z0-9]+")
NON_LATIN = re.compile(r"[\u0370-\u1fff\u2e80-\uffff]")


def clean_name(name):
    """The name as the maps show it: spaces collapsed, a tail after a dash, bar or bracket cut when the name is long,
    an all-capitals name in title case (capitals read as shouting on a small map). None when nothing fit to show
    is left."""
    if not name:
        return None
    n = " ".join(str(name).split()).strip(" ,.;:-")
    # A name in two scripts keeps its Latin one (the in-game text is English, and the HUD's type may lack the other).
    if re.search(r"[A-Za-z]", n) and NON_LATIN.search(n):
        n = " ".join(w for w in n.split() if not NON_LATIN.search(w)).strip(" ,.;:-")
    if PERSONAL.match(n):
        return None
    if len(n) > MAX_NAME:
        n = CUT.sub("", n).strip(" ,.;:-")
    if "," in n and len(n) > MAX_NAME:
        n = n.split(",")[0].strip()
    if len(n) > MAX_NAME or len(n) < 3 or not re.search(r"[A-Za-z]", n):
        return None
    if n.isupper() and len(n) > 4:
        n = " ".join(w if len(w) <= 3 and w.isalpha() and w not in {"THE", "AND", "OF"} else w.capitalize()
                     for w in n.split())
    return n


def words(name):
    return set(WORD.findall(name.lower())) - {"the", "of", "and", "accra", "ghana", "ltd", "limited", "plc", "gh"}


def same_name(a, b):
    """Whether two names name the same place: one holds the other, or they share most of their words."""
    la, lb = a.lower(), b.lower()
    if la in lb or lb in la:
        return True
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6


# About the best score() gives: a hospital of 20 ha or more, named on OpenStreetMap and pinned in Overture.
TOP_SCORE = 1.6


def score(row, category):
    """How much the place matters, 0 to about 1.6: its category's weight, scaled by the confidence, plus what is known
    of it (a website, socials, phones, a brand, a source besides Meta's) for an Overture place, or its area for an
    OpenStreetMap land use (37 Military Hospital's 20 ha outrank a clinic)."""
    weight = CATEGORIES[category][0]
    s = weight * (0.45 + 0.55 * float(row.get("confidence") or 0))
    if row["source"] == "places":
        s += 0.05 * bool(row.get("websites")) + 0.05 * bool(row.get("socials")) + 0.03 * bool(row.get("phones"))
        s += 0.05 * bool(row.get("brand"))
        s += 0.05 * any(d not in ("meta", "Overture") for d in (row.get("datasets") or []))
    else:
        area = float(row.get("area_m2") or 0)
        s += 0.15 + min(0.45, 0.15 * math.log10(1 + area / 2000))
    return s


def select_places(rows, inside, to_local):
    """The places of an area, best first: dicts with name, cat (CATEGORIES), x and z (metres from the area's origin,
    z north), rank (score() over TOP_SCORE, 0 to 1, the same scale in every area, so the game can merge areas) and brand (true when Overture names a brand: brands are cleared
    before a public release, circle-bible DECISIONS.md, 14 Nov 2026).

    rows: dicts as places.parquet holds them, with lon and lat in place of the geometry. inside(x, z): whether a point
    (local metres) belongs to the area. to_local(lon, lat): local metres (x east, z north)."""
    cands = []
    for r in rows:
        cat = CATEGORY_OF.get(r.get("category") or "")
        if not cat or (r.get("status") or "").lower() in CLOSED:
            continue
        if r["source"] == "places" and r.get("category") in BASE_ONLY:
            continue
        if r["source"] == "places":
            floor = MIN_CONFIDENCE_SAFETY if cat in SAFETY else MIN_CONFIDENCE
            if float(r.get("confidence") or 0) < floor:
                continue
        name = clean_name(r.get("name"))
        if not name:
            continue
        x, z = to_local(r["lon"], r["lat"])
        if not inside(x, z):
            continue
        cands.append({"name": name, "cat": cat, "x": x, "z": z, "score": score(r, cat), "brand": bool(r.get("brand")),
                      "area": r["source"] == "land_use"})
    # Best first; a place merges into a better one of the same category nearby with the same name, or into a land
    # use area of the same name (the area's point wins: it stands inside the site, not at a pin on its gate).
    cands.sort(key=lambda c: (not c["area"], -c["score"]))
    kept = []
    for c in cands:
        twin = None
        for k in kept:
            reach = MERGE_AREA_M if k["area"] else MERGE_M
            if (k["cat"] == c["cat"] or k["area"]) and math.hypot(k["x"] - c["x"], k["z"] - c["z"]) <= reach \
                    and same_name(k["name"], c["name"]):
                twin = k
                break
        if twin:
            # What is known of the twin adds to the place: an area named on the map and pinned in Overture is better
            # known than either alone.
            twin["score"] = max(twin["score"], c["score"]) + 0.05
            twin["brand"] = twin["brand"] or c["brand"]
            continue
        kept.append(c)
    kept.sort(key=lambda c: -c["score"])
    return [{"name": c["name"], "cat": c["cat"], "x": round(c["x"], 1), "z": round(c["z"], 1),
             "rank": round(min(1.0, c["score"] / TOP_SCORE), 3), **({"brand": True} if c["brand"] else {})}
            for c in kept[:MAX_PLACES]]


def load_places(con, path):
    """places.parquet's rows as dicts, with lon and lat (the points' WKB read by shapely: no DuckDB extension)."""
    cols = ["source", "name", "category", "confidence", "status", "brand", "websites", "socials", "phones", "datasets",
            "area_m2"]
    import shapely

    rows = con.execute(f"SELECT {', '.join(cols)}, geometry::BLOB FROM '{path}'").fetchall()
    out = []
    for r in rows:
        p = shapely.from_wkb(bytes(r[-1]))
        out.append(dict(zip(cols + ["lon", "lat"], (*r[:-1], p.x, p.y))))
    return out
