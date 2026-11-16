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
import os
import re

# The game's icon categories (circle-game src/ui/minimap-places.ts draws one icon each), in the order of their weight:
# how much a place of the kind matters to a driver finding their way in Accra. Each lists the Overture places'
# categories (basic_category, else taxonomy.primary) and the base theme's land use and infrastructure classes it takes.
CATEGORIES = {
    "hospital": (1.00, {"hospital", "emergency_department", "emergency_or_urgent_care_facility", "clinic", "health_care",
                        "dental_clinic", "diagnostics_imaging_or_lab_service", "medical_service"}),
    "landmark": (0.95, {"monument", "museum", "stadium_arena", "stadium", "lighthouse", "fort", "castle", "public_plaza",
                        "plaza", "park", "beach", "golf_course", "zoo", "amusement_park", "cultural_center",
                        "historic_site", "art_gallery"}),
    "transit": (0.90, {"bus_station", "railway_station", "international_airport", "airport", "ferry_terminal"}),
    "police": (0.85, {"police_station"}),
    "fuel": (0.80, {"gas_station"}),
    "market": (0.75, {"market", "farmers_market", "shopping_mall", "department_store", "marketplace", "retail"}),
    "bank": (0.70, {"bank_or_credit_union", "financial_service"}),
    "university": (0.70, {"college_university", "university", "college", "military_school"}),
    "embassy": (0.60, {"embassy"}),
    "hotel": (0.55, {"hotel", "resort", "beach_resort", "lodging", "private_lodging"}),
    "school": (0.50, {"high_school", "elementary_school", "middle_school", "school", "place_of_learning", "education",
                      "specialty_school", "preschool", "educational_service"}),
    "worship": (0.50, {"christian_place_of_worship", "muslim_place_of_worship", "place_of_worship",
                       "hindu_place_of_worship", "religious_organization"}),
    "pharmacy": (0.45, {"pharmacy_and_drug_store"}),
    "nightlife": (0.40, {"bar", "dance_club", "lounge", "music_venue"}),
    "food": (0.35, {"restaurant", "fast_food_restaurant", "casual_eatery", "cafe", "coffee_shop", "food_and_drink", "food_service"}),
    # Every other kind of place OpenStreetMap maps (osm_category()), and Overture's garages: the maps show them all.
    "garage": (0.65, {"automotive_service", "automotive_repair", "auto_body_shop", "vehicle_service",
                      "tire_dealer_and_repair", "vehicle_parts_store"}),
    # Overture's broad kinds (its basic categories since 2025), so its shops and services in Accra show too.
    "service": (0.40, {"professional_service", "real_estate_service", "personal_or_beauty_service", "event_or_party_service",
                       "home_service", "wellness_service", "technical_service", "media_service", "travel_service",
                       "printing_service", "design_service", "shipping_or_delivery_service", "rental_service",
                       "laundry_service", "animal_or_pet_service", "building_or_construction_service", "attorney_or_law_firm",
                       "government_office", "civic_organization", "social_or_community_service", "community_and_government",
                       "agricultural_service", "travel_and_transportation", "post_office"}),
    "leisure": (0.35, {"gym", "sport_or_fitness_facility", "arts_and_entertainment", "recreational_facility"}),
    "shop": (0.30, {"fashion_and_apparel_store", "shopping", "hardware_home_and_garden_store", "electronics_store",
                    "food_and_beverage_store", "personal_care_and_beauty_store", "warehouse_club_store", "convenience_store",
                    "flowers_and_gifts_store", "arts_crafts_and_hobby_store", "specialty_store", "books_music_and_video_store",
                    "auto_dealer", "grocery_store", "supermarket"}),
    "office": (0.25, {"corporate_or_business_office", "b2b_service", "b2b_transportation_and_storage_service",
                      "b2b_energy_and_utility_service", "b2b_office_and_professional_service", "b2b_industrial_and_machine_service",
                      "manufacturer", "supplier_or_distributor", "research_institute"}),
}
CATEGORY_OF = {c: name for name, (_, cats) in CATEGORIES.items() for c in cats}
# Stations and airports come from OpenStreetMap only: Overture places of those kinds in Accra are mostly offices and
# agents named after them (a "Tamale Airport" pinned in Osu).
BASE_ONLY = {"bus_station", "railway_station", "international_airport", "airport", "ferry_terminal"}

