"""Checks the choice of the named places for the game's maps (scripts/places.py select_places) on synthetic rows.

    python3 tests/test_places.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import places as pl  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


def place(name, category, x, z, confidence=0.9, source="places", **more):
    # Local metres stand in for lon and lat: to_local below is the identity.
    return {"source": source, "name": name, "category": category, "confidence": confidence, "lon": x, "lat": z, **more}


rows = [
    # A hospital's OpenStreetMap site (20 ha) and its Overture pin at the gate, 150 m away, a little differently named.
    place("37 Military Hospital", "hospital", 0, 0, 1.0, "land_use", area_m2=200000),
    place("37 Military Hospital Accra", "hospital", 150, 0, 0.8, websites=1),
    # A small clinic, well known online.
    place("Rabito Clinic", "hospital", 400, 0, 0.95, websites=1, socials=1, phones=1),
    # The same bank pinned twice 30 m apart, and another bank's branch 30 m away.
    place("Ecobank Osu", "bank_or_credit_union", 600, 0, 0.9, brand="Ecobank"),
    place("Ecobank", "bank_or_credit_union", 630, 0, 0.7),
    place("Access Bank Osu", "bank_or_credit_union", 600, 30, 0.9),
    # Left out: unsure, closed, outside the area, a kind the maps do not show, a doctor by name, an Overture
    # "airport" that is an agent's office.
    place("Maybe Chop Bar", "restaurant", 800, 0, 0.3),
    place("Old Fuel", "gas_station", 820, 0, 0.9, status="permanently_closed"),
    place("Far Hotel", "hotel", 5000, 0, 0.99),
    place("Kofi's Tailoring", "fashion_and_apparel_store", 840, 0, 0.99),
    place("Dr. Mensah", "hospital", 860, 0, 0.99),
    place("Tamale Airport", "airport", 880, 0, 0.99),
    # Filed under a category its name contradicts.
    place("KFC 37 Liberation", "hospital", 1200, 0, 0.99),
    place("Akofena Travel Consult", "museum", 1250, 0, 0.99),
    # Kept: a fuel station at the lower floor for safety kinds, a trotro station from OpenStreetMap.
    place("TotalEnergies Station", "gas_station", 900, 0, 0.6, brand="TotalEnergies"),
    place("La Main Lorry Station", "bus_station", 950, 0, 1.0, "infrastructure"),
    place("中加商务酒店 G.C Royal Hotel", "hotel", 1000, 0, 0.9),
]
out = pl.select_places(rows, lambda x, z: abs(x) < 2000 and abs(z) < 2000, lambda lon, lat: (lon, lat))
names = [p["name"] for p in out]
by = {p["name"]: p for p in out}

check("the site and its pin are one place, at the site's point",
      names.count("37 Military Hospital") == 1 and "37 Military Hospital Accra" not in names
      and by["37 Military Hospital"]["x"] == 0, str(names))
check("the big hospital ranks first", out[0]["name"] == "37 Military Hospital" and out[0]["rank"] > 0.9, out[0]["name"])
check("the hospital outranks a bank, the bank a hotel",
      by["Rabito Clinic"]["rank"] > by["Ecobank Osu"]["rank"] > by["G.C Royal Hotel"]["rank"])
check("a bank pinned twice shows once; another bank beside it stays",
      names.count("Ecobank Osu") == 1 and "Ecobank" not in names and "Access Bank Osu" in names, str(names))
check("unsure, closed, outside, unshown kinds, people, false airports and contradicted categories are left out",
      not {"Maybe Chop Bar", "Old Fuel", "Far Hotel", "Kofi's Tailoring", "Dr. Mensah", "Tamale Airport", "KFC 37 Liberation",
       "Akofena Travel Consult"} & set(names),
      str(names))
check("a fuel station passes at the safety floor", "TotalEnergies Station" in names)
check("stations come from OpenStreetMap", by.get("La Main Lorry Station", {}).get("cat") == "transit")
check("brands are flagged for clearance", by["Ecobank Osu"].get("brand") is True and "brand" not in by["Rabito Clinic"])
check("a name in two scripts keeps its Latin one", "G.C Royal Hotel" in names, str(names))
check("best first", [p["rank"] for p in out] == sorted((p["rank"] for p in out), reverse=True))
check("every place has a category the game draws", all(p["cat"] in pl.CATEGORIES for p in out))

# Names.
check("capitals read in title case", pl.clean_name("JAMES TOWN POLICE STATION") == "James Town Police Station",
      str(pl.clean_name("JAMES TOWN POLICE STATION")))
check("a long name is cut at its separator",
      pl.clean_name("French Embassy in Ghana / Ambassade de France au Ghana") == "French Embassy in Ghana")
check("a name with nothing to read is dropped", pl.clean_name("  --  ") is None and pl.clean_name("12") is None)

print(f"\n{len(out)} places kept of {len(rows)}; {failures} failure(s)")
sys.exit(1 if failures else 0)