# Overture places below this confidence are left out (its own score that the place exists as named), and below
# MIN_CONFIDENCE_SAFETY for the kinds a driver may look for in an emergency, which are fewer.
# MIN_CONFIDENCE_BROAD is for the everyday kinds (shops, services, offices, food, bars, leisure): Overture's Meta-only
# pins in Accra mostly score 0.5 to 0.65, and a stale one there costs a small icon at close zoom, not a wrong turn.
MIN_CONFIDENCE, MIN_CONFIDENCE_SAFETY, MIN_CONFIDENCE_BROAD = 0.65, 0.55, 0.50
SAFETY = {"hospital", "police", "fuel"}
BROAD = {"shop", "service", "office", "food", "nightlife", "leisure"}
# Two places of the same category closer than this (m) whose names share most of their words are one place; a land
# use area and a place of the same name merge within MERGE_AREA_M.
MERGE_M, MERGE_AREA_M = 80.0, 300.0
# Names longer than this (characters) are cut at a separator or dropped; at most MAX_PLACES per area, best first.
MAX_NAME = 34
# No cap on the places an area shows (Idriss, 16 Nov 2026: every place that exists in Accra on the maps): the game
# picks what fits at each zoom. MAX_PLACES stays for a test or a build that wants one.
MAX_PLACES = None
CLOSED = {"closed", "permanently_closed", "temporarily_closed"}
# The launch switch (circle-bible DECISIONS.md, 16 Nov 2026): the proof of concept shows every real name, those that
# name a private person included, each marked "person"; a public build (CHALE_PUBLIC=1) leaves those out, as the game
# did before, so the fallback before launch is one variable.
PUBLIC = os.environ.get("CHALE_PUBLIC") == "1"
# Names that name a private person: a title before a name ("Dr Mensah", "Auntie Ama Spraying"), or an address.
PERSONAL = re.compile(r"^(dr|doctor|prof|rev|pastor|apostle|bishop|mr|mrs|ms|madam|hon|master|auntie|aunty|uncle|papa|"
                      r"paa|maame|sister|brother|bro|bra|nii|naa|nana|opanin|egya|alhaji|hajia|osofo|agya|togbe|lady|oga|daddy|mummy|mama)\.?\s", re.I)
# A shop named after its owner ("Kofi's Fitting Shop").
POSSESSIVE = re.compile(r"^[A-Z][a-z]+(?:\s[A-Z][a-z]+)?['’]s\s")
# An Overture place whose category its name contradicts is left out: a "hospital" with no word of care in its name (a
# "KFC 37 Liberation" filed as a hospital), a "landmark" named like a business (a "travel consult").
CARE = re.compile(r"hospital|clinic|medic|health|maternity|polyclinic|dental|surgery|infirmary|emergency|diagnostic|"
                  r"specialist|eye|heart|cancer|children", re.I)
BUSINESS = re.compile(r"\b(consult|consulting|ltd|limited|enterprise|enterprises|services?|company|agency|travels?|"
                      r"tours?|ventures|solutions)\b", re.I)
CUT = re.compile(r"\s+(?:[-|–—(/]|\|\|\|).*$")
WORD = re.compile(r"[a-z0-9]+")
NON_LATIN = re.compile(r"[\u0370-\u1fff\u2e80-\uffff]")


# OpenStreetMap's tags to the icon categories: (key, value) first, then the key alone. None leaves the feature out
# (parking lots, ATMs and the like are not places a driver looks for by name).
OSM_BY_VALUE = {
    ("amenity", "hospital"): "hospital", ("amenity", "clinic"): "hospital", ("amenity", "doctors"): "hospital",
    ("amenity", "dentist"): "hospital", ("healthcare", "*"): "hospital", ("emergency", "ambulance_station"): "hospital",
    ("amenity", "police"): "police", ("amenity", "fire_station"): "police", ("office", "diplomatic"): "embassy",
    ("amenity", "embassy"): "embassy", ("amenity", "fuel"): "fuel", ("amenity", "charging_station"): "fuel",
    ("amenity", "bank"): "bank", ("amenity", "bureau_de_change"): "bank", ("amenity", "mobile_money_agent"): "bank",
    ("amenity", "marketplace"): "market", ("shop", "mall"): "market", ("shop", "supermarket"): "market",
    ("shop", "department_store"): "market", ("amenity", "university"): "university", ("amenity", "college"): "university",
    ("amenity", "school"): "school", ("amenity", "kindergarten"): "school", ("amenity", "place_of_worship"): "worship",
    ("amenity", "pharmacy"): "pharmacy", ("shop", "chemist"): "pharmacy", ("amenity", "bar"): "nightlife",
    ("amenity", "pub"): "nightlife", ("amenity", "nightclub"): "nightlife", ("amenity", "restaurant"): "food",
    ("amenity", "fast_food"): "food", ("amenity", "cafe"): "food", ("amenity", "food_court"): "food",
    ("amenity", "ice_cream"): "food", ("amenity", "bus_station"): "transit", ("amenity", "taxi"): "transit",
    ("amenity", "ferry_terminal"): "transit", ("public_transport", "station"): "transit",
    ("tourism", "hotel"): "hotel", ("tourism", "guest_house"): "hotel", ("tourism", "hostel"): "hotel",
    ("tourism", "motel"): "hotel", ("tourism", "apartment"): "hotel", ("tourism", "museum"): "landmark",
    ("tourism", "attraction"): "landmark", ("tourism", "gallery"): "landmark", ("tourism", "zoo"): "landmark",
    ("tourism", "theme_park"): "landmark", ("historic", "*"): "landmark", ("man_made", "lighthouse"): "landmark",
    ("man_made", "monument"): "landmark", ("leisure", "park"): "landmark", ("leisure", "stadium"): "landmark",
    ("leisure", "golf_course"): "landmark", ("amenity", "arts_centre"): "landmark", ("amenity", "theatre"): "landmark",
    ("amenity", "cinema"): "nightlife", ("amenity", "casino"): "nightlife",
    ("shop", "car_repair"): "garage", ("craft", "mechanic"): "garage", ("shop", "tyres"): "garage",
    ("shop", "car_parts"): "garage", ("shop", "car"): "garage", ("amenity", "car_wash"): "garage",
    ("amenity", "car_rental"): "garage", ("shop", "motorcycle"): "garage", ("amenity", "vehicle_inspection"): "garage",
    ("amenity", "parking"): None, ("amenity", "atm"): None, ("amenity", "parking_entrance"): None,
    ("leisure", "swimming_pool"): None, ("leisure", "pitch"): None, ("leisure", "playground"): None,
    ("leisure", "garden"): None, ("man_made", "tower"): None, ("man_made", "water_tower"): None,
}
OSM_BY_KEY = {"amenity": "service", "shop": "shop", "craft": "shop", "office": "office", "leisure": "leisure",
              "sport": "leisure", "club": "leisure", "tourism": "landmark", "healthcare": "hospital",
              "historic": "landmark", "public_transport": "transit", "emergency": "hospital"}
# The OpenStreetMap features that are garages for the game's fitters (build_district.py "garages"), by kind.
GARAGE_KIND = {("shop", "car_repair"): "repair", ("craft", "mechanic"): "repair", ("shop", "tyres"): "tyres",
               ("shop", "car_parts"): "parts"}
# Overture's categories that are garages, by kind.
GARAGE_KIND_OVERTURE = {"automotive_service": "repair", "automotive_repair": "repair", "auto_body_shop": "repair",
                        "vehicle_service": "repair", "tire_dealer_and_repair": "tyres", "vehicle_parts_store": "parts"}


def osm_category(key, value):
    """The icon category of an OpenStreetMap feature by its main tag (osm_extract.py key and value), or None."""
    for k in ((key, value), (key, "*")):
        if k in OSM_BY_VALUE:
            return OSM_BY_VALUE[k]
    return OSM_BY_KEY.get(key)


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


def is_person(name):
    """Whether a name (as clean_name() gives it) names a private person: a title before a name, or a first name's
    possessive ("Kofi's Tailoring"). Shown in the proof of concept, marked "person"; left out of a public build."""
    return bool(name) and bool(PERSONAL.match(name) or POSSESSIVE.match(name))


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
    if row["source"] == "osm":
        # Mapped by hand on OpenStreetMap: as sure as a confident Overture place, more so with an outline or a brand.
        area = float(row.get("area_m2") or 0)
        return weight * 0.9 + 0.1 + 0.05 * bool(row.get("brand")) + (min(0.45, 0.15 * math.log10(1 + area / 2000)) if area else 0)
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
        cat = r["cat"] if r["source"] == "osm" else CATEGORY_OF.get(r.get("category") or "")
        if not cat or (r.get("status") or "").lower() in CLOSED:
            continue
        if r["source"] == "osm" and not r.get("name"):
            continue
        if r["source"] == "places" and r.get("category") in BASE_ONLY:
            continue
        if r["source"] == "places":
            floor = MIN_CONFIDENCE_SAFETY if cat in SAFETY else MIN_CONFIDENCE_BROAD if cat in BROAD else MIN_CONFIDENCE
            if float(r.get("confidence") or 0) < floor:
                continue
        name = clean_name(r.get("name"))
        if not name:
            continue
        # A name that names a private person ("Kofi's Tailoring", "Dr Mensah"), unless it is a brand (Domino's Pizza,
        # cleared before a release): shown and marked in the proof of concept, left out of a public build.
        person = is_person(name) and not r.get("brand")
        if person and PUBLIC:
            continue
        if r["source"] == "places" and ((cat == "hospital" and not CARE.search(name))
                                        or (cat in ("landmark", "hotel", "market") and BUSINESS.search(name))):
            continue
        x, z = to_local(r["lon"], r["lat"])
        if not inside(x, z):
            continue
        cands.append({"name": name, "cat": cat, "x": x, "z": z, "score": score(r, cat), "brand": bool(r.get("brand")), "person": person,
                      "area": r["source"] == "land_use" or (r["source"] == "osm" and float(r.get("area_m2") or 0) > 2000)})
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
             "rank": round(min(1.0, c["score"] / TOP_SCORE), 3), **({"brand": True} if c["brand"] else {}),
             **({"person": True} if c["person"] else {})}
            for c in (kept if MAX_PLACES is None else kept[:MAX_PLACES])]


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


def load_osm(path):
    """osm.parquet's rows (osm_extract.py) as select_places() rows: source "osm", the icon category as "cat" (None
    left out), and the garage kind as "garage" where the feature is one."""
    import pyarrow.parquet as pq

    out = []
    for r in pq.read_table(path, columns=["id", "name", "key", "value", "brand", "lon", "lat", "area_m2", "outline"]).to_pylist():
        cat = osm_category(r["key"], r["value"])
        if cat is None:
            continue
        out.append({"source": "osm", "cat": cat, "id": r["id"], "name": r["name"], "brand": r["brand"], "lon": r["lon"],
                    "lat": r["lat"], "area_m2": r["area_m2"], "outline": r["outline"],
                    "garage": GARAGE_KIND.get((r["key"], r["value"]))})
    return out


def garage_name(name):
    """A garage's name as the game shows it, or "" when it has none fit to show. A name that names a private person (a
    title, a first name's possessive) is kept in the proof of concept and dropped in a public build (CHALE_PUBLIC=1),
    where the game names the garage generically (circle-bible DECISIONS.md, 16 Nov 2026)."""
    n = clean_name(name)
    if not n or (PUBLIC and is_person(n)):
        return ""
    return n
