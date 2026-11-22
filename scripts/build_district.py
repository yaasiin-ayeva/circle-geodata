"""Build a drivable district from an Overture export: buildings, roads, streetlights and a benchmark lap.

    python3 scripts/build_district.py --slice data/work/slice --out data/work/osu-poc

Writes one shared asset set for the Unity and Unreal proof of concept:

  osu-poc.obj, osu-poc.mtl  meshes in metres, chunked by 250 m cells. OBJ axes are right-handed, Y up:
                            X = west, Y = up, Z = north. Unity's OBJ import flips X, so in Unity
                            x = east, y = up, z = north. Unreal's importer converts to its Z-up frame.
  osu-poc.json              origin, bounds, streetlights, the benchmark lap (flat [x0, z0, x1, z1, ...])
                            and the spawn point, in Unity axes (x east, y up, z north), metres from the
                            district origin; "parked" cars along the streets where the district's rules ask
                            for them (DISTRICT_RULES "parking", not Osu).
  osu-poc-preview.png       top-down check: roads, buildings by height, lights, lap.

An area is a box (osu-poc, jamestown) or a corridor along named roads less the boxes of its neighbours (ring-road,
--name ring-road): see AREAS and area_outline(). A corridor lays its ground per 250 m cell, and its lap is a route
through given points (route_lap()) rather than a loop round a block.

Heights come from OSM tags where they exist, otherwise from the typology rules in storeys(); they are
placeholders until the Open Buildings 2.5D heights are merged (pipeline step 3). Each footprint is then built from
the modules in massing.py (hipped roofs, bulkheads, set-backs, frame storeys, compound walls); --no-massing keeps
plain extruded boxes. Needs shapely, pyproj,
mapbox_earcut, networkx and Pillow (requirements-district.txt).

The landmarks circle-game builds itself (landmark_footprints.py LANDMARKS: Black Star Square, the stadium, Jubilee
House, Accra Mall...) are left out of every district: no massing and no solid outline on their footprints, which still
keep compound walls, parked cars and hiding places off their ground. --keep-landmarks builds them as before.
"""
import argparse
import csv
import hashlib
from collections import Counter
import json
import math
from collections import defaultdict
import os
import sys

import duckdb
import mapbox_earcut as earcut
import networkx as nx
import numpy as np
import shapely
from PIL import Image, ImageDraw
from pyproj import Transformer

import ev_chargers
import fuel_stations
import imagery
import places as named_places
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import substring

import landmark_footprints
import avenue_kit
import massing
import typology

WORLD_ORIGIN_UTM = (808523.0, 616356.0)  # Kwame Nkrumah Interchange ("Circle"), see README

JAMESTOWN_ORIGIN = (5.5395, -0.2080)          # High Street by Ussher Fort, where the lap starts
JAMESTOWN_LAP = ("High Street", 5.5335)          # set from the slice's road names
OSU_ORIGIN = (5.5669, -0.1809)                   # Danquah Circle

# The vertical slice's box (aoi/slices.geojson "vertical-slice"): the Osu slice's buildings stop at its west edge.
VERTICAL_SLICE = {"south": 5.542, "north": 5.575, "west": -0.1945, "east": -0.137}
# The Ring Road through the vertical slice, by the names Overture gives its segments: from the slice's west edge on
# Ring Road Central (2.1 km short of Kwame Nkrumah Circle) through the Ako Adjei Interchange, Ring Road East and
# Danquah Circle, then Ring Road East to its end at La. Both carriageways are mapped, each one way along its geometry.
RING_ROADS = ("Ring Road Central", "Ring Road East", "Danquah Circle", "Ako Adjei Interchange")
# Roads mapped one way per carriageway, driven along their geometry: OpenStreetMap tags Ring Road East and Danquah
# Circle oneway=yes (circle-reference street-survey.md), and the slice has each carriageway of the Ring Road as its own
# segment, the two running opposite ways, westbound on the north side as Ghana drives on the right. Overture's one-way
# rules are not in the slice, so the names stand in for them.
ONE_WAY_ROADS = RING_ROADS
# The Ring Road lap (route_lap): points the route passes in order, each on one carriageway, at a vertex of its segment
# (lat, lon). From the westbound carriageway leaving Danquah Circle, west to the slice's edge, back east through the
# circle, on to La and back to the circle. The route between them follows the carriageways' directions.
RING_LAP = {"route": [(5.567666, -0.182117),     # westbound, leaving Danquah Circle
                      (5.573174, -0.192172),     # westbound, Ring Road Central past the Ako Adjei Interchange
                      (5.573673, -0.193304),     # eastbound, after turning at the west end
                      (5.564333, -0.176351),     # eastbound, Ring Road East past Danquah Circle
                      (5.560141, -0.169233),     # eastbound, before La
                      (5.556986, -0.167259),     # westbound, after turning at La
                      (5.566147, -0.179392)],    # westbound, before Danquah Circle
            "one_way": ONE_WAY_ROADS}

# North of the vertical slice (aoi/slices.geojson "north-37"; slice in data/work/slice-north): Independence Avenue from
# the Ako Adjei Interchange to 37 Circle, then Liberation Road past the 37 Military Hospital and 37 Station towards the
# airport, by the names Overture gives them. Its box starts 400 m inside the vertical slice, at the Ako Adjei
# Interchange, and leaves out the Ring Road's corridor ("minus"), so the two meet along the edge of the Ring Road's
# buffer without a gap or an overlap.
LIBERATION = {"south": 5.5715, "north": 5.6065, "west": -0.2000, "east": -0.1650}
LIBERATION_ROADS = ("Independence Avenue", "37 Circle", "Liberation Road")
# Its lap: up the avenue and Liberation Road to the airport end and back (lat, lon, each near a vertex of the road).
LIBERATION_LAP = {"route": [(5.5739635, -0.1892662),  # Independence Avenue, its northbound (east) carriageway
                            (5.5891, -0.1818),           # 37 Circle
                            (5.6045120, -0.1778479),     # Liberation Road northbound, before the crossover
                            (5.6006832, -0.1788379)]}    # Liberation Road southbound, back towards 37

# East of 37 (aoi/slices.geojson "east-accra"; slice in data/work/slice-east). Two corridors that meet Liberation Road's
# and each other where their buffers meet ("minus"), both in Osu's frame so the game streams them with the rest:
#   legon: Liberation Road on to the Tetteh Quarshie Interchange, J.J. Rawlings Avenue (the N4) past Legon, Atomic
#   Junction and Madina to Adenta Barrier and the first stretch of the Aburi Road, Boundary Road and La Bawaleshi Road
#   through East Legon, and the road from Madina to Ashaley Botwe; with East Legon's grid (American House, Lagos
#   Avenue, A&C) and Adenta's SSNIT flats as whole districts ("boxes"). The N4's unnamed carriageway pieces come with
#   its class ("classes").
#   tema: the Kwame Nkrumah (Tema) Motorway from the interchange to its roundabout at Tema, every motorway piece and
#   slip road in the box (the exits to Spintex, Ashaiman and the Aflao Road), Harbour Road down to Community 1 and the
#   start of the Aflao Road, with Community 1's centre as a whole district.
LEGON = {"south": 5.6000, "north": 5.7160, "west": -0.1920, "east": -0.1250}
LEGON_ROADS = ("Liberation Road", "Tetteh Quarshie Interchange", "J.J. Rawlings Avenue", "Aburi Road", "Atomic Junction",
               "Boundary Road", "La Bawaleshi Road", "Ashale Botwe Nmai Dzorn Road", "Adenta Dodowa Road")
EAST_LEGON = {"south": 5.6290, "north": 5.6540, "west": -0.1720, "east": -0.1460}
ADENTA_FLATS = {"south": 5.6980, "north": 5.7140, "west": -0.1700, "east": -0.1500}
# Its lap: up the N4 from the interchange to Adenta and back down (lat, lon on the carriageway driven: the east one
# northbound, the west one southbound).
LEGON_LAP = {"route": [(5.6320, -0.17682), (5.6680, -0.17667), (5.7030, -0.17145), (5.6680, -0.17709), (5.6320, -0.17702)]}
TEMA = {"south": 5.6120, "north": 5.7000, "west": -0.1850, "east": 0.0120}
TEMA_ROADS = ("Kwame Nkrumah Motorway", "Tetteh Quarshie Interchange", "Harbour Road", "Valco Roundabout", "Aflao Road")
TEMA_C1 = {"south": 5.6380, "north": 5.6560, "west": -0.0080, "east": 0.0080}
# Its lap: the motorway from the interchange to Tema and back (the south carriageway eastbound, the north one back).
TEMA_LAP = {"route": [(5.6300, -0.15467), (5.6650, -0.06476), (5.6800, -0.03115), (5.6650, -0.06530), (5.6300, -0.15528)]}

# North of Adenta (aoi/slices.geojson "aburi"; slice in data/work/slice-aburi): the Aburi Road (the N4, a single
# carriageway past Adenta) through Oyarifa, Teiman, Ayi Mensah and Kweiman, up the Peduase climb of the Akuapem ridge
# to Kitase and Aburi, its unnamed trunk pieces with it ("classes"); Aburi's centre and the Botanical Gardens as a whole
# district ("boxes"). It meets the legon corridor at Adenta ("minus"). The game lays it on the real relief (relief.py).
ABURI = {"south": 5.7100, "north": 5.8660, "west": -0.2050, "east": -0.1500}
ABURI_ROADS = ("Aburi Road", "J.J. Rawlings Avenue", "Nana A. Kodua I Avenue", "Nana Kwam Affum Street", "Nana K. Bampoe Street")
ABURI_TOWN = {"south": 5.8380, "north": 5.8560, "west": -0.1860, "east": -0.1680}
# Its lap: from Adenta up the ridge to Aburi and back down the same road ("penalty" 1: a single carriageway has no other
# way back), past Ayi Mensah and the Peduase climb (lat, lon, each near a vertex of the N4).
ABURI_LAP = {"route": [(5.722266, -0.172756), (5.782797, -0.181725), (5.807419, -0.183105), (5.844452, -0.174003),
                       (5.807419, -0.183105), (5.782797, -0.181725)], "penalty": 1.0}

# West of the vertical slice (aoi/slices.geojson "accra-central"; slice in data/work/slice-central), two corridors in
# Osu's frame that meet the Ring Road's and each other where their buffers meet ("minus"):
#   central: the Ring Road Central on to Kwame Nkrumah Circle, Kwame Nkrumah Avenue down past Tudu and Makola to
#   Jamestown, Independence Avenue from the Ako Adjei Interchange down to Black Star Square, and the old town's
#   avenues between them (Liberia, Kojo Thompson, Barnes, Castle Road, High Street to the lighthouse, 28th February
#   Road towards Osu); with Circle, the Makola and Kantamanto markets and Jamestown as whole districts ("boxes"). The
#   standalone Jamestown district (AREAS "jamestown") stays as it is, in its own frame.
#   kaneshie: the Ring Road West from Circle to the Obetsebi Lamptey Interchange, the Winneba Road (Dr. Busia High
#   Street) through Kaneshie First Light to Darkuman Junction, and Graphic Road; Kaneshie's market and station as a
#   whole district.
CENTRAL = {"south": 5.5280, "north": 5.5780, "west": -0.2220, "east": -0.1900}
CENTRAL_ROADS = ("Ring Road Central", "Kwame Nkrumah Circle", "Kwame Nkrumah Avenue", "Independence Avenue", "Liberia Avenue",
                 "Kojo Thompson Avenue", "Barnes Avenue", "Castle Road", "Prof. Atta Mills High Street", "High Street",
                 "Asafoatse Nettey Road", "Nii Kojo Ababio Avenue", "28th February Road", "Starlets '91 Road")
CIRCLE_BOX = {"south": 5.5640, "north": 5.5740, "west": -0.2220, "east": -0.2100}
MAKOLA_BOX = {"south": 5.5430, "north": 5.5520, "west": -0.2150, "east": -0.2030}
JAMESTOWN_BOX = {"south": 5.5300, "north": 5.5425, "west": -0.2175, "east": -0.2055}
# Its lap: from Circle down Kwame Nkrumah Avenue past Makola to Jamestown's High Street, and back up Independence
# Avenue towards the Ako Adjei Interchange (lat, lon, each a vertex of the road).
CENTRAL_LAP = {"route": [(5.559966, -0.213014), (5.549972, -0.210487), (5.538571, -0.208737), (5.555185, -0.200433),
                         (5.561478, -0.196064)]}
KANESHIE = {"south": 5.5530, "north": 5.5850, "west": -0.2580, "east": -0.2120}
KANESHIE_ROADS = ("Ring Road West High Street", "Obetsebi Lamptey Interchange", "Obetsebi Lamptey Circle",
                  "Dr. Busia High Street", "Graphic Road")
KANESHIE_MARKET = {"south": 5.5600, "north": 5.5720, "west": -0.2430, "east": -0.2280}
# Its lap: from the Obetsebi Lamptey Circle up the Winneba Road through Kaneshie to Darkuman Junction and back down
# the same road ("penalty" 1).
KANESHIE_LAP = {"route": [(5.561672, -0.229382), (5.576038, -0.253541)],
                "penalty": 1.0}

# The coast east of Osu (aoi/slices.geojson "coast-east"; slice in data/work/slice-coast): La Road from Osu along the
# sea past La and Labadi Beach, then Teshie Road on to Teshie, the Ring Road East's south end where it meets La Road;
# La Pleasure Beach, La Palm and Labadi's old town as a whole district. A road for night runs by the sea.
LABADI = {"south": 5.5400, "north": 5.5850, "west": -0.1900, "east": -0.0980}
LABADI_ROADS = ("La Road", "Teshie Road", "Lakpanaa Street", "Ring Road East")
LABADI_BEACH = {"south": 5.5540, "north": 5.5670, "west": -0.1500, "east": -0.1340}
# Osu Castle (Christiansborg) on the shore, with Castle Drive, the streets of Osu above it and 28th February Road
# behind (circle-game docs/osu-castle.md), added to Labadi's corridor so that the castle the game builds stands on the
# city's ground with its road side and its sea side. Its grounds (OpenStreetMap way 11827738) are 570 by 350 m; the box takes them
# with the shore below and the streets to 28th February Road. Without it the castle lay 0.5 km outside every tile
# (circle-reference catalogue/accra-places.md, #18). Its west edge is central's east edge (CENTRAL), so that Castle
# Drive, 28th February Road and Nii Owuo Street, cut there, go on in central's tiles: a gap between the two left the
# castle's streets cut off from every route.
OSU_CASTLE_BOX = {"south": 5.5440, "north": 5.5505, "west": -0.1900, "east": -0.1800}
# Its lap: La Road from Osu east to Labadi, Teshie Road to Teshie and back (a single carriageway mostly: "penalty" 1).
LABADI_LAP = {"route": [(5.553498, -0.174993), (5.559395, -0.150092), (5.564775, -0.137170), (5.578105, -0.109933),
                        (5.564775, -0.137170), (5.559395, -0.150092)], "penalty": 1.0}

AREAS = {
    # About 1.3 km2 of Osu: Danquah Circle, the north half of Oxford Street, Ring Road East.
    "osu-poc": {"south": 5.5575, "north": 5.5695, "west": -0.1870, "east": -0.1780,
                "origin": OSU_ORIGIN, "lap": ("Oxford Street", 5.5600), "district": "osu"},
    # Jamestown, Bukom and Ussher Town (aoi/slices.geojson "jamestown"; slice in data/work/slice-jamestown).
    "jamestown": {"south": 5.5300, "north": 5.5425, "west": -0.2175, "east": -0.2055,
                  "origin": JAMESTOWN_ORIGIN, "lap": JAMESTOWN_LAP, "district": "jamestown"},
    # The Ring Road beyond Osu: a corridor 250 m either side of RING_ROADS inside the vertical slice, less osu-poc's
    # box, so the two areas meet without overlapping (slice in data/work/slice). It shares Osu's origin, so the game
    # loads both in one frame; its lap runs through osu-poc, on the roads of the whole corridor.
    "ring-road": {"corridor": {"roads": RING_ROADS, "half_width_m": 250.0, "within": VERTICAL_SLICE},
                  "minus": ["osu-poc"], "origin": OSU_ORIGIN, "lap": RING_LAP, "district": "ring-road"},
    # Liberation Road to 37: a corridor 300 m either side of LIBERATION_ROADS inside LIBERATION (wide enough for the 37
    # Military Hospital's grounds and 37 Station), streamed beside the Ring Road; Osu's origin, so the game loads it in
    # one frame with the other two.
    "liberation": {"corridor": {"roads": LIBERATION_ROADS, "half_width_m": 300.0, "within": LIBERATION},
                   "minus": ["ring-road"], "origin": OSU_ORIGIN, "lap": LIBERATION_LAP, "district": "ring-road"},
    "legon": {"corridor": {"roads": LEGON_ROADS, "classes": ("trunk",), "half_width_m": 300.0, "within": LEGON,
                           "boxes": (EAST_LEGON, ADENTA_FLATS)},
              "minus": ["liberation"], "origin": OSU_ORIGIN, "lap": LEGON_LAP, "district": "ring-road"},
    "tema": {"corridor": {"roads": TEMA_ROADS, "classes": ("motorway",), "half_width_m": 220.0, "within": TEMA,
                          "boxes": (TEMA_C1,)},
             "minus": ["liberation", "legon"], "origin": OSU_ORIGIN, "lap": TEMA_LAP, "district": "ring-road"},
    "aburi": {"corridor": {"roads": ABURI_ROADS, "classes": ("trunk",), "half_width_m": 250.0, "within": ABURI,
                           "boxes": (ABURI_TOWN,)},
              "minus": ["legon"], "origin": OSU_ORIGIN, "lap": ABURI_LAP, "district": "ring-road"},
    "central": {"corridor": {"roads": CENTRAL_ROADS, "half_width_m": 220.0, "within": CENTRAL,
                             "boxes": (CIRCLE_BOX, MAKOLA_BOX, JAMESTOWN_BOX)},
                "minus": ["osu-poc", "ring-road", "liberation"], "origin": OSU_ORIGIN, "lap": CENTRAL_LAP, "district": "central"},
    "kaneshie": {"corridor": {"roads": KANESHIE_ROADS, "half_width_m": 220.0, "within": KANESHIE,
                              "boxes": (KANESHIE_MARKET,)},
                 "minus": ["central"], "origin": OSU_ORIGIN, "lap": KANESHIE_LAP, "district": "ring-road"},
    "labadi": {"corridor": {"roads": LABADI_ROADS, "half_width_m": 220.0, "within": LABADI,
                            "boxes": (LABADI_BEACH, OSU_CASTLE_BOX)},
               "minus": ["osu-poc", "ring-road", "central"], "origin": OSU_ORIGIN, "lap": LABADI_LAP, "district": "ring-road"},
}

# The office districts (typology.py "tower"): footprints of 700 m2 or more with no mapped floors stand as glass towers
# of 6 to 12 storeys, where OpenStreetMap maps the towers' outlines but hardly ever their floors (9 of Airport City's
# 814 footprints carry levels, 15 of Ridge's 2,485). Airport City between the airport's fence and Liberation Road's
# junction with the Airport Bypass, and Ridge from Independence Avenue to the Kanda Highway (lat, lon boxes); the
# airport's terminals (class roof, transportation) and anything named a terminal are left out by typology.classify().
TOWER_ZONES = {"airport-city": {"south": 5.5985, "north": 5.6075, "west": -0.1790, "east": -0.1690},
               "ridge": {"south": 5.5600, "north": 5.5720, "west": -0.2010, "east": -0.1900}}
TOWER_STOREYS = [(6, 0.30), (8, 0.30), (10, 0.25), (12, 0.15)]

# What changes from one district to the next (circle-reference district cards, catalogue/*-identity-survey.md).
#   yards: houses stand in walled compounds with gates (Osu); in the old town buildings stand on the street line.
#   paints: the facade paints, (material, weight); None keeps the even mix of WALLS.
#   landmarks: hand-built pieces, by name, at (lat, lon).
#   storeys, storeys_from_m2: footprints of that area or more without a height draw their storeys from this
#   (storeys, weight) list instead of the typology rules, where footprint size does not predict height.
#   signs: real shop signs at their places, (name, lat, lon, bearing of the facade); the game draws the names.
#   parking: cars parked parallel to the road edge, for districts without a dressed look-dev block (place_parked()):
#   the road classes parked on and the chance a place is taken ("classes", "take"), named streets with their own
#   chance whatever their class ("streets"), how far from the lap ("lap_m"), one place every "step_m" on each side,
#   the car's centre "inset_m" inside the road edge on roads narrower than "narrow_m" and "wide_inset_m" on wider
#   ones, the clearances to junctions, buildings, the spawn and other cars, and the paints as (colour, weight).
#   Osu has none: its parked cars come from build_lookdev.py.
DISTRICT_RULES = {
    "osu": {"yards": True, "paints": None, "landmarks": []},
    # Jamestown: no compound walls; houses painted yellow, ochre, green and pale grey; the lighthouse.
    "jamestown": {"yards": False,
                  "paints": [("M_Wall_Ochre", 0.30), ("M_Wall_Mint", 0.20), ("M_Wall_Grey", 0.20), ("M_Wall_Concrete", 0.20),
                             ("M_Wall_PaleBlue", 0.10)],
                  "landmarks": [("lighthouse", 5.5331, -0.2122)],
                  # Storeys of footprints of 150 m2 or more that carry no mapped floors, drawn whatever their size:
                  # the large footprints along the lap are often a row of one-storey shops or several roofs merged
                  # into one (7 checked in Street View: 1, 1, 2, 2, 3, 4 and about 12 storeys), where the typology
                  # rules gave them 3 to 6 storeys and a flat roof. The draw is the lap count of 82 buildings (44% one
                  # storey, 40% two, 9% three, 7% four or more; circle-reference obs-jamestown-storeys-*). Footprints
                  # with OpenStreetMap levels keep them: 4 of 4 tagged one storey were one storey in Street View.
                  "storeys": [(1, 0.44), (2, 0.40), (3, 0.09), (4, 0.04), (5, 0.03)], "storeys_from_m2": 150,
                  # The shop signs read in full along the lap (circle-reference texts.csv txt-jamestown-lap-001..003),
                  # by name: the Street View point they were read from and the bearing of their facade from it.
                  # About 1 shop bay in 10 carries a sign here: the game hangs these and no others.
                  "signs": [("TWM SPORTS EQUIPMENT STORE", 5.539694, -0.207763, 175),
                            ("MAKOLA PRICES", 5.537146, -0.210161, 138),
                            ("SHIP HOUZZ", 5.538331, -0.208946, 131)],
                  # Parked cars along the streets (circle-reference jamestown-identity-survey.md: side streets about
                  # 6 m of asphalt with no kerb and no sidewalk, kiosks and tables on sandy edges; High Street wider,
                  # two lanes each way, with kerbs; vehicles.md: cars park on sandy verges). Within 250 m of the lap, a
                  # place every 6.5 m on each side, taken about one time in four on the side streets and one in three
                  # on High Street, which is classed primary and parked on by name: estimates, not counted. On roads
                  # under 7 m the car's centre is 1.0 m inside the asphalt edge, its outer side at the sand; 1.1 m on
                  # wider roads. No place within 12 m of a junction, 1.5 m of a building, 6 m of the spawn or 5.5 m of
                  # another car. Paints in the order vehicles.md gives for private cars (dark grey and black first,
                  # then silver, white, red, blue), and a taxi waiting on about 12% of the places: weights estimated.
                  "parking": {"classes": ["residential", "unclassified", "tertiary", "secondary"], "take": 0.25,
                              "streets": {"High Street": 0.35}, "lap_m": 250.0, "step_m": 6.5,
                              "inset_m": 1.0, "wide_inset_m": 1.1, "narrow_m": 7.0,
                              "junction_m": 12.0, "building_m": 1.5, "spawn_m": 6.0, "gap_m": 5.5,
                              "colours": [("grey", 0.20), ("black", 0.20), ("silver", 0.18), ("white", 0.16),
                                          ("red", 0.08), ("blue", 0.06), ("taxi", 0.12)]}},
    # The old centre (AREAS "central"): Jamestown's streets, without its storey draw (Circle's and Makola's large
    # footprints are market halls and offices of several storeys, which the typology rules give) and with cars parked
    # along every side street rather than only near a lap: houses on the street line, Jamestown's paints, the
    # lighthouse and its read shop signs.
    "central": {"yards": False, "paints": None, "landmarks": [("lighthouse", 5.5331, -0.2122)], "signs": [],
                "parking": {"classes": ["residential", "unclassified", "tertiary", "secondary"], "take": 0.25,
                            "streets": {"High Street": 0.35}, "lap_m": None, "step_m": 6.5,
                            "inset_m": 1.0, "wide_inset_m": 1.1, "narrow_m": 7.0,
                            "junction_m": 12.0, "building_m": 1.5, "spawn_m": 6.0, "gap_m": 5.5,
                            "colours": [("grey", 0.20), ("black", 0.20), ("silver", 0.18), ("white", 0.16),
                                        ("red", 0.08), ("blue", 0.06), ("taxi", 0.12)]}},
    # The Ring Road corridor: Osu's building rules (walled yards off the main roads, Osu's roof mix and paints). The
    # street survey describes Ring Road East's frontage as 1-2 storeys behind walls with about 20% shopfronts, which
    # the yards already give; it counted no storeys there, so the typology rules stay. It has no dressed block, so
    # cars park along its side streets (street survey: "on sandy verges and concrete aprons in the residential
    # streets"), never on the Ring Road itself, a primary road with no parking lane. The chance a place is taken
    # (about one in five) is an estimate, not a count; the clearances and paints are Jamestown's.
    "ring-road": {"yards": True, "paints": None, "landmarks": [],
                  "parking": {"classes": ["residential", "unclassified", "tertiary"], "take": 0.2,
                              "streets": {}, "lap_m": None, "step_m": 6.5,
                              "inset_m": 1.0, "wide_inset_m": 1.1, "narrow_m": 7.0,
                              "junction_m": 12.0, "building_m": 1.5, "spawn_m": 6.0, "gap_m": 5.5,
                              "colours": [("grey", 0.20), ("black", 0.20), ("silver", 0.18), ("white", 0.16),
                                          ("red", 0.08), ("blue", 0.06), ("taxi", 0.12)]}},
}
# The old centre shares Jamestown's paints and its read shop signs (they stand in Jamestown, inside its box).
DISTRICT_RULES["central"].update(paints=DISTRICT_RULES["jamestown"]["paints"], signs=DISTRICT_RULES["jamestown"]["signs"])

ROAD_WIDTH = {"motorway": 22.0, "trunk": 18.0, "primary": 13.0, "secondary": 10.0, "tertiary": 8.5,
              "residential": 6.0, "unclassified": 6.0, "living_street": 5.0, "service": 4.5}
# The Ring Road's cross-section (circle-reference catalogue/street-survey.md, Ring Road East at 5.5679,-0.1828):
# 2 + 2 lanes, about 7 m per carriageway, either side of a grass median of about 8-10 m with twin-arm LED poles in it;
# Danquah Circle's ring about 9-10 m wide around its island. The class widths drew each carriageway 13 m wide, which
# covered the median and stood the poles on the asphalt (the user's test drive of 14 Nov 2026). Each carriageway is
# mapped as its own one-way road, so the width is the carriageway's. Ring Road Central and the Ako Adjei Interchange's
# ramps are not surveyed; they take Ring Road East's 7 m.
DUAL_CARRIAGEWAYS = ("Ring Road Central", "Ring Road East", "Ako Adjei Interchange")
CARRIAGEWAY_M = 7.0
# The one the survey measured keeps its 7 m whatever OSM says (OSM agrees: 2 lanes); the others take OSM's lane count
# where it has one (Ring Road Central: 3 on some pieces).
SURVEYED_CARRIAGEWAY = "Ring Road East"
NAMED_WIDTH = {"Danquah Circle": 9.5}


# The carriageways of the area's dual carriageways that no survey names, found in its geometry (find_carriageways()):
# road id -> carriageway width (m). Filled by build() for the area it builds; read by road_width(), the traffic
# network, the lap and the median's poles. Boulevards with a central reservation (Independence Avenue, Liberation
# Road, the Spintex and Airport roads) are mapped in OpenStreetMap as one one-way road per carriageway, each drawn the
# way its traffic runs; drawn at their class's width (18 m for a trunk) two of them covered the median.
CARRIAGEWAY_W = {}
# road id -> the median distance (m) centre to centre to its opposite carriageway, from find_carriageways(): a width
# from the lane count is capped so that a median of at least MEDIAN_MIN_M stays between the two.
CARRIAGEWAY_GAP = {}
MEDIAN_MIN_M = 1.5
DUAL_CLASSES = ("motorway", "trunk", "primary", "secondary")

# Lane counts from OpenStreetMap (aoi/osm-lanes.geojson, scripts/osm_lanes.py): road id -> lanes on the road as drawn
# (each way's own count on a carriageway, the total on a two-way road). Filled by build() with match_lanes(); read by
# road_width(), the lane markings and the traffic network. Overture's segments carry no lane count; OSM tags most of
# Accra's main roads (16 Nov 2026: Liberation Road 3 each way, the Ring Road and Independence Avenue 2). The user's
# night drive on the Madina-Adenta road, 16 Nov 2026: the boulevards read too narrow.
LANES = {}
OSM_LANES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aoi", "osm-lanes.geojson")
# A lane on a main road (m): Ghana's trunk roads are built with 3.65 m lanes, the city's boulevards a little less;
# plus 0.25 m of edge strip on each side.
LANE_M = {"motorway": 3.65, "trunk": 3.5, "primary": 3.5, "secondary": 3.3, "tertiary": 3.1}
EDGE_M = 0.5
# The sandy, laterite verge outside a main road's asphalt (the night drive; circle-reference street-survey.md,
# Danquah Circle: "sandy and laterite verges"), m, on the kerbside only of a carriageway. Oxford Street has its
# pavements in the look-dev block, so none there.
VERGE_M = {"motorway": 3.0, "trunk": 3.0, "primary": 2.5, "secondary": 1.5}


def match_lanes(roads, feats, local, max_d=7.0, sample_m=10.0, share=0.5):
    """Lane counts for the area's main roads from OSM ways (osm_lanes.py's features, in lon/lat): for each road of
    LANE_M's classes, points every `sample_m` vote for the lanes of the nearest OSM way within `max_d` m that runs
    parallel (within about 25 degrees), the same way when either is one-way, under the same name or one of them
    unnamed; the count most voted wins when at least `share` of the points found one. roads: (id, class, name, line
    in metres). local: lon/lat geometry -> metres. Returns {road id: lanes}."""
    ways = []
    for f in feats:
        p = f["properties"]
        try:
            n = int(str(p.get("lanes", "")).split(";")[0])
        except ValueError:
            continue
        if not 1 <= n <= 8:
            continue
        g = local(LineString(f["geometry"]["coordinates"]))
        if g.length >= 2:
            ways.append((g, n, p.get("name", ""), p.get("oneway") in ("yes", "1", "true", "-1")))
    if not ways:
        return {}
    tree = shapely.STRtree([w[0] for w in ways])
    out = {}
    for rid, cls, name, g in roads:
        if cls not in LANE_M or not isinstance(g, LineString) or g.length < 5:
            continue
        one = rid in CARRIAGEWAY_W or name in DUAL_CARRIAGEWAYS
        votes, n_pts = Counter(), 0
        for d in np.arange(min(sample_m / 2, g.length / 2), g.length, sample_m):
            p = g.interpolate(d)
            t = np.array(g.interpolate(min(d + 1, g.length)).coords[0]) - np.array(g.interpolate(max(d - 1, 0)).coords[0])
            if np.linalg.norm(t) < 1e-6:
                continue
            t /= np.linalg.norm(t)
            n_pts += 1
            best = None
            for j in tree.query(p.buffer(max_d)):
                wg, n, wname, wone = ways[j]
                if name and wname and wname != name:
                    continue
                dist = wg.distance(p)
                if dist > max_d:
                    continue
                at = wg.project(p)
                u = np.array(wg.interpolate(min(at + 1, wg.length)).coords[0]) - np.array(wg.interpolate(max(at - 1, 0)).coords[0])
                if np.linalg.norm(u) < 1e-6:
                    continue
                c = float(u @ t) / np.linalg.norm(u)
                if abs(c) < 0.9 or ((one or wone) and c < 0.9):
                    continue
                if best is None or dist < best[0]:
                    best = (dist, n)
            if best is not None:
                votes[best[1]] += 1
        if n_pts and sum(votes.values()) / n_pts >= share:
            out[rid] = votes.most_common(1)[0][0]
    return out


def load_osm_lanes(path=OSM_LANES):
    """osm_lanes.py's features, or none when the file is missing (the build then draws the widths as before)."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f).get("features", [])


def find_carriageways(roads, sample_m=20.0, min_gap=5.0, max_gap=45.0, share=0.6):
    """The roads that are one carriageway of a dual carriageway: a road of DUAL_CLASSES with, along at least `share`
    of its length (points every `sample_m`), another such road to its left (Ghana drives on the right, so the
    opposite carriageway lies left of the way the traffic runs) between `min_gap` and `max_gap` metres centre to
    centre, running the other way (within about 35 degrees), under the same name or one of them unnamed (the links).
    Returns {road id: width}: about 55% of the median distance centre to centre (the rest is the median), between 6.5 m
    (two lanes) and 10.5 m (three); the Ring Road's surveyed widths (DUAL_CARRIAGEWAYS) still win in road_width().
    roads: (id, class, name, line in metres)."""
    cand = [(rid, name, g) for rid, cls, name, g in roads if cls in DUAL_CLASSES and isinstance(g, LineString) and g.length >= 30]
    if len(cand) < 2:
        return {}
    tree = shapely.STRtree([g for _, _, g in cand])
    out = {}
    for i, (rid, name, g) in enumerate(cand):
        hits, gaps, n = 0, [], 0
        for d in np.arange(min(sample_m / 2, g.length / 2), g.length, sample_m):
            p = np.array(g.interpolate(d).coords[0])
            q = np.array(g.interpolate(min(d + 1.0, g.length)).coords[0]) - np.array(g.interpolate(max(d - 1.0, 0.0)).coords[0])
            if np.linalg.norm(q) < 1e-6:
                continue
            t = q / np.linalg.norm(q)
            left = np.array([-t[1], t[0]])
            n += 1
            best = None
            for j in tree.query(Point(p).buffer(max_gap)):
                if j == i:
                    continue
                orid, oname, og = cand[j]
                if name and oname and oname != name:
                    continue
                at = og.project(Point(p))
                o = np.array(og.interpolate(at).coords[0])
                v = o - p
                dist = float(np.linalg.norm(v))
                if not (min_gap <= dist <= max_gap) or float(v @ left) < 0.8 * dist:
                    continue
                u = np.array(og.interpolate(min(at + 1.0, og.length)).coords[0]) - np.array(og.interpolate(max(at - 1.0, 0.0)).coords[0])
                if np.linalg.norm(u) < 1e-6 or float(u @ t) / np.linalg.norm(u) > -0.82:
                    continue
                best = dist if best is None else min(best, dist)
            if best is not None:
                hits += 1
                gaps.append(best)
        if n and hits / n >= share:
            out[rid] = round(float(np.clip(0.55 * np.median(gaps), 6.5, 10.5)), 1)
            CARRIAGEWAY_GAP[rid] = float(np.median(gaps))
    return out


MEDIAN_H = 0.15


def find_medians(roads, clip, step=4.0, min_gap=5.0, max_gap=45.0, margin=2.0, min_len=6.0):
    """The central reservations between the carriageways find_carriageways() found: polylines along the middle of
    each pair, with the width left between their asphalt (centre to centre less the two half widths), cut where
    another road crosses (half its width and `margin` either side, the gaps a driver turns through) and to clip.
    Each carriageway is walked every `step` m and paired with the nearest carriageway to its left running the other
    way; a stretch the other side of the pair already drew is left out, so each median is drawn once. Returns dicts
    with pts (x, z, local metres), w, h and name. roads: (id, class, name, line in metres)."""
    carr = [(rid, name, g) for rid, cls, name, g in roads if rid in CARRIAGEWAY_W and isinstance(g, LineString)]
    if len(carr) < 2:
        return []
    # The asphalt as drawn (the Ring Road's surveyed 7 m win over the found widths).
    width = {rid: road_width(cls, name, rid=rid) for rid, cls, name, _ in roads if rid in CARRIAGEWAY_W}
    tree = shapely.STRtree([g for _, _, g in carr])
    ids = {rid for rid, _, _ in carr}
    others = [(g, road_width(cls, name, rid=rid)) for rid, cls, name, g in roads if rid not in ids]
    otree = shapely.STRtree([g for g, _ in others]) if others else None
    seen = {}                                    # 2 m cells -> midpoints already drawn
    cell = lambda p: (int(p[0] // 2.0), int(p[1] // 2.0))

    def drawn(p):
        cx, cy = cell(p)
        return any(np.hypot(*(q - p)) < 3.0 for dx in (-1, 0, 1) for dy in (-1, 0, 1) for q in seen.get((cx + dx, cy + dy), ()))

    runs = []
    for i, (rid, name, g) in enumerate(sorted(carr, key=lambda c: c[0])):
        run, n = [], max(2, int(math.ceil(g.length / step)) + 1)
        samples = []
        for d in np.linspace(0.0, g.length, n):
            p = np.array(g.interpolate(d).coords[0])
            q = np.array(g.interpolate(min(d + 1.0, g.length)).coords[0]) - np.array(g.interpolate(max(d - 1.0, 0.0)).coords[0])
            best = None
            if np.linalg.norm(q) > 1e-6:
                t = q / np.linalg.norm(q)
                left = np.array([-t[1], t[0]])
                for j in tree.query(Point(p).buffer(max_gap)):
                    orid, oname, og = carr[j]
                    if orid == rid or (name and oname and oname != name):
                        continue
                    at = og.project(Point(p))
                    o = np.array(og.interpolate(at).coords[0])
                    v = o - p
                    dist = float(np.linalg.norm(v))
                    if not (min_gap <= dist <= max_gap) or float(v @ left) < 0.8 * dist:
                        continue
                    u = np.array(og.interpolate(min(at + 1.0, og.length)).coords[0]) - np.array(og.interpolate(max(at - 1.0, 0.0)).coords[0])
                    if np.linalg.norm(u) < 1e-6 or float(u @ t) / np.linalg.norm(u) > -0.82:
                        continue
                    w = dist - (width[rid] + width[orid]) / 2
                    if best is None or dist < best[0]:
                        best = (dist, (p + o) / 2, w, oname)
            samples.append(best)
        for b in samples + [None]:
            if b is not None and b[2] >= 0.5 and not drawn(b[1]):
                run.append(b)
                continue
            if len(run) >= 2:
                runs.append((run, name or next((r[3] for r in run if r[3]), "")))
            run = []
        for b in samples:
            if b is not None:
                seen.setdefault(cell(b[1]), []).append(b[1])
    out = []
    for run, name in runs:
        line = LineString([b[1] for b in run])
        widths = np.array([b[2] for b in run])
        cuts = []
        if otree is not None:
            for j in otree.query(line):
                og, ow = others[j]
                hit = line.intersection(og)
                for pt in getattr(hit, "geoms", [hit]):
                    if isinstance(pt, Point):
                        s = line.project(pt)
                        cuts.append((s - ow / 2 - margin, s + ow / 2 + margin))
        cuts.sort()
        pieces, start = [], 0.0
        for a, b in cuts + [(line.length, line.length)]:
            if a > start:
                pieces.append((start, a))
            start = max(start, b)
        for a, b in pieces:
            if b - a < min_len:
                continue
            seg = substring(line, a, b).intersection(clip)
            for s in getattr(seg, "geoms", [seg]):
                if not isinstance(s, LineString) or s.length < min_len:
                    continue
                s = s.simplify(0.3)
                at = [line.project(Point(c)) for c in s.coords]
                w = float(np.median(np.interp(at, np.linspace(0, line.length, len(widths)), widths)))
                out.append({"pts": [[round(x, 1), round(z, 1)] for x, z in s.coords], "w": round(w, 1), "h": MEDIAN_H,
                            **({"name": name} if name else {})})
    return out


def is_carriageway(rid, cls, name):
    """One carriageway of a dual carriageway: found in the geometry, or one of the Ring Road's named ones."""
    return rid in CARRIAGEWAY_W or (cls in MAJOR_CLASSES and name in DUAL_CARRIAGEWAYS)


def lanes_of(rid, cls, name, default=2):
    """The lanes the road is drawn with: OSM's count, at least two on a carriageway (a count of one there is a slip
    road's, matched where it runs alongside before it joins), else `default`."""
    n = LANES.get(rid)
    if n is None:
        return default
    return max(2, n) if is_carriageway(rid, cls, name) else n


def road_width(cls, name="", widths=None, rid=None):
    """The carriageway's width (m): by class, except the Ring Road's surveyed main carriageways and Danquah Circle,
    and the carriageways find_carriageways() found (by road id)."""
    widths = widths or ROAD_WIDTH
    if cls in MAJOR_CLASSES and name == SURVEYED_CARRIAGEWAY:
        return CARRIAGEWAY_M
    if rid is not None and rid in LANES and cls in LANE_M:
        w = lanes_of(rid, cls, name) * LANE_M[cls] + EDGE_M
        if is_carriageway(rid, cls, name):
            # A carriageway: as many lanes as OSM counts, but a median left between it and its opposite one.
            gap = CARRIAGEWAY_GAP.get(rid)
            if gap:
                w = min(w, max(6.0, gap - MEDIAN_MIN_M))
            return round(w, 1)
        # A two-way road: never narrower than its class draws it (the class widths include the parking edges).
        return max(round(w, 1), widths[cls])
    if cls in MAJOR_CLASSES and name in DUAL_CARRIAGEWAYS:
        return CARRIAGEWAY_M
    if rid is not None and rid in CARRIAGEWAY_W:
        return CARRIAGEWAY_W[rid]
    if cls in MAJOR_CLASSES:
        if name in DUAL_CARRIAGEWAYS:
            return CARRIAGEWAY_M
        if name in NAMED_WIDTH:
            return NAMED_WIDTH[name]
    return widths[cls]


ROAD_LIFT = {"motorway": 0.09, "trunk": 0.08, "primary": 0.07, "secondary": 0.06, "tertiary": 0.05,
             "residential": 0.04, "unclassified": 0.04, "living_street": 0.04, "service": 0.035}
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
# The classes a surveyed width overrides: the Ring Road's slip roads (tertiary) keep theirs.
MAJOR_CLASSES = ("motorway", "trunk", "primary")
STOREY_M = 3.2
CELL_M = 250.0

MATERIALS = {
    "M_Ground": (0.20, 0.17, 0.14),
    "M_Asphalt": (0.11, 0.115, 0.125),
    "M_AsphaltMajor": (0.13, 0.135, 0.145),
    "M_RoadPaint": (0.85, 0.82, 0.74),
    "M_GroundVerge": (0.27, 0.21, 0.15),
    "M_Roof": (0.36, 0.37, 0.38),
    "M_Wall_Concrete": (0.85, 0.83, 0.78),
    "M_Wall_Ochre": (0.79, 0.64, 0.42),
    "M_Wall_PaleBlue": (0.66, 0.75, 0.81),
    "M_Wall_Terracotta": (0.71, 0.40, 0.29),
    "M_Wall_Grey": (0.56, 0.56, 0.54),
    "M_Wall_Mint": (0.62, 0.76, 0.66),
    "M_Lighthouse_White": (0.90, 0.90, 0.88),
    "M_Lighthouse_Red": (0.62, 0.13, 0.10),
}
WALLS = [m for m in MATERIALS if m.startswith("M_Wall_")]


def unit_hash(*parts):
    """Deterministic number in [0, 1) from any values, so reruns give the same city."""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def storeys(area_m2, rnd, on_oxford):
    """Typology rules for footprints without a height: kiosks, houses, compounds, blocks."""
    if area_m2 < 40:
        n = 1
    elif area_m2 < 150:
        n = 1 + (rnd > 0.55)
    elif area_m2 < 500:
        n = 2 + (rnd > 0.6)
    elif area_m2 < 1500:
        n = 2 + int(rnd * 3)
    else:
        n = 3 + int(rnd * 4)
    if on_oxford:
        n = max(n, 2) + (rnd > 0.7)
    return n


class Mesh:
    """Triangles grouped by (object, material), written as OBJ with flat normals and UVs."""

    def __init__(self):
        self.groups = {}

    def tri(self, obj, mat, a, b, c, uv=None):
        """a, b, c are (east, north, up) and counter-clockwise seen from the side the face points to."""
        self.groups.setdefault((obj, mat), []).append((a, b, c, uv))

    def quad(self, obj, mat, a, b, c, d, uv=None):
        uva, uvb = (None, None) if uv is None else ((uv[0], uv[1], uv[2]), (uv[0], uv[2], uv[3]))
        self.tri(obj, mat, a, b, c, uva)
        self.tri(obj, mat, a, c, d, uvb)

    def stats(self):
        tris = sum(len(v) for v in self.groups.values())
        objs = len({k[0] for k in self.groups})
        return tris, objs

    def write(self, path, mtl_name):
        def to_obj(p):  # (east, north, up) -> OBJ (west, up, north)
            return (-p[0], p[2], p[1])

        lines = [f"# CHALE district, {len(self.groups)} groups", f"mtllib {mtl_name}"]
        v = vt = vn = 0
        for obj in sorted({k[0] for k in self.groups}):
            lines.append(f"o {obj}")
            for (o, mat), tris in sorted(self.groups.items()):
                if o != obj:
                    continue
                lines.append(f"usemtl {mat}")
                for a, b, c, uv in tris:
                    n = np.cross(np.subtract(b, a), np.subtract(c, a))
                    ln = np.linalg.norm(n)
                    if ln < 1e-9:
                        continue
                    n = to_obj(n / ln)
                    for p in (a, b, c):
                        x, y, z = to_obj(p)
                        lines.append(f"v {x:.3f} {y:.3f} {z:.3f}")
                    uvs = uv or ((a[0] / 4, a[1] / 4), (b[0] / 4, b[1] / 4), (c[0] / 4, c[1] / 4))
                    for u, w in uvs:
                        lines.append(f"vt {u:.3f} {w:.3f}")
                    lines.append(f"vn {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}")
                    vn += 1
                    lines.append(f"f {v + 1}/{vt + 1}/{vn} {v + 2}/{vt + 2}/{vn} {v + 3}/{vt + 3}/{vn}")
                    v += 3
                    vt += 3
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return v


def cell_name(prefix, e, n):
    return f"{prefix}_{int(math.floor(e / CELL_M))}_{int(math.floor(n / CELL_M))}"


def connect():
    """DuckDB for reading the slice's Parquet files: core only. Extensions are never fetched or loaded behind our back
    (DuckDB would otherwise download them from its server on first use)."""
    return duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})


def load(con, path, cols):
    return con.execute(f"SELECT {cols}, geometry::BLOB FROM '{path}'").fetchall()


def lighthouse(mesh, obj, x, y):
    """Jamestown Lighthouse (1930s): a 28 m tower, white with a broad red band, a gallery and a red-capped lantern
    (circle-reference jamestown-identity-survey.md). An octagonal prism tapering from 2.6 to 2.0 m in radius."""
    sides = 8
    def ring(r, z):
        return [(x + r * math.cos(2 * math.pi * k / sides + math.pi / 8), y + r * math.sin(2 * math.pi * k / sides + math.pi / 8), z)
                for k in range(sides)]
    def band(mat, r0, z0, r1, z1):
        a, b = ring(r0, z0), ring(r1, z1)
        for k in range(sides):
            k2 = (k + 1) % sides
            mesh.quad(obj, mat, a[k], a[k2], b[k2], b[k])
    def disc(mat, r, z, up=True):
        pts = ring(r, z)
        for k in range(1, sides - 1):
            mesh.tri(obj, mat, *((pts[0], pts[k], pts[k + 1]) if up else (pts[0], pts[k + 1], pts[k])))
    taper = lambda z: 2.6 - 0.6 * z / 25.0
    for mat, z0, z1 in (("M_Lighthouse_White", 0.0, 13.0), ("M_Lighthouse_Red", 13.0, 19.0), ("M_Lighthouse_White", 19.0, 25.0)):
        band(mat, taper(z0), z0, taper(z1), z1)
    disc("M_Lighthouse_Red", 2.9, 25.0, up=False)                      # gallery floor, seen from below
    band("M_Lighthouse_Red", 2.9, 25.0, 2.9, 25.25)
    disc("M_Lighthouse_White", 2.9, 25.25)
    band("M_Lighthouse_Red", 2.9, 25.25, 2.9, 26.2)                    # gallery railing as a solid band
    band("M_Lamp_LED", 1.3, 25.25, 1.3, 27.0)                          # lantern glass, lit at night
    band("M_Lighthouse_Red", 1.4, 27.0, 0.2, 28.4)                     # red cap
    disc("M_Lighthouse_White", 2.6, 0.0, up=False)


def area_box(area):
    """An area's box in WGS84 (lon, lat)."""
    return box(area["west"], area["south"], area["east"], area["north"])


def corridor_region(spec, road_rows, local):
    """A corridor's region in metres: its named roads, and every road of its "classes" (a motorway's unnamed pieces
    and its slip roads), buffered by its half width, with its "boxes" (whole districts, WGS84), inside its box."""
    within = area_box(spec["within"])
    classes = set(spec.get("classes", ()))
    lines = [local(g) for _, cls, name, g in road_rows
             if name in spec["roads"] or (cls in classes and g.intersects(within))]
    if not lines:
        return None
    region = shapely.union_all(lines).buffer(spec["half_width_m"], quad_segs=4)
    for b in spec.get("boxes", ()):
        region = region.union(local(area_box(b)))
    region = region.intersection(local(within))
    return shapely.make_valid(region.simplify(1.0))


def area_outline(area, road_rows, local, lonlat=None):
    """Where an area's content goes, in metres from its origin, and the wider region its lap may use.

    A box area (south, north, west, east) is its box, the lap region the same. A corridor area follows named roads:
    the union of every segment named in "corridor"["roads"], buffered by "half_width_m" and kept inside the box
    "within"; that is the lap region. The content then leaves out the boxes of the areas named in "minus", so two
    areas meet along a shared edge; a corridor named there leaves out its region as this slice's roads draw it (the
    Liberation Road corridor meets the Ring Road's where their buffers meet). road_rows: (id, class, name, WGS84
    geometry) of the slice. Returns (clip, region, minus): minus is the list of the left-out areas' outlines in
    WGS84."""
    if "corridor" not in area:
        clip = local(area_box(area))
        return clip, clip, []
    spec = area["corridor"]
    region = corridor_region(spec, road_rows, local)
    if region is None:
        sys.exit(f"no road named {', '.join(spec['roads'])} in the slice")
    minus = []
    for m in area.get("minus", []):
        other = AREAS[m]
        if "corridor" not in other:
            minus.append(area_box(other))
            continue
        theirs = corridor_region(other["corridor"], road_rows, local)
        if theirs is not None and lonlat is not None:
            minus.append(lonlat(theirs))
    clip = region
    for m in minus:
        clip = clip.difference(local(m))
    return clip, region, minus


def ground_region(clip, minus, local, margin):
    """The ground a corridor area lays: its clip widened by the margin, less the ground the left-out areas lay
    themselves (their outline in metres widened by the same margin: a box's bounds, as build() grids them, or a
    corridor's ground), so ground never overlaps."""
    g = clip.buffer(margin, join_style="mitre")
    for m in minus:
        g = g.difference(local(m).buffer(margin, join_style="mitre"))
    return g


def lay_ground(mesh, region):
    """Ground triangles over a region, cut on the 250 m cell grid into objects Ground_<i>_<j> so the GLB export
    can tile them with the buildings and roads of the same cell."""
    x0, y0, x1, y1 = region.bounds
    for i in range(int(math.floor(x0 / CELL_M)), int(math.floor(x1 / CELL_M)) + 1):
        for j in range(int(math.floor(y0 / CELL_M)), int(math.floor(y1 / CELL_M)) + 1):
            piece = region.intersection(box(i * CELL_M, j * CELL_M, (i + 1) * CELL_M, (j + 1) * CELL_M))
            obj = f"Ground_{i}_{j}"
            for poly in getattr(piece, "geoms", [piece]):
                if not isinstance(poly, Polygon) or poly.area < 1:
                    continue
                poly = orient(poly, 1.0)
                rings = [poly.exterior] + list(poly.interiors)
                verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
                ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
                idx = earcut.triangulate_float64(verts, ends)
                for k in range(0, len(idx), 3):
                    a, b, c = (tuple(verts[idx[k + n]]) for n in range(3))
                    if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
                        b, c = c, b
                    mesh.tri(obj, "M_Ground", (*a, 0), (*b, 0), (*c, 0),
                             uv=((a[0] / 8, a[1] / 8), (b[0] / 8, b[1] / 8), (c[0] / 8, c[1] / 8)))


# The interchanges, flyovers and footbridges (scripts/elevated.py): what the game builds above or below the ground.
ELEVATED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "work", "elevated.json")
OFF_STRUCTURE_M = 3.0     # a road this close to a structure's centreline, and along it, is that structure
OFF_STRUCTURE_DEG = 25.0
# What stands on the ground keeps off a structure's road where the road is low over it: within its asphalt, the
# deck's overhang and this much more (m), wherever the road there is under the thing's height and a car's over the
# ground (a ramp, a cut, a low deck). Buildings, compound walls, street lights, parked cars and hiding places stood in
# the lanes of the ramps and cuts, and under the decks through them (the director, 18 Nov 2026: "some lanes of the
# interchanges are blocked").
STRUCTURE_CLEAR_M = 1.5
LAMP_TOP_M = 10.0         # a street light's height
CAR_TOP_M = 1.6           # a parked car's
FOOT_CLEAR_M = 60.0       # no car parked this near a ramp's foot on the ground (m)



def load_elevated(path, oe, on):
    """The structures of scripts/elevated.py in the area's frame (x east, y north), or ([], []) without the file."""
    if not path or not os.path.exists(path):
        return [], []
    d = json.load(open(path))
    move = lambda pts: [[round(x - oe, 2), round(y - on, 2)] for x, y in pts]
    return ([{**p, "pts": move(p["pts"]), **({"drawn": move(p["drawn"])} if "drawn" in p else {})} for p in d["pieces"]],
            [{**f, "pts": move(f["pts"])} for f in d["footbridges"]])


def whole_structures(pieces, near):
    """The structures in the area: every piece of each group joined end to end (an interchange, a flyover and its
    ramps) that meets the area, whole. A group cut by the area's edge would leave a deck ending in the air and its
    roads cut off from the ground's (Circle lies across two areas: both name it, and it is built once)."""
    ends = defaultdict(list)
    for i, p in enumerate(pieces):
        for e in (p["pts"][0], p["pts"][-1]):
            ends[(round(e[0]), round(e[1]))].append(i)
    group = list(range(len(pieces)))

    def find(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for (x, y), ids in ends.items():
        near_ids = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in ends.get((x + dx, y + dy), ())]
        for j in near_ids:
            group[find(j)] = find(ids[0])
    inside = [LineString(p["pts"]).intersects(near) for p in pieces]
    meets = defaultdict(bool)
    for i in range(len(pieces)):
        meets[find(i)] |= inside[i]
    return [p for i, p in enumerate(pieces) if meets[find(i)]]


def cut_structures(roads, pieces, step=1.0):
    """The roads with what runs along a structure taken out: a road within OFF_STRUCTURE_M of a piece's centreline
    and within OFF_STRUCTURE_DEG of its direction is that piece (Overture's roads are OpenStreetMap's ways), which the
    game builds on its deck, its fill or in its cut; a road that crosses a piece keeps its crossing. Returns the roads
    (rid, cls, name, line) and the metres taken out."""
    if not pieces:
        return roads, 0.0
    segs, dirs = [], []
    for p in pieces:
        pts = p.get("drawn", p["pts"])      # OpenStreetMap's line, where elevated.py moved twin carriageways apart
        for a, b in zip(pts, pts[1:]):
            d = math.dist(a, b)
            if d > 1e-6:
                segs.append(LineString([a, b]))
                dirs.append(((b[0] - a[0]) / d, (b[1] - a[1]) / d))
    tree = shapely.STRtree(segs)
    cos_max = math.cos(math.radians(OFF_STRUCTURE_DEG))
    out, gone = [], 0.0
    for rid, cls, name, g in roads:
        lines = [g] if isinstance(g, LineString) else [l for l in getattr(g, "geoms", []) if isinstance(l, LineString)]
        for line in lines:
            if not tree.query(line.buffer(OFF_STRUCTURE_M)).size:
                out.append((rid, cls, name, line))
                continue
            n = max(2, int(math.ceil(line.length / step)) + 1)
            ds = np.linspace(0, line.length, n)
            keep = np.ones(n, bool)
            for k, dd in enumerate(ds):
                p = line.interpolate(dd)
                a = line.interpolate(max(0.0, dd - 0.5))
                b = line.interpolate(min(line.length, dd + 0.5))
                vx, vy = b.x - a.x, b.y - a.y
                vn = math.hypot(vx, vy) or 1.0
                for j in tree.query(p.buffer(OFF_STRUCTURE_M)):
                    # Square ends: the piece stops at its last point, where the road on the ground goes on.
                    (ax, ay), (bx, by) = segs[j].coords
                    ln = math.hypot(bx - ax, by - ay)
                    t = ((p.x - ax) * dirs[j][0] + (p.y - ay) * dirs[j][1])
                    if t < -0.01 or t > ln + 0.01:
                        continue
                    if segs[j].distance(p) < OFF_STRUCTURE_M and abs((vx * dirs[j][0] + vy * dirs[j][1]) / vn) > cos_max:
                        keep[k] = False
                        break
            if keep.all():
                out.append((rid, cls, name, line))
                continue
            kept = 0.0
            k = 0
            while k < n:
                if not keep[k]:
                    k += 1
                    continue
                m = k
                while m + 1 < n and keep[m + 1]:
                    m += 1
                # The cut falls half a step into the structure: the piece's own end meets it there.
                d0 = ds[k] if k == 0 else (ds[k] + ds[k - 1]) / 2
                d1 = ds[m] if m == n - 1 else (ds[m] + ds[m + 1]) / 2
                if d1 - d0 >= 2.0:
                    seg = substring(line, d0, d1)
                    if isinstance(seg, LineString):
                        out.append((rid, cls, name, seg))
                        kept += seg.length
                k = m + 1
            gone += float(line.length) - kept
    return out, gone


def structure_ground(pieces):
    """Each piece's road as footprints on the ground, one per segment: (polygon, lowest offset over the ground at its
    ends, m). The polygon covers the asphalt, the deck's overhang and STRUCTURE_CLEAR_M, flat at the segment's ends."""
    out = []
    for p in pieces:
        reach = float(p.get("w", 7.0)) / 2 + 0.55 + STRUCTURE_CLEAR_M
        for k in range(len(p["pts"]) - 1):
            a, b = p["pts"][k], p["pts"][k + 1]
            if math.dist(a, b) < 1e-6:
                continue
            out.append((LineString([a, b]).buffer(reach, cap_style="flat"), min(p["off"][k], p["off"][k + 1])))
    return out


def under_structure(geom, ground, top):
    """Whether something on the ground, `top` m tall, at `geom` stands in a structure's road: a footprint of
    structure_ground() whose road is lower over the ground there than the thing's top and a car's height (a cut's
    road is under the ground: anything over it stands in its lanes or on its wall)."""
    return any(off < top + 2.0 and poly.intersects(geom) for poly, off in ground)


def typology_building(mesh, obj, wall, bid, poly, n, height, floors, parapet, near, cls, name, place_index, fronts,
                      zones, area_name, counts, names):
    """Builds a footprint by its typology (typology.py) when it has one, and returns its kind; None for the generic
    massing (massing.building() or the plain extrusion), left to the caller. n: its storeys (None when only a height
    is mapped); the look-dev Oxford Street's neighbours (near) keep the generic rules."""
    if near:
        return None
    tagged = bool(height or floors)
    storeys_n = n if n is not None else max(1, round(float(height) / STOREY_M))
    places = place_index.inside(poly)
    zone = not tagged and any(z.contains(poly.centroid) for z in zones)
    if zone and (cls in ("roof", "transportation") or any("terminal" in (x or "").lower() for x in [name] + [p[1] for p in places])):
        zone = False
    kind = typology.classify(bid, poly.area, storeys_n, tagged, height, cls, name, places,
                             typology.frontage(fronts, poly), area_name, zone)
    counts[f"kind {kind}"] += 1
    label = None
    if kind == "church":
        label = typology.worship_name(name, places)
        massing.church(mesh, obj, wall, bid, poly, counts)
    elif kind == "mosque":
        label = typology.worship_name(name, places)
        massing.mosque(mesh, obj, wall, bid, poly, storeys_n, parapet, counts)
    elif kind == "warehouse":
        massing.warehouse(mesh, obj, wall, bid, poly, cls, counts)
    elif kind == "tower":
        drawn = storeys_n if tagged else massing.pick(TOWER_STOREYS, unit_hash(bid, "tower storeys"))
        massing.tower(mesh, obj, wall, bid, poly, drawn, counts)
    elif kind == "apartments":
        massing.building(mesh, obj, wall, bid, poly, storeys_n, parapet, False, False, counts, "osu", None, massing.BALCONIES)
    elif kind == "storey":
        label = typology.shop_name(name, places)
        massing.building(mesh, obj, wall, bid, poly, storeys_n, parapet, False, False, counts, "osu", None, massing.SHOPFRONT)
    elif kind == "kiosk":
        massing.kiosk(mesh, obj, wall, bid, poly, counts)
    else:
        return None
    if label:
        f = typology.facing(fronts, poly)
        if f is not None:
            c = poly.centroid
            names.append({"name": label, "kind": kind, "x": round(c.x, 1), "z": round(c.y, 1),
                          "nx": round(float(f[0]), 3), "nz": round(float(f[1]), 3)})
    return kind


# The avenue kit's wall paints for the civic and office types (avenue_kit.py): render, grey and pale blue, as the
# Ministries' and Ridge's offices; taken only where the district already paints with them (no material new to a cell).
OFFICE_PAINTS = [("M_Wall_Concrete", 0.5), ("M_Wall_Grey", 0.25), ("M_Wall_PaleBlue", 0.25)]


def avenue_building(mesh, obj, wall, bid, poly, n, height, floors, parapet, src, cls, place_index, fronts, avenue,
                    counts, rows, district, rules, institutions):
    """Builds a footprint of Independence Avenue's corridor by its typology (avenue_kit.py) and returns it, or None for
    the generic massing (the typologies of later stages, the modelled sites, the small machine-learnt footprints).
    rows receives one line per footprint of the corridor for the build's record (avenue-kit.csv)."""
    places = place_index.inside(poly)
    named = avenue.named_of(poly)
    lot = avenue.lot_of(poly)
    seg = avenue.segment(poly)
    mapped = floors or (max(1, round(float(height) / STOREY_M)) if height else None)
    ml = src != "OpenStreetMap"
    kind = avenue_kit.choose(poly.area, mapped, cls, places, lot, named, seg, ml)
    c = poly.centroid
    row = {"id": bid, "source": src, "x": round(c.x, 1), "y": round(c.y, 1), "area_m2": round(poly.area), "segment": seg, "lot": (named or lot or {}).get("lot"),
           "class": cls or "", "kind": kind or "generic", "storeys": "", "estimated": ""}
    rows.append(row)
    if kind is None:
        counts["avenue generic"] += 1
        return None
    est = n if n is not None else (mapped or 1)
    k = avenue_kit.storeys_for(kind, est, mapped)
    row["storeys"], row["estimated"] = k, "" if mapped else "yes"
    if kind in ("T2", "T4", "T5", "T6", "T7", "T12", "T13", "T14", "VOL"):
        # The civic types are painted by the game in their kind's paint; their material only picks the wall finish's
        # grain, so the plain render's where the district has it (not the grey's stained concrete).
        have = {m for m, _ in (rules["paints"] or [(w, 1) for w in WALLS])}
        mix = [(m, w) for m, w in OFFICE_PAINTS if m in have] or [(wall, 1.0)]
        wall = "M_Wall_Concrete" if "M_Wall_Concrete" in have else massing.pick(mix, unit_hash(bid, "office paint"))
    facing = typology.facing(fronts, poly)
    if kind == "T1":
        massing.villa(mesh, obj, wall, bid, poly, k, parapet, counts, massing.ROOF_PALETTES.get(district), None, facing)
    elif kind in ("T2", "T12"):
        massing.office(mesh, obj, wall, bid, poly, k, counts, facing, porte=kind == "T12")
    elif kind == "T4":
        massing.bank(mesh, obj, wall, bid, poly, k, counts, facing)
    elif kind == "T5":
        massing.ministry(mesh, obj, wall, bid, poly, k, counts)
    elif kind in ("T6", "T7", "T14"):
        massing.veranda_block(mesh, obj, wall, bid, poly, k, counts, kind, facing)
    elif kind == "T8":
        massing.building(mesh, obj, wall, bid, poly, k, parapet, False, False, counts, "osu", None, massing.BALCONIES)
    elif kind == "T9":
        massing.building(mesh, obj, wall, bid, poly, k, parapet, False, False, counts, "osu", None, massing.SHOPFRONT)
    elif kind == "T10":
        massing.kiosk(mesh, obj, wall, bid, poly, counts)
    elif kind == "T11":
        massing.church(mesh, obj, wall, bid, poly, counts)
    elif kind == "T13":
        massing.car_park(mesh, obj, wall, bid, poly, k, counts)
    elif kind == "VOL":
        massing.volume(mesh, obj, wall, bid, poly, k, counts)
    counts[f"avenue {kind}"] += 1
    if kind in ("T7", "T14"):
        institutions.append((poly, wall))
    return kind


def build(args):
    area = AREAS[args.name]
    rules = DISTRICT_RULES.get(area.get("district", "osu"), DISTRICT_RULES["osu"])
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True)
    oe, on = to_utm.transform(area["origin"][1], area["origin"][0])

    def local(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])) - (oe, on))

    def lonlat(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(to_ll.transform(xy[:, 0] + oe, xy[:, 1] + on)))

    con = connect()
    road_rows = [(rid, cls, name or "", shapely.from_wkb(bytes(blob)))
                 for rid, cls, name, blob in load(con, os.path.join(args.slice, "roads.parquet"), "id, class, name")]
    clip, region, minus = area_outline(area, road_rows, local, lonlat)
    # Buildings belong to the area whose box (or corridor, less the boxes it leaves out) holds their centroid.
    lonlat_box = area_box(area) if "corridor" not in area else lonlat(region)
    shapely.prepare(lonlat_box)
    minx, miny, maxx, maxy = clip.bounds
    mesh = Mesh()

    # Roads first: buildings near Oxford Street get an extra storey, so its line is needed. A corridor keeps the
    # roads of its whole region, inside the areas it leaves out too, for its lap; everything drawn is cut to clip.
    roads = []
    for rid, cls, name, g in road_rows:
        if cls not in ROAD_WIDTH:
            continue
        if not g.intersects(lonlat_box):
            continue
        roads.append((rid, cls, name, local(g)))
    # The interchanges and flyovers: their pieces leave the ground's roads (the game builds them at their levels).
    elev, feet = load_elevated(args.elevated, oe, on)
    on_structures, under = [], Counter()
    if elev or feet:
        near = clip.buffer(50)
        elev = whole_structures(elev, near)
        feet = [f for f in feet if LineString(f["pts"]).intersects(near)]
        roads, gone = cut_structures(roads, elev)
        on_structures = structure_ground(elev)
        print(f"structures: {len(elev)} pieces ({sum(LineString(p['pts']).length for p in elev) / 1000:.2f} km) "
              f"and {len(feet)} footbridges in the area; {gone / 1000:.2f} km of road taken off the ground")
    oxford = shapely.union_all([g for _, _, n, g in roads if n == "Oxford Street"]) if roads else None
    CARRIAGEWAY_W.clear()
    CARRIAGEWAY_GAP.clear()
    CARRIAGEWAY_W.update(find_carriageways(roads))
    LANES.clear()
    LANES.update(match_lanes(roads, load_osm_lanes(), local))
    if LANES:
        by_n = Counter(LANES.values())
        print(f"OSM lane counts: {len(LANES)} road segments ({', '.join(f'{v} with {k}' for k, v in sorted(by_n.items()))})")
    if CARRIAGEWAY_W:
        named = sorted({n for rid, _, n, _ in roads if rid in CARRIAGEWAY_W and n})
        print(f"dual carriageways: {len(CARRIAGEWAY_W)} carriageway segments, median between them"
              + (f" ({', '.join(named[:8])}{'...' if len(named) > 8 else ''})" if named else ""))

    # The places OpenStreetMap and Overture map in the area (an older slice has no places.parquet, an older extract no
    # osm.parquet: none then), read before the buildings: the fuel stations' canopies are left out of them.
    places_path = os.path.join(args.slice, "places.parquet")
    # Every place OpenStreetMap maps there joins them, where the slice has its extract (scripts/osm_extract.py).
    osm_path = os.path.join(args.slice, "osm.parquet")
    osm_rows = named_places.load_osm(osm_path) if os.path.exists(osm_path) else []
    place_rows = named_places.load_places(con, places_path) if os.path.exists(places_path) else []
    # The landmarks the game builds (landmark_footprints.py): their footprints are left out of the buildings below,
    # and the places the data has wrong on them set right.
    lm_outlines = ([] if getattr(args, "keep_landmarks", False) else
                   landmark_footprints.load(getattr(args, "landmark_footprints", landmark_footprints.FOOTPRINTS)))
    if landmark_footprints.fix_places(place_rows, lm_outlines):
        print("places on the landmarks set right: " + ", ".join(f["name"] for lm in landmark_footprints.LANDMARKS
                                                                for f in lm.get("places", [])))
    to_local_ll = lambda lon, lat: tuple(np.array(to_utm.transform(lon, lat)) - (oe, on))
    shapely.prepare(clip)
    inside_clip = lambda x, z: bool(shapely.contains_xy(clip, x, z))
    # The fuel stations (scripts/fuel_stations.py): the game builds their forecourts, canopies on columns included, so
    # the canopies OpenStreetMap maps as building=roof (extruded to the ground as blocks until now) are left out of the
    # buildings below, and the compound walls and parked cars keep off their ground. --no-fuel builds as before.
    # The streets a car drives, for the street in front of a fuel station or a charger.
    street_lines = []
    for rid, cls, name, g in roads:
        if cls not in TRAFFIC_CLASSES:
            continue
        g = g.intersection(clip)
        for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))):
            if isinstance(l, LineString) and l.length >= 2:
                street_lines.append((name, road_width(cls, name, rid=rid), l))
    fuel = []
    if not args.no_fuel:
        roofs = [local(shapely.from_wkb(bytes(blob))) for (blob,) in
                 con.execute(f"SELECT geometry::BLOB FROM '{os.path.join(args.slice, 'buildings.parquet')}' WHERE class = 'roof'").fetchall()]
        fuel = fuel_stations.find_fuel_stations(osm_rows, place_rows, street_lines, inside_clip, to_local_ll, local, roofs)
        # The footprints the point stations' pins stand in or beside (their canopies, drawn as plain buildings or by a
        # detector).
        pins = [s for s in fuel if not s.get("site") and not s.get("canopies")]
        if pins:
            pin_tree = shapely.STRtree([lonlat(Point(s["x"], s["z"]).buffer(fuel_stations.PIN_M)) for s in pins])
            held = []
            for (blob,) in con.execute(f"SELECT geometry::BLOB FROM '{os.path.join(args.slice, 'buildings.parquet')}'").fetchall():
                g = shapely.from_wkb(bytes(blob))
                if len(pin_tree.query(g, predicate="intersects")):
                    held.append(local(g).buffer(0))
            adopted = fuel_stations.adopt_pin_footprints(pins, held)
            if adopted:
                print(f"fuel stations: {adopted} pinned in or beside a footprint of a canopy's size, built under it")
    fuel_canopies = fuel_stations.canopy_shapes(fuel)
    fuel_lots = fuel_stations.lot_shapes(fuel)
    fuel_ground = fuel_stations.keep_clear(fuel)
    replaced = cleared = 0
    # The electric-vehicle chargers (scripts/ev_chargers.py): OpenStreetMap's and Overture's, and those their operators
    # document at a host OpenStreetMap maps (--ev-documented); each joined to the fuel station it stands in. Their
    # ground off a forecourt is kept clear of compound walls and parked cars like a station's. --no-ev: none.
    ev = []
    if not args.no_ev:
        documented = ev_chargers.load_documented(args.ev_documented) if args.ev_documented else []
        hosts = ev_chargers.read_hosts(osm_path, local, [d.get("host") for d in documented]) if os.path.exists(osm_path) else {}
        ev = ev_chargers.find_ev_chargers(osm_rows, place_rows, street_lines, inside_clip, to_local_ll, local, fuel, hosts, documented)
        fuel_ground = fuel_ground + ev_chargers.keep_clear(ev)

    landmark_points = [shapely.geometry.Point(*np.array(to_utm.transform(lon, lat)) - (oe, on))
                       for _, lat, lon in rules["landmarks"]]
    # The footprints the game's landmarks stand on: not drawn, not solid, but kept clear like any building.
    lm_match = landmark_footprints.Matcher(lm_outlines, local)
    landmark_ground = []
    # The grounds the game lays out itself (landmark_footprints.py "grounds"): no compound wall, parked car or hiding
    # place on them; their buildings stay.
    lm_grounds = landmark_footprints.grounds(lm_outlines, local)

    # Buildings, minus those a detailed look-dev version replaces.
    exclude = set()
    if args.exclude and os.path.exists(args.exclude):
        exclude = {l.strip() for l in open(args.exclude) if l.strip()}
    # What the satellite saw (imagery.py roofs): each roof's material, and the footprints that are swimming pools.
    seen_roofs = imagery.load_roofs(getattr(args, "roofs", None))
    if seen_roofs:
        print(f"roofs seen from the Maxar 2019 mosaic: {len(seen_roofs)}")
    nb = 0
    heights = []
    # Building modules (massing.py): who stands in a walled compound, and what keeps the walls out.
    yards, keep_out = [], []
    counts = Counter()
    # The typologies (typology.py): the places inside each footprint, the roads it fronts, the office districts.
    # City-wide they are paused (20 Nov 2026) and opt-in (--typologies); the avenue's kit (--avenue-kit) uses their
    # builders on Independence Avenue's corridor alone.
    kit2 = getattr(args, "typologies", False) and not args.no_massing
    avenue = None
    if getattr(args, "avenue_kit", None) and not args.no_massing:
        avenue = avenue_kit.Avenue(avenue_kit.load(args.avenue_kit), local)
        if not avenue.touches(clip):
            avenue = None
    avenue_rows, institutions, fancy_yards = [], [], []
    if kit2 or avenue is not None:
        place_index = typology.Places(typology.slice_places(args.slice, local, con))
        fronts = typology.front_index([(cls, g) for _, cls, _, g in roads], lambda c: ROAD_WIDTH[c])
        zones = [local(area_box(z)) for z in TOWER_ZONES.values()]
    names = []
    named_cols = kit2 or avenue is not None
    cols = "id, source, height, num_floors" + (", class, name" if named_cols else "")
    for row in load(con, os.path.join(args.slice, "buildings.parquet"), cols):
        bid, src, height, floors, blob = row[:4] + row[-1:]
        bcls, bname = row[4:6] if named_cols else (None, None)
        g = shapely.from_wkb(bytes(blob))
        c = g.centroid
        if not lonlat_box.contains(c) or any(m.contains(c) for m in minus):
            continue
        g = local(g).buffer(0).simplify(0.25)
        if any(g.contains(p) for p in landmark_points):
            continue                                    # the landmark is built by hand below
        if fuel_canopies[1] is not None and fuel_stations.is_canopy(g, fuel_canopies, src):
            replaced += 1
            continue                                    # a fuel station's canopy: the game builds it on columns
        if fuel_lots[1] is not None and fuel_stations.on_lot(g, fuel_lots, src):
            cleared += 1
            continue                                    # drawn by a detector on a station's ground: the forecourt's
        polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
        if bid in exclude:
            keep_out += [p for p in polys if isinstance(p, Polygon)]
            continue
        seen = seen_roofs.get(bid)
        lm_osm = lm_match.match(g)
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.area < 8:
                continue
            poly = orient(poly, 1.0)
            if seen == imagery.POOL and not args.no_massing:
                if lm_osm:
                    continue                            # on a landmark: the game builds its ground
                massing.pool(mesh, cell_name("Buildings", poly.centroid.x, poly.centroid.y), poly, counts)
                continue                                # a pool, not a building: no walls, no collider
            rnd = unit_hash(bid)
            near = oxford is not None and poly.distance(oxford) < 35
            if height:
                h = float(height)
                n = None
            else:
                if floors:
                    n = int(floors)
                elif rules.get("storeys") and poly.area >= rules["storeys_from_m2"]:
                    n = massing.pick(rules["storeys"], unit_hash(bid, "storeys"))
                else:
                    n = storeys(poly.area, rnd, near)
                h = n * STOREY_M
            parapet = 0.4 + rnd * 0.6
            h += parapet
            # Over a ramp or a cut, or under a deck lower than its roof: the structure's road is there (the game
            # builds it), and the building's walls stood across its lanes or through its deck.
            if on_structures and under_structure(poly, on_structures, h):
                under["buildings"] += 1
                continue
            if lm_osm:
                landmark_ground.append(poly)            # a landmark the game builds (landmark_footprints.py)
                continue
            heights.append(h)
            c = poly.centroid
            obj = cell_name("Buildings", c.x, c.y)
            wall = (massing.pick(rules["paints"], unit_hash(bid, "paint")) if rules["paints"]
                    else WALLS[int(unit_hash(bid, "paint") * len(WALLS))])
            if avenue is not None and avenue.inside(poly):
                kind = avenue_building(mesh, obj, wall, bid, poly, n, height, floors, parapet, src, bcls, place_index,
                                       fronts, avenue, counts, avenue_rows, area.get("district", "osu"), rules,
                                       institutions)
                if kind:
                    # Villas and the sensitive sites stand behind their walls (compounds()); the rest keep them out.
                    (yards.append((bid, poly)) if kind in ("T1", "VOL") else keep_out.append(poly))
                    if kind == "T1":
                        fancy_yards.append(poly)
                    nb += 1
                    continue
            if kit2:
                kind = typology_building(mesh, obj, wall, bid, poly, n, height, floors, parapet, near, bcls, bname,
                                         place_index, fronts, zones, args.name, counts, names)
                if kind:
                    keep_out.append(poly)
                    nb += 1
                    continue
            if not args.no_massing and n is not None:
                # Houses and small blocks off Oxford Street stand in walled yards.
                yard = rules["yards"] and not near and n <= 3 and poly.area < 600
                massing.building(mesh, obj, wall, bid, poly, n, parapet, near, yard, counts, area.get("district", "osu"), seen)
                if yard:
                    yards.append((bid, poly))
                else:
                    keep_out.append(poly)
                nb += 1
                continue
            keep_out.append(poly)
            rings = [poly.exterior] + list(poly.interiors)
            for ring in rings:
                pts = list(ring.coords)[:-1]
                for i in range(len(pts)):
                    (ae, an), (be, bn) = pts[i], pts[(i + 1) % len(pts)]
                    edge = math.hypot(be - ae, bn - an)
                    if edge < 0.05:
                        continue
                    mesh.quad(obj, wall, (ae, an, 0), (be, bn, 0), (be, bn, h), (ae, an, h),
                              uv=((0, 0), (edge / 3, 0), (edge / 3, h / 3), (0, h / 3)))
            verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
            ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
            idx = earcut.triangulate_float64(verts, ends)
            for k in range(0, len(idx), 3):
                a, b, cc = (tuple(verts[idx[k + j]]) for j in range(3))
                if (b[0] - a[0]) * (cc[1] - a[1]) - (b[1] - a[1]) * (cc[0] - a[0]) < 0:
                    b, cc = cc, b
                mesh.tri(obj, "M_Roof", (*a, h), (*b, h), (*cc, h))
            nb += 1

    for (kind, _, _), p in zip(rules["landmarks"], landmark_points):
        if kind == "lighthouse":
            lighthouse(mesh, cell_name("Landmarks", p.x, p.y), p.x, p.y)
            counts["landmarks"] += 1
            keep_out.append(p.buffer(4.0))

    compound_walls, compound_gates = [], []
    if not args.no_massing:
        district_roads = []
        for _, cls, _, g in roads:
            g = g.intersection(clip.buffer(60))
            district_roads += [(cls, l) for l in ([g] if isinstance(g, LineString) else getattr(g, "geoms", [])) if isinstance(l, LineString)]
        # Off the structures' roads where they are low (a ramp, a cut), as off a fuel station's ground.
        low = [poly for poly, off in on_structures if off < 4.0]
        compound_walls = massing.compounds(mesh, cell_name, yards, keep_out + landmark_ground + lm_grounds + fuel_ground + low, district_roads,
                                           ROAD_WIDTH, counts, clip, oxford, gates_out=compound_gates,
                                           paints=None if rules["yards"] else rules["paints"],
                                           gate_mat="M_Gate" if rules["yards"] else "M_Roof", fancy=fancy_yards or None)
        if institutions:
            # The avenue's hospital and school blocks joined by covered walks (massing.covered_walks()).
            massing.covered_walks(mesh, cell_name, institutions[0][1], [p for p, _ in institutions], counts,
                                  avoid=[p for p in keep_out + [y for _, y in yards] if all(p is not q for q, _ in institutions)])

    # Roads: ribbons with mitred joints, a dashed centre line on the major classes.
    lights = []
    road_km = 0.0
    # The dual carriageways, for the median's poles (place_lights).
    carriageways = [(rid, g) for rid, cls, name, g in roads
                    if (name in DUAL_CARRIAGEWAYS and cls in MAJOR_CLASSES) or rid in CARRIAGEWAY_W]
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        lines = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
        for line in lines:
            if not isinstance(line, LineString) or line.length < 1:
                continue
            road_km += line.length / 1000
            w, lift = road_width(cls, name, rid=rid), ROAD_LIFT[cls]
            mat = "M_AsphaltMajor" if cls in MAJOR else "M_Asphalt"
            ribbon(mesh, line, w, lift, mat, "Roads")
            one = is_carriageway(rid, cls, name)
            # The verge: packed sand and laterite outside the asphalt, on the kerbside of a carriageway (its left is
            # the median: Ghana drives on the right), on both sides of a two-way road.
            if cls in VERGE_M and name != "Oxford Street":
                vw = VERGE_M[cls]
                for side in ((-1,) if one else (-1, 1)):
                    edge = line.offset_curve(side * (w / 2 + vw / 2), join_style="mitre", mitre_limit=2.0)
                    for piece in ([edge] if isinstance(edge, LineString) else list(getattr(edge, "geoms", []))):
                        if isinstance(piece, LineString) and piece.length > 1:
                            ribbon(mesh, piece, vw, lift * 0.5, "M_GroundVerge", "Roads")
            # Lane lines between the lanes (OSM's count, else two: the centre line). Oxford Street has no lane
            # markings (street survey; OSM lane_markings=no).
            if cls in ("primary", "secondary", "trunk") and name != "Oxford Street":
                for off in lane_lines(w, lanes_of(rid, cls, name)):
                    guide = line if abs(off) < 0.05 else line.offset_curve(off, join_style="mitre", mitre_limit=2.0)
                    for gl in ([guide] if isinstance(guide, LineString) else list(getattr(guide, "geoms", []))):
                        if not isinstance(gl, LineString) or gl.length < 4:
                            continue
                        for d0 in np.arange(2.0, gl.length - 3.0, 6.0):
                            # Lines in Accra are worn: about a third of the dashes are gone.
                            if unit_hash(rid, "dash", round(off, 1), int(d0)) < 0.35:
                                continue
                            seg = substring(gl, d0, d0 + 3.0)
                            if isinstance(seg, LineString) and seg.length > 0.5:
                                ribbon(mesh, seg, 0.15, lift + 0.01, "M_RoadPaint", "Roads")
            if cls in MAJOR:
                lights += place_lights(rid, cls, name, line, w, carriageways)

    # The median's twin-arm poles first, one row about a pole step apart (both carriageways place them), then the rest.
    twins = dedupe([l for l in lights if "twin" in l], RING_STEP_M * 0.75)
    lights = dedupe(twins + [l for l in lights if "twin" not in l], 7.0)
    lights, moved, dropped = off_asphalt(lights, [(g, road_width(cls, name, rid=rid)) for rid, cls, name, g in roads])
    if moved or dropped:
        print(f"street lights off the asphalt: {moved} moved back, {dropped} dropped")
    # None on a structure's road, nor under a deck its pole would pass through: the decks have their own lamps.
    if on_structures:
        kept = [l for l in lights if not under_structure(Point(l["x"], l["z"]), on_structures, LAMP_TOP_M)]
        under["street lights"] += len(lights) - len(kept)
        lights = kept
    for l in lights:
        l.pop("twin", None)

    # Ground under everything, with a margin. A corridor lays it per cell, and not under the areas it leaves out.
    m = 60.0
    if "corridor" in area:
        lay_ground(mesh, ground_region(clip, minus, local, m))
    else:
        for e0 in np.arange(minx - m, maxx + m, CELL_M):
            for n0 in np.arange(miny - m, maxy + m, CELL_M):
                e1, n1 = min(e0 + CELL_M, maxx + m), min(n0 + CELL_M, maxy + m)
                mesh.quad("Ground", "M_Ground", (e0, n0, 0), (e1, n0, 0), (e1, n1, 0), (e0, n1, 0),
                          uv=((e0 / 8, n0 / 8), (e1 / 8, n0 / 8), (e1 / 8, n1 / 8), (e0 / 8, n1 / 8)))

    if "route" in area["lap"]:
        lap = route_lap(roads, region, area["lap"], to_utm, (oe, on))
    else:
        lap = benchmark_lap(roads, clip, area["lap"], to_utm, (oe, on))

    # Parked cars along the streets, where the district has no dressed block to park them (DISTRICT_RULES "parking").
    parked = []
    if rules.get("parking"):
        pieces = []
        for rid, cls, name, g in roads:
            g = g.intersection(clip)
            pieces += [(rid, cls, name, l) for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", [])))
                       if isinstance(l, LineString) and l.length >= 1]
        parked = place_parked(rules["parking"], pieces, keep_out + landmark_ground + lm_grounds + [p for _, p in yards] + fuel_ground, lap)
        if on_structures:
            # And none where a ramp comes down onto the road (FOOT_CLEAR_M round its foot): its lanes merge there.
            ramp_feet = [Point(p["pts"][i]).buffer(FOOT_CLEAR_M) for p in elev for i in (0, -1) if abs(p["off"][i]) < 0.3]
            kept = [c for c in parked if not under_structure(Point(c["x"], c["z"]).buffer(2.3), on_structures, CAR_TOP_M)
                    and not any(f.contains(Point(c["x"], c["z"])) for f in ramp_feet)]
            under["parked cars"] += len(parked) - len(kept)
            parked = kept

    # Hiding places for the heat chase, off the side streets and away from Oxford Street's dressed block.
    hide_roads = []
    for rid, cls, _, g in roads:
        g = g.intersection(clip)
        hide_roads += [(rid, cls, l) for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", [])))
                       if isinstance(l, LineString) and l.length >= 1]
    if under:
        print("off the structures' roads: " + ", ".join(f"{n} {k}" for k, n in under.items()))
    if lm_match.hits:
        print(f"landmarks the game builds: {len(landmark_ground)} footprint{'s' * (len(landmark_ground) != 1)} left out "
              f"({', '.join(f'{k} x{n}' if n > 1 else k for k, n in sorted(lm_match.hits.items()))})")
    hiding = hiding_places(hide_roads, keep_out + landmark_ground + lm_grounds + [p for _, p in yards] + [poly for poly, off in on_structures if off < 4.0], compound_walls, parked, lights,
                           avoid=oxford.buffer(40) if oxford is not None else None, clip=clip)

    # The named places of the area, where the slice has them (read above, before the buildings).
    spots = []
    if place_rows or osm_rows:
        spots = named_places.select_places(place_rows + osm_rows, inside_clip, to_local_ll)
    else:
        print(f"no {places_path}: no named places (scripts/overture_slice.py export --layers places)")
    if not osm_rows:
        print(f"no {osm_path}: no OpenStreetMap places or garages (scripts/osm_extract.py)")
    garages = find_garages(osm_rows, place_rows, roads, clip, inside_clip, to_local_ll, local)
    if garages:
        print(f"garages: {len(garages)} ({sum(1 for g in garages if g['name'])} named, "
              f"{sum(1 for g in garages if g.get('osm'))} from OpenStreetMap)")
    if fuel:
        print(f"fuel stations: {len(fuel)} ({sum(1 for f in fuel if f.get('osm'))} from OpenStreetMap, "
              f"{sum(1 for f in fuel if f.get('site'))} with their site, {sum(1 for f in fuel if f.get('canopies'))} with a canopy); "
              f"{replaced} canopy footprints left out of the buildings, {cleared} machine-learned footprints cleared off their ground")
    if ev:
        print(f"ev chargers: {len(ev)} ({sum(1 for c in ev if c.get('osm'))} from OpenStreetMap, "
              f"{sum(1 for c in ev if c.get('source') == 'overture')} from Overture, {sum(1 for c in ev if c.get('source') == 'documented')} documented; "
              f"{sum(1 for c in ev if c.get('fuel'))} at a fuel station, {sum(1 for c in ev if c.get('host'))} in a car park or mall)")
    medians = find_medians(roads, clip)
    if medians:
        print(f"medians: {len(medians)} pieces, {sum(LineString(m['pts']).length for m in medians) / 1000:.2f} km")

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, args.name)
    with open(base + ".mtl", "w") as f:
        for name, (r, g, b) in MATERIALS.items():
            f.write(f"newmtl {name}\nKd {r:.3f} {g:.3f} {b:.3f}\nKs 0 0 0\nNs 10\nd 1\nillum 1\n\n")
    verts = mesh.write(base + ".obj", os.path.basename(base) + ".mtl")
    tris, objs = mesh.stats()
    meta = {
        "name": args.name,
        "bounds_wgs84": ({k: area[k] for k in ("south", "north", "west", "east")} if "corridor" not in area else
                         dict(zip(("west", "south", "east", "north"), (round(v, 5) for v in lonlat(clip).bounds)))),
        # A corridor's outline (one ring per piece, the boxes it leaves out may cut it in two), and the areas it meets
        # along their edges.
        **({"outline_m": [[[round(x, 1), round(y, 1)] for x, y in p.exterior.coords]
                          for p in getattr(clip.simplify(5.0), "geoms", [clip.simplify(5.0)]) if isinstance(p, Polygon)],
            "joins": area.get("minus", [])} if "corridor" in area else {}),
        "origin_wgs84": {"lat": area["origin"][0], "lon": area["origin"][1]},
        "origin_utm30n": {"e": round(oe, 2), "n": round(on, 2)},
        "offset_from_world_origin_m": {"x": round(oe - WORLD_ORIGIN_UTM[0], 2), "z": round(on - WORLD_ORIGIN_UTM[1], 2)},
        "bounds_m": {"xmin": round(minx, 1), "xmax": round(maxx, 1), "zmin": round(miny, 1), "zmax": round(maxy, 1)},
        "axes": "x east, y up, z north, metres from origin",
        "stats": {"buildings": nb, "road_km": round(road_km, 2), "lights": len(lights),
                  **({"parked": len(parked)} if rules.get("parking") else {}), "hiding": len(hiding), "places": len(spots), "fuel_stations": len(fuel), "ev_chargers": len(ev), "canopies_replaced": replaced, "forecourts_cleared": cleared, "medians_m": round(sum(LineString(m["pts"]).length for m in medians)), "triangles": tris,
                  "vertices": verts, "objects": objs, "lap_m": round(lap["length_m"], 1) if lap else None,
                  "height_m_median": round(float(np.median(heights)), 1) if heights else None,
                  "massing": {k: (round(v) if isinstance(v, float) else v) for k, v in sorted(counts.items())}},
        "lights": lights,
        "lap": lap,
        "signs": [{"name": n, "x": round(float(e - oe), 2), "z": round(float(nn - on), 2), "bearing": b}
                  for n, lat, lon, b in rules.get("signs", []) for e, nn in [to_utm.transform(lon, lat)]],
        # Only districts with a parking rule carry the key, so Osu's file is unchanged.
        **({"parked": parked} if parked else {}),
        # The drivable network for the game's traffic (circle-game src/vehicles/traffic.ts).
        "roads": traffic_network(roads, clip, ONE_WAY_ROADS),
        # What the game's cars collide with (circle-game src/core/scene.ts addSolid).
        "solid": solid_outlines(keep_out + [p for _, p in yards], compound_walls,
                                [(cls, g, road_width(cls, name, rid=rid)) for rid, cls, name, g in roads], gates=compound_gates),
        # Where the game's hiding places stand (circle-game src/world/hiding.ts).
        "hiding": hiding,
        # The real names the typologies' buildings carry (typology.py): churches and mosques, storey buildings' name
        # bands; from OpenStreetMap and Overture, as text only.
        **({"names": names} if names else {}),
        # The named places the game's maps show, best first (scripts/places.py; circle-game src/ui/minimap-places.ts).
        "places": spots,
        # The central reservations of the dual carriageways, cut at the crossings (find_medians()).
        "medians": medians,
        # Real garages, for the game's fitters (find_garages(); circle-game reads SceneData.garages).
        "garages": garages,
        # The fuel stations (scripts/fuel_stations.py; circle-game src/world/fuel.ts builds their forecourts).
        **({"fuelStations": fuel} if fuel else {}),
        # The electric-vehicle chargers (scripts/ev_chargers.py; circle-game src/world/ev.ts builds their bays).
        **({"evChargers": ev} if ev else {}),
        # The interchanges', flyovers' and footbridges' pieces in the area (scripts/elevated.py ids): relief.py gives
        # them their heights and the game builds them (circle-game src/world/interchanges.ts).
        **({"structures": [p["id"] for p in elev] + [f["id"] for f in feet]} if elev or feet else {}),
        # The OpenStreetMap ids of the landmarks the game builds whose footprints this area left out
        # (landmark_footprints.py), so the game need not mask them.
        **({"landmarksLeftOut": sorted(lm_match.hits)} if lm_match.hits else {}),
        "credits": "Buildings and roads: Overture Maps Foundation (ODbL), from OpenStreetMap contributors, "
                   "Google Open Buildings and Microsoft. Heights from typology rules."
                   + (" Places, garages, fuel stations and chargers: OpenStreetMap contributors (ODbL)." if osm_rows else "")
                   + (" Places: Overture Maps Foundation (CDLA Permissive 2.0 and ODbL), from Meta, Microsoft, "
                      "Foursquare, AllThePlaces and OpenStreetMap contributors." if spots else ""),
    }
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    if avenue_rows:
        # The avenue kit's record: one line per footprint of the corridor, its typology or "generic", for the plan.
        with open(os.path.join(args.out, "avenue-kit.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(avenue_rows[0]))
            w.writeheader()
            w.writerows(avenue_rows)
        by = Counter(r["kind"] for r in avenue_rows)
        print(f"avenue kit: {len(avenue_rows)} footprints in the corridor: "
              + ", ".join(f"{k} {by[k]}" for k in sorted(by, key=lambda k: (k[0] != "T", len(k), k))))
    preview(base + "-preview.png", roads, clip, mesh, lights, lap)
    print(json.dumps(meta["stats"]))
    if rules.get("parking"):
        mix = Counter(c["colour"] for c in parked)
        print(f"parked: {len(parked)} places, " + ", ".join(f"{n} {k}" for k, n in mix.most_common()))


def find_garages(osm_rows, place_rows, roads, clip, inside, to_local, local, merge_m=80.0, twin_m=15.0):
    """The real garages of the area, for the game's fitters: OpenStreetMap's car repair shops, mechanics, tyre and
    parts shops (places.GARAGE_KIND), named or not, and Overture's of the same kinds above its confidence floor; one
    garage where both map the same one (the same name within merge_m, or any two within twin_m, OpenStreetMap's kept).
    Each: name (places.garage_name(): "" when it has none fit to show), kind (repair, tyres, parts), x and z (its
    outline's centre or its pin, local metres, z north), door (the edge of the nearest drivable road's asphalt, on the
    garage's side), toward (unit vector from the garage to the door), road (name, width, unit direction along it),
    outline (local metres) when OpenStreetMap maps the building, osm id or "source": "overture", brand when a brand is
    named (cleared before a public release, circle-bible DECISIONS.md), person when the name names a private person
    (shown in the proof of concept, dropped by a public build: places.PUBLIC)."""
    cands = []
    for r in osm_rows:
        if r.get("garage"):
            g = shapely.from_wkb(r["outline"]) if r.get("outline") else None
            cands.append({"kind": r["garage"], "name": named_places.garage_name(r["name"]), "raw": r["name"] or "",
                          "lonlat": (r["lon"], r["lat"]), "outline": g, "osm": r["id"], "brand": bool(r.get("brand"))})
            cands[-1]["person"] = named_places.is_person(cands[-1]["name"]) and not cands[-1]["brand"]
    for r in place_rows:
        kind = named_places.GARAGE_KIND_OVERTURE.get(r.get("category") or "")
        if kind and r["source"] == "places" and float(r.get("confidence") or 0) >= named_places.MIN_CONFIDENCE \
                and (r.get("status") or "").lower() not in named_places.CLOSED:
            cands.append({"kind": kind, "name": named_places.garage_name(r.get("name")), "raw": r.get("name") or "",
                          "lonlat": (r["lon"], r["lat"]), "outline": None, "osm": None, "brand": bool(r.get("brand"))})
            cands[-1]["person"] = named_places.is_person(cands[-1]["name"]) and not cands[-1]["brand"]
    lines = []
    for rid, cls, name, g in roads:
        if cls not in TRAFFIC_CLASSES:
            continue
        g = g.intersection(clip)
        for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))):
            if isinstance(l, LineString) and l.length >= 2:
                lines.append((name, road_width(cls, name, rid=rid), l))
    if not lines:
        return []
    tree = shapely.STRtree([l for _, _, l in lines])
    kept = []
    for c in cands:
        if c["outline"] is not None:
            poly = local(c["outline"])
            x, z = poly.centroid.x, poly.centroid.y
        else:
            poly = None
            x, z = to_local(*c["lonlat"])
        if not inside(x, z):
            continue
        twin = next((k for k in kept if math.hypot(k["x"] - x, k["z"] - z) <= twin_m or
                     (c["raw"] and k["raw"] and math.hypot(k["x"] - x, k["z"] - z) <= merge_m
                      and named_places.same_name(k["raw"], c["raw"]))), None)
        if twin:
            if not twin["name"] and c["name"]:
                twin["name"], twin["person"] = c["name"], c["person"]
            twin["brand"] = twin["brand"] or c["brand"]
            continue
        p = Point(x, z)
        name, w, line = lines[int(tree.nearest(p))]
        at = line.project(p)
        q = line.interpolate(at)
        a, b = line.interpolate(max(0.0, at - 2.0)), line.interpolate(min(line.length, at + 2.0))
        d = np.array([b.x - a.x, b.y - a.y]); d /= max(1e-6, np.hypot(*d))
        to = np.array([x - q.x, z - q.y]); dist = np.hypot(*to)
        side = to / dist if dist > 1e-6 else np.array([-d[1], d[0]])
        door = np.array([q.x, q.y]) + side * min(w / 2, dist)
        toward = door - (x, z); toward = toward / max(1e-6, np.hypot(*toward))
        kept.append({"kind": c["kind"], "name": c["name"], "raw": c["raw"], "x": x, "z": z, "door": door, "toward": toward,
                     "road": {"name": name, "w": w, "dir": d}, "poly": poly, "osm": c["osm"], "brand": c["brand"],
                     "person": c["person"]})
    out = []
    for k in kept:
        e = {"name": k["name"], "kind": k["kind"], "x": round(float(k["x"]), 1), "z": round(float(k["z"]), 1),
             "door": [round(float(v), 1) for v in k["door"]], "toward": [round(float(v), 4) for v in k["toward"]],
             "road": {"name": k["road"]["name"], "w": k["road"]["w"], "dir": [round(float(v), 4) for v in k["road"]["dir"]]}}
        if k["poly"] is not None and isinstance(k["poly"], Polygon):
            e["outline"] = [[round(px, 1), round(pz, 1)] for px, pz in k["poly"].simplify(0.3).exterior.coords[:-1]]
        e.update({"osm": k["osm"]} if k["osm"] else {"source": "overture"})
        if k["brand"]:
            e["brand"] = True
        if k["person"]:
            e["person"] = True
        out.append(e)
    return out


def lane_lines(w, n):
    """Offsets (m, left of the line positive) of the dashed lines between `n` lanes on asphalt `w` wide."""
    n = max(1, int(n))
    inner = w - EDGE_M
    return [round(-inner / 2 + k * inner / n, 3) for k in range(1, n)]


def ribbon(mesh, line, w, lift, mat, prefix):
    pts = np.array(line.coords)
    if len(pts) < 2:
        return
    d = np.diff(pts, axis=0)
    seg_len = np.linalg.norm(d, axis=1)
    keep = np.concatenate([[True], seg_len > 1e-3])
    pts = pts[keep]
    if len(pts) < 2:
        return
    d = np.diff(pts, axis=0)
    t = d / np.linalg.norm(d, axis=1)[:, None]
    nrm = np.column_stack([-t[:, 1], t[:, 0]])  # left normal
    vn = np.zeros_like(pts)
    vn[0], vn[-1] = nrm[0], nrm[-1]
    for i in range(1, len(pts) - 1):
        m = nrm[i - 1] + nrm[i]
        ml = np.linalg.norm(m)
        if ml < 1e-6:
            vn[i] = nrm[i]
            continue
        m /= ml
        cos = max(float(np.dot(m, nrm[i])), 0.5)  # cap the mitre at 2x
        vn[i] = m / cos
    left = pts + vn * w / 2
    right = pts - vn * w / 2
    along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    for i in range(len(pts) - 1):
        mid = (pts[i] + pts[i + 1]) / 2
        obj = cell_name(prefix, mid[0], mid[1])
        r0, r1, l1, l0 = right[i], right[i + 1], left[i + 1], left[i]
        mesh.quad(obj, mat, (*r0, lift), (*r1, lift), (*l1, lift), (*l0, lift),
                  uv=((0, along[i] / 4), (0, along[i + 1] / 4), (w / 4, along[i + 1] / 4), (w / 4, along[i] / 4)))


# Street lights on the lap, from circle-reference/catalogue/street-survey.md (Street View, Aug 2024):
#   - Oxford Street: one side only (east), poles 39, 142 and 84 m apart (mean 88 m), never alternating sides;
#   - Ring Road East and Danquah Circle: about 40 m apart (twin-arm poles in the median where the road is split);
#   - every head on the lap is white LED: the 2020-21 programme swapped the sodium heads on the same poles.
# Sodium survives only off the lap, on other main roads.
LAP_ROADS = ("Oxford Street", "Ring Road East", "Danquah Circle")
OXFORD_GAP_M = (35.0, 145.0)
RING_STEP_M = 40.0


# Main roads off the lap whose heads were all seen as white LED by day (circle-reference observations.csv,
# obs-accra-lights-heads-main-001, Aug-Oct 2024): they never draw sodium.
LED_ROADS = ("Cantonments Road", "Independence Avenue", "Ring Road Central", "Castle Road", "Liberation Road",
             "Kojo Thompson Road", "Kinbu Road")
# Roads lit by solar all-in-one poles: a panel on top of the pole, the LED head on a curved arm below it (3 of 3 heads
# seen on Cantonments Road, Street View Aug 2024). The game draws the panel from the lamp's "pole" field.
SOLAR_ROADS = ("Cantonments Road",)


def lamp_kind(rid, stretch, k, on_lap, name=""):
    """White LED everywhere on the lap (80% neutral, 20% cold); off the lap 10% of stretches keep sodium, the rest
    as on the lap. circle-reference counted 1 sodium-era head of 14 off the lap (Street View, Aug-Oct 2024; the
    2020-21 programme put LED on the main roads), so sodium stays a minority kept for the mix (decision of
    14 Nov 2026). Dead poles: whole dark stretches (about one in eight), and off the lap about one pole in four more.
    The city is darker than that (most poles dark on main roads in Oct 2024), but the game's nights stay lighter
    for comfort of play (decision of 14 Nov 2026)."""
    r = unit_hash(rid, stretch, "kind")
    kind = "led_cool" if r >= 0.8 else "led"
    if not on_lap and 0.7 <= r < 0.8 and name not in LED_ROADS:
        kind = "sodium"
    if unit_hash(rid, stretch) < 0.12 or (not on_lap and unit_hash(rid, "dead", k) < 0.25):
        kind = "dead"
    return kind


def light_spots(rid, name, length):
    """Distances along the road where poles stand, and the side rule for each road."""
    if name == "Oxford Street":
        out, d, k = [], 12.0, 0
        while d < length:
            out.append(d)
            u = unit_hash(rid, "gap", k)
            d += OXFORD_GAP_M[0] + (OXFORD_GAP_M[1] - OXFORD_GAP_M[0]) * u ** 1.2
            k += 1
        return out, "east"
    if name in LAP_ROADS:
        return list(np.arange(RING_STEP_M / 2, length, RING_STEP_M)), "left"
    return list(np.arange(16.0, length, 32.0)), "alternate"


def median_width(p, left, w, rid, carriageways, reach=25.0):
    """Centre to centre (m) from a point on a carriageway's centreline to the other carriageway across the median, to
    its left (the median side: Ghana drives on the right), or None when no other carriageway lies within `reach`."""
    a = p + left * (w / 2)
    probe = LineString([a, p + left * reach])
    best = None
    for other, g in carriageways:
        if other == rid or not probe.intersects(g):
            continue
        x = probe.intersection(g)
        for part in getattr(x, "geoms", [x]):
            if part.is_empty:
                continue
            d = float(Point(p).distance(part))
            if best is None or d < best:
                best = d
    return best


def place_lights(rid, cls, name, line, w, carriageways=()):
    """Poles along major roads, following the street survey on the lap (see LAP_ROADS) and, elsewhere, one pole
    every 32 m on alternating sides, so the night has dark gaps between pools. On a dual carriageway
    (DUAL_CARRIAGEWAYS, with the other carriageways in `carriageways`: (road id, line)) a pole stands in the middle
    of the median with a head over each carriageway, a twin-arm pole (two lamps on one foot sharing a "twin" key);
    where the median is under 2 m, on the outer verge."""
    out = []
    on_lap = name in LAP_ROADS
    spots, rule = light_spots(rid, name, line.length)
    step = 88.0 if name == "Oxford Street" else (RING_STEP_M if on_lap else 32.0)
    for k, d in enumerate(spots):
        stretch = int(d // (step * 6))
        kind = lamp_kind(rid, stretch, k, on_lap, name)
        p = np.array(line.interpolate(d).coords[0])
        q = np.array(line.interpolate(min(d + 1.0, line.length)).coords[0])
        if np.linalg.norm(q - p) < 1e-6:
            continue
        t = (q - p) / np.linalg.norm(q - p)
        left = np.array([-t[1], t[0]])
        if (name in DUAL_CARRIAGEWAYS or rid in CARRIAGEWAY_W) and carriageways:
            across = median_width(p, left, w, rid, carriageways)
            if across is not None and across - w >= 2.0:
                foot = p + left * (across / 2)
                for face in (-left, left):
                    lamp = {"x": round(float(foot[0]), 2), "y": 0.0, "z": round(float(foot[1]), 2),
                            "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                            "kind": kind, "road": name or cls, "twin": f"{rid}:{k}"}
                    out.append(lamp)
                continue
            if across is not None:
                # No room in the median: the outer verge, the arm over this carriageway.
                pos = p - left * (w / 2 + 1.2)
                face = left
                out.append({"x": round(float(pos[0]), 2), "y": 0.0, "z": round(float(pos[1]), 2),
                            "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                            "kind": kind, "road": name or cls})
                continue
        if rule == "east":
            s = 1 if left[0] >= 0 else -1
        elif rule == "left":
            s = 1
        else:
            s = 1 if k % 2 == 0 else -1
        pos = p + left * s * (w / 2 + 1.2)
        face = -left * s  # the lamp arm points over the road
        lamp = {"x": round(float(pos[0]), 2), "y": 0.0, "z": round(float(pos[1]), 2),
                "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                "kind": kind, "road": name or cls}
        if name in SOLAR_ROADS:
            lamp["pole"] = "solar"
        out.append(lamp)
    return out


def off_asphalt(lights, roads, margin=0.3, back=8.0, step=0.5):
    """No pole on a carriageway: where several roads meet or run side by side, one road's pole can land on another's
    asphalt (the user's test drive of 14 Nov 2026 saw poles standing on the Ring Road). roads: (line, width). A pole
    within `margin` m of any carriageway moves back, away from the road its arm reaches over, up to `back` m, until it
    is clear; if it cannot, it goes. A twin-arm pole in a median has nowhere to move: it goes whole.
    Returns (lights, moved, dropped)."""
    if not lights or not roads:
        return lights, 0, 0
    asphalt = shapely.STRtree([line.buffer(w / 2 + margin) for line, w in roads])
    on = lambda x, z: len(asphalt.query(Point(x, z), predicate="intersects")) > 0
    out, moved, dropped, gone = [], 0, 0, set()
    for l in lights:
        if not on(l["x"], l["z"]):
            out.append(l)
            continue
        if "twin" in l:
            gone.add(l["twin"])
            dropped += 1
            continue
        yaw = math.radians(l["yaw"])
        fx, fz = math.sin(yaw), math.cos(yaw)       # the way the arm reaches, over the road
        for d in np.arange(step, back + 1e-6, step):
            x, z = l["x"] - fx * d, l["z"] - fz * d
            if not on(x, z):
                out.append({**l, "x": round(float(x), 2), "z": round(float(z), 2)})
                moved += 1
                break
        else:
            dropped += 1
    # A twin pole goes whole: its other head too, wherever it came in the list.
    kept = [l for l in out if l.get("twin") not in gone]
    return kept, moved, dropped + (len(out) - len(kept))


def dedupe(lights, min_gap):
    """Lamps at least `min_gap` m from every pole kept before them; the heads of a twin-arm pole (one "twin" key) are
    kept or dropped together."""
    kept, poles, twins = [], [], set()
    for l in lights:
        tw = l.get("twin")
        if tw is not None and tw in twins:
            kept.append(l)
            continue
        if all((l["x"] - x) ** 2 + (l["z"] - z) ** 2 >= min_gap ** 2 for x, z in poles):
            kept.append(l)
            poles.append((l["x"], l["z"]))
            if tw is not None:
                twins.add(tw)
    return kept


# The game's parked car, width and length in metres (circle-game src/render/city.ts, a 1.74 x 4.3 m body).
CAR_BODY_M = (1.74, 4.3)


def junctions(lines, tol=0.05):
    """Points where three road arms or more meet: three segment ends, a segment ending on another, two crossing.
    Overture segments often run on through a junction, so their ends alone do not find them all; two segments
    meeting end to end (one street continued) are no junction."""
    tree = shapely.STRtree(lines)
    found = {}
    for i, a in enumerate(lines):
        for j in tree.query(a):
            if j <= i:
                continue
            x = a.intersection(lines[j])
            for part in getattr(x, "geoms", [x]):
                if part.is_empty:
                    continue
                # Two segments sharing a stretch meet at its ends.
                ends = [part] if part.geom_type == "Point" else [Point(part.coords[0]), Point(part.coords[-1])]
                for p in ends:
                    found.setdefault((round(p.x, 1), round(p.y, 1)), p)
    out = []
    for p in found.values():
        arms = 0
        for k in tree.query(p, predicate="dwithin", distance=tol):
            l = lines[k]
            at_end = min(p.distance(Point(l.coords[0])), p.distance(Point(l.coords[-1]))) <= tol
            arms += 1 if at_end and not l.is_closed else 2
        if arms >= 3:
            out.append(p)
    return out


TRAFFIC_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified")


def solid_outlines(footprints, walls, roads=(), tolerance=0.5, kerb=0.3, gates=()):
    """What the game's cars collide with: the outline of every building (look-dev ones and landmarks included) and
    the compound walls with their closed gates, simplified to `tolerance` metres and rounded to 0.1 m. Each outline
    or wall is a flat list x0, z0, x1, z1, ...; an outline is a closed ring without its first point repeated.

    roads: (class, line) or (class, line, width) of the drivable network (the width by class when not given). Mapped footprints sometimes reach onto the carriageway (kiosks,
    canopies, a misplaced centreline: 23 outlines in Osu and 57 in Jamestown lie within 1 m of one), which would
    block the street the traffic and the lap drive. The part of an outline that lies on the carriageway, up to `kerb`
    metres short of its edge, is left out, and so is what remains of the outline if under 4 m2.

    gates: (x, y) of the compound walls' gates (massing.compounds), carried as "gates", each [x, z] rounded to 0.1 m,
    when there are any: the game plants its shrubs either side of them (circle-game src/world/vegetation.ts)."""
    carriage = None
    # Round ends, so the corners of the junctions, where the cars cut across, are kept clear too.
    lanes = [r[1].buffer(max(0.5, (r[2] if len(r) > 2 else ROAD_WIDTH[r[0]]) / 2 - kerb))
             for r in roads if r[0] in TRAFFIC_CLASSES]
    if lanes:
        carriage = shapely.union_all(lanes)
    rings, lines = [], []
    for p in footprints:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        if carriage is not None and p.intersects(carriage):
            p = p.difference(carriage)
        for q in (list(p.geoms) if hasattr(p, "geoms") else [p]):
            if not isinstance(q, Polygon) or q.is_empty:
                continue
            q = q.simplify(tolerance)
            if not isinstance(q, Polygon) or q.is_empty or q.area < 4:
                continue
            pts = list(q.exterior.coords)[:-1]
            if len(pts) >= 3:
                rings.append([round(float(c), 1) for xy in pts for c in xy[:2]])
    for w in walls:
        if carriage is not None and w.intersects(carriage):
            w = w.difference(carriage)
        for piece in (list(w.geoms) if hasattr(w, "geoms") else [w]):
            piece = piece.simplify(tolerance)
            if isinstance(piece, LineString) and piece.length >= 1:
                lines.append([round(float(c), 1) for xy in piece.coords for c in xy[:2]])
    out = {"buildings": rings, "walls": lines}
    if gates:
        out["gates"] = [[round(float(x), 1) + 0.0, round(float(y), 1) + 0.0] for x, y in gates]
    return out


# Hiding places (circle-bible bible/game-design.md, heat level 3: "a trotro station, car wash, market shed, a crew
# garage"): open sheds on free plots beside the side streets, where the game lets the heat cool. They are invented
# places, unnamed, on ground the map leaves empty; the game builds the shed (circle-game src/world/hiding.ts).
HIDING_KINDS = ("car-wash", "market-shed", "crew-garage")
HIDING_CLASSES = ("tertiary", "residential", "unclassified")
HIDING_SIZE_M = (9.0, 11.0)          # along the street, and deep
HIDING_SPACING_M = 200.0


def hiding_places(roads, footprints, walls, parked=(), lights=(), avoid=None, clip=None, step=6.0,
                  size=HIDING_SIZE_M, setback=1.2, spacing=HIDING_SPACING_M, widths=ROAD_WIDTH):
    """Free plots beside the side streets for the game's hiding places, at least `spacing` metres apart.

    roads: (id, class, line) in metres; a place stands beside a road of HIDING_CLASSES, its open front `setback`
    metres back from the carriageway and facing it, `size` = (width along the street, depth). The plot, with the strip
    between it and the carriageway, must be clear of footprints (0.5 m), walls (0.3 m), every carriageway (1 m), parked
    cars (2.5 m) and street-light poles (1 m), inside `clip`, and outside `avoid` (a geometry, such as the dressed
    block's street). Candidates every `step` metres on both sides are taken in a hashed order, so reruns give the same
    places, and each new one keeps `spacing` from those taken. Returns [{"kind", "x", "z", "yaw", "w", "d"}] in builder
    axes (z north), yaw in degrees clockwise from north: the way the open front faces."""
    w_along, depth = size
    blockers = [p.buffer(0.5) for p in footprints if not p.is_empty]
    blockers += [w.buffer(0.3) for w in walls if not w.is_empty]
    blockers += [Point(c["x"], c["z"]).buffer(2.5) for c in parked]
    blockers += [Point(l["x"], l["z"]).buffer(1.0) for l in lights]
    btree = shapely.STRtree(blockers) if blockers else None
    lanes = [l.buffer(widths[c] / 2 + 1.0) for _, c, l in roads if c in TRAFFIC_CLASSES]
    ltree = shapely.STRtree(lanes) if lanes else None
    candidates = []
    for rid, cls, line in roads:
        if cls not in HIDING_CLASSES or line.length < w_along + 2 * step:
            continue
        half_w = widths[cls] / 2
        for k, d in enumerate(np.arange(w_along / 2 + step, line.length - w_along / 2 - step + 1e-6, step)):
            a = np.array(line.interpolate(d - w_along / 2).coords[0])
            b = np.array(line.interpolate(d + w_along / 2).coords[0])
            t = b - a
            if np.linalg.norm(t) < w_along * 0.95:
                continue                                     # the street bends here
            t = t / np.linalg.norm(t)
            left = np.array([-t[1], t[0]])
            c = np.array(line.interpolate(d).coords[0])
            for side in (1, -1):
                n = left * side
                front = c + n * (half_w + setback)
                centre = front + n * depth / 2
                corners = [front - t * w_along / 2, front + t * w_along / 2, front + t * w_along / 2 + n * depth,
                           front - t * w_along / 2 + n * depth]
                plot = Polygon([tuple(p) for p in corners])
                # The way in: from the carriageway's edge to the plot's front.
                way_in = Polygon([tuple(p) for p in (c + n * half_w - t * w_along / 2, c + n * half_w + t * w_along / 2,
                                                     front + t * w_along / 2, front - t * w_along / 2)])
                candidates.append((unit_hash(rid, "hide", k, side), plot, way_in, centre, -n))
    candidates.sort(key=lambda x: x[0])
    taken = []
    for h, plot, way_in, centre, face in candidates:
        if any(math.hypot(centre[0] - o["x"], centre[1] - o["z"]) < spacing for o in taken):
            continue
        if clip is not None and not clip.contains(plot):
            continue
        if avoid is not None and plot.intersects(avoid):
            continue
        if btree is not None and len(btree.query(plot.union(way_in), predicate="intersects")):
            continue
        if ltree is not None and len(ltree.query(plot, predicate="intersects")):
            continue
        taken.append({"kind": HIDING_KINDS[len(taken) % len(HIDING_KINDS)], "x": round(float(centre[0]), 2),
                      "z": round(float(centre[1]), 2), "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                      "w": w_along, "d": depth})
    return taken


def traffic_network(roads, clip, one_way=()):
    """The drivable roads as a network for the game's traffic: every road of a traffic class inside the district,
    noded at every crossing (Overture segments often run straight through junctions), each piece with the class,
    width and name of the road it lies on, simplified to 0.5 m. Points are x east, z north, as the rest of the file.
    A piece of a road named in one_way is driven one way only: it runs the way its road's geometry runs and carries
    "oneway": true (the Ring Road's carriageways and Danquah Circle, ONE_WAY_ROADS, and every carriageway
    find_carriageways() found)."""
    src = []
    for rid, cls, name, g in roads:
        if cls not in TRAFFIC_CLASSES:
            continue
        g = g.intersection(clip)
        for line in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))):
            if isinstance(line, LineString) and line.length >= 2:
                src.append((cls, name, line, rid))
    if not src:
        return []
    tree = shapely.STRtree([l for _, _, l, _ in src])
    noded = shapely.union_all([l for _, _, l, _ in src])
    pieces = [noded] if isinstance(noded, LineString) else list(getattr(noded, "geoms", []))
    out = []
    for piece in pieces:
        if not isinstance(piece, LineString) or piece.length < 1.0:
            continue
        mid = piece.interpolate(0.5, normalized=True)
        cls, name, line, rid = src[int(tree.nearest(mid))]
        pts = list(piece.simplify(0.5).coords)
        entry = {"cls": cls, "w": road_width(cls, name, rid=rid), "name": name}
        if rid in LANES:
            entry["lanes"] = lanes_of(rid, cls, name)
        if name in one_way or rid in CARRIAGEWAY_W:
            if line.project(Point(pts[0])) > line.project(Point(pts[-1])):
                pts = pts[::-1]
            entry["oneway"] = True
        entry["pts"] = [round(float(c), 1) for xy in pts for c in xy[:2]]
        out.append(entry)
    return out


def place_parked(rule, pieces, buildings, lap, widths=ROAD_WIDTH):
    """Cars parked parallel to the road edge, for a district without a dressed block (DISTRICT_RULES "parking").

    pieces: (road id, class, name, line) of every road in the district, clipped to it, in metres; the rule's classes
    and streets are parked on, and all of them make the junctions. buildings: the footprints the cars keep clear of.
    lap: benchmark_lap()'s dict, or None for no limit around the lap. Every place is a station along the road, one
    each step_m from half a step in, on each side; it is taken when its hash falls under the street's chance and it
    clears the junctions, the buildings (the car's long axis, rule "building_m" away), the spawn and the cars already
    placed. A car also stays off every other road's asphalt (its centre half a car's width outside that carriageway),
    which keeps it out of the side street at a sharp junction and off a road running alongside. The heading is
    along the road, either way. Returns [{"x", "z", "y", "yaw", "colour"}] in builder axes, yaw in degrees
    clockwise from north, as build_lookdev.py writes its parked cars."""
    if not pieces:
        return []
    lines = [l for _, _, _, l in pieces]
    jn = junctions(lines)
    jtree = shapely.STRtree(jn) if jn else None
    btree = shapely.STRtree(buildings) if buildings else None
    # Other roads' carriageways, with flat ends so a street's next segment does not stop the cars on this one.
    bands = shapely.STRtree([l.buffer(road_width(c, n, widths, r) / 2 + CAR_BODY_M[0] / 2, cap_style="flat")
                             for r, c, n, l in pieces])
    lap_line = None
    if lap and lap.get("path") and rule.get("lap_m"):
        lap_line = LineString(list(zip(lap["path"][0::2], lap["path"][1::2])))
    spawn = Point(lap["spawn"]["x"], lap["spawn"]["z"]) if lap and lap.get("spawn") else None
    gap, half = rule["gap_m"], CAR_BODY_M[1] / 2
    grid, out = {}, []
    seen = Counter()

    def clear_of_cars(c):
        gx, gz = int(math.floor(c[0] / gap)), int(math.floor(c[1] / gap))
        return all((c[0] - o[0]) ** 2 + (c[1] - o[1]) ** 2 >= gap ** 2
                   for dx in (-1, 0, 1) for dz in (-1, 0, 1) for o in grid.get((gx + dx, gz + dz), []))

    for i, (rid, cls, name, line) in enumerate(pieces):
        piece = seen[rid]            # a road cut in several pieces by the district's edge
        seen[rid] += 1
        take = rule.get("streets", {}).get(name, rule["take"] if cls in rule["classes"] else 0.0)
        if take <= 0 or line.length < rule["step_m"]:
            continue
        w = road_width(cls, name, widths, rid)
        off = w / 2 - (rule["inset_m"] if w < rule["narrow_m"] else rule["wide_inset_m"])
        for k, d in enumerate(np.arange(rule["step_m"] / 2, line.length - rule["step_m"] / 2 + 1e-6, rule["step_m"])):
            p = np.array(line.interpolate(d).coords[0])
            t = np.subtract(line.interpolate(min(d + 1.0, line.length)).coords[0], line.interpolate(max(d - 1.0, 0.0)).coords[0])
            if np.linalg.norm(t) < 1e-6:
                continue
            t = t / np.linalg.norm(t)
            left = np.array([-t[1], t[0]])
            for side in (1, -1):
                if unit_hash(rid, "park", piece, side, k) >= take:
                    continue
                c = p + left * side * off
                pt = Point(c)
                if lap_line is not None and pt.distance(lap_line) > rule["lap_m"]:
                    continue
                if spawn is not None and pt.distance(spawn) < rule["spawn_m"]:
                    continue
                if jtree is not None and len(jtree.query(pt, predicate="dwithin", distance=rule["junction_m"])):
                    continue
                axis = LineString([c - t * half, c + t * half])
                if btree is not None and len(btree.query(axis, predicate="dwithin", distance=rule["building_m"])):
                    continue
                if any(j != i for j in bands.query(pt, predicate="intersects")):
                    continue
                if not clear_of_cars(c):
                    continue
                grid.setdefault((int(math.floor(c[0] / gap)), int(math.floor(c[1] / gap))), []).append(c)
                face = t if unit_hash(rid, "parkdir", piece, side, k) < 0.5 else -t
                out.append({"x": round(float(c[0]), 2), "z": round(float(c[1]), 2), "y": 0.0,
                            "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                            "colour": massing.pick(rule["colours"], unit_hash(rid, "parkcol", piece, side, k))})
    return out


def benchmark_lap(roads, clip, lap_spec, to_utm, origin):
    """A loop for the scripted lap: Danquah Circle down Oxford Street, back up a parallel route."""
    street, south_lat = lap_spec
    G = nx.Graph()
    key = lambda p: (round(p[0], 1), round(p[1], 1))
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        lines = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
        for line in lines:
            if not isinstance(line, LineString) or line.length < 1 or cls == "service":
                continue
            a, b = key(line.coords[0]), key(line.coords[-1])
            if a == b:
                continue
            if G.has_edge(a, b) and G[a][b]["length"] <= line.length:
                continue
            G.add_edge(a, b, length=line.length, name=name, coords=list(line.coords))
    if G.number_of_nodes() == 0:
        return None
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    ox_nodes = [n for u, v, d in G.edges(data=True) if d["name"] == street for n in (u, v)]
    if not ox_nodes:
        return None
    _, south_n = to_utm.transform(-0.1825, south_lat)
    south_n -= origin[1]
    start = min(ox_nodes, key=lambda p: p[0] ** 2 + p[1] ** 2)  # nearest the origin, Danquah Circle
    turn = min(ox_nodes, key=lambda p: abs(p[1] - south_n))
    go = nx.shortest_path(G, start, turn, weight="length")
    used = {frozenset(e) for e in zip(go, go[1:])}
    back = nx.shortest_path(G, turn, start, weight=lambda u, v, d: d["length"] * (25 if frozenset((u, v)) in used else 1))
    pts = []
    for u, v in list(zip(go, go[1:])) + list(zip(back, back[1:])):
        c = G[u][v]["coords"]
        if key(c[0]) != u:
            c = c[::-1]
        pts.extend(c if not pts else c[1:])
    line = LineString(pts)
    wp = [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 8.0)]
    names = []
    for u, v in list(zip(go, go[1:])) + list(zip(back, back[1:])):
        n = G[u][v]["name"]
        if n and (not names or names[-1] != n):
            names.append(n)
    p0, p1 = wp[0], wp[1]
    return {"length_m": line.length, "via": names,
            "spawn": {"x": round(p0[0], 2), "y": 0.5, "z": round(p0[1], 2),
                      "yaw": round(math.degrees(math.atan2(p1[0] - p0[0], p1[1] - p0[1])), 1)},
            "path": [round(v, 2) for x, z in wp for v in (x, z)]}


def split_at_shared_vertices(lines, ndigits=2):
    """Cuts each line at its inner vertices that another line also passes through: the junctions mapped as shared
    nodes, where an Overture segment often runs on. Lines that cross without sharing a vertex stay uncut, so a flyover
    or a bridge over a road never becomes a turn."""
    key = lambda p: (round(p[0], ndigits), round(p[1], ndigits))
    users = Counter()
    for line in lines:
        for k in {key(c) for c in line.coords}:
            users[k] += 1
    out = []
    for line in lines:
        cs = list(line.coords)
        cuts = [0] + [i for i in range(1, len(cs) - 1) if users[key(cs[i])] > 1] + [len(cs) - 1]
        out += [(line, LineString(cs[a:b + 1])) for a, b in zip(cuts, cuts[1:]) if b > a]
    return out


def route_lap(roads, region, spec, to_utm, origin):
    """A closed route through the points of spec["route"] (lat, lon), in order and back to the first: the lap of an
    area that follows a road rather than circling a block (the Ring Road). Roads are cut to the region at their shared
    vertices; the roads named in spec["one_way"] are driven along their geometry only (dual carriageways and
    roundabouts, mapped one way per carriageway; Ghana drives on the right), the others both ways. Each leg is the
    shortest way to the next point, at 25 times the cost on stretches already driven, so the turns at the ends go
    round a block rather than back along the same street (spec["penalty"], 25 by default; 1 drives back the same
    way, for a road with no other). Service roads are left out. Returns benchmark_lap()'s
    dict, with the distance each route point was moved to reach the network ("snapped_m")."""
    one_way = set(spec.get("one_way", ()))
    penalty = spec.get("penalty", 25.0)
    G = nx.DiGraph()
    key = lambda p: (round(p[0], 2), round(p[1], 2))
    src = []
    for rid, cls, name, g in roads:
        if cls == "service":
            continue
        g = g.intersection(region)
        src += [(name, l, rid in CARRIAGEWAY_W) for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", [])))
                if isinstance(l, LineString) and l.length >= 1]
    # By piece: its name, and whether it is driven one way (named in spec["one_way"], or a carriageway
    # find_carriageways() found).
    names = {id(l): (n, n in one_way or dual) for n, l, dual in src}

    def add(a, b, coords, length, name):
        if not G.has_edge(a, b) or G[a][b]["length"] > length:
            G.add_edge(a, b, length=length, name=name, coords=coords)

    for whole, piece in split_at_shared_vertices([l for _, l, _ in src]):
        a, b = key(piece.coords[0]), key(piece.coords[-1])
        if a == b or piece.length < 0.05:
            continue
        name, single = names[id(whole)]
        add(a, b, list(piece.coords), piece.length, name)
        if not single:
            add(b, a, list(piece.coords)[::-1], piece.length, name)
    if G.number_of_nodes() == 0:
        return None
    # Route points snap to the largest strongly connected part of the network: a corridor cut out of the city leaves
    # stubs (a slip road's end, a one-way piece at the edge) from which the rest cannot be reached.
    main = max(nx.strongly_connected_components(G), key=len)
    nodes = np.array([n for n in G.nodes if n in main])
    stops, snapped = [], []
    for lat, lon in spec["route"]:
        e, n = to_utm.transform(lon, lat)
        p = np.array([e - origin[0], n - origin[1]])
        d = np.hypot(*(nodes - p).T)
        stops.append(tuple(nodes[int(np.argmin(d))]))
        snapped.append(round(float(d.min()), 2))
    used = set()
    edges = []
    for s, t in zip(stops, stops[1:] + stops[:1]):
        leg = nx.shortest_path(G, s, t, weight=lambda u, v, d: d["length"] * (penalty if frozenset((u, v)) in used else 1))
        for u, v in zip(leg, leg[1:]):
            used.add(frozenset((u, v)))
            edges.append((u, v))
    pts = []
    for u, v in edges:
        c = G[u][v]["coords"]
        pts.extend(c if not pts else c[1:])
    line = LineString(pts)
    wp = [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 8.0)]
    via = []
    for u, v in edges:
        n = G[u][v]["name"]
        if n and (not via or via[-1] != n):
            via.append(n)
    p0, p1 = wp[0], wp[1]
    return {"length_m": line.length, "via": via, "snapped_m": snapped,
            "spawn": {"x": round(p0[0], 2), "y": 0.5, "z": round(p0[1], 2),
                      "yaw": round(math.degrees(math.atan2(p1[0] - p0[0], p1[1] - p0[1])), 1)},
            "path": [round(v, 2) for x, z in wp for v in (x, z)]}


def preview(path, roads, clip, mesh, lights, lap):
    minx, miny, maxx, maxy = clip.bounds
    s = 1.0
    W, H = int((maxx - minx) * s) + 1, int((maxy - miny) * s) + 1
    img = Image.new("RGB", (W, H), (19, 29, 56))
    dr = ImageDraw.Draw(img)
    P = lambda e, n: ((e - minx) * s, (maxy - n) * s)
    for (obj, mat), tris in mesh.groups.items():
        if mat == "M_Roof":
            for a, b, c, _ in tris:
                shade = int(min(255, 90 + a[2] * 6))
                dr.polygon([P(a[0], a[1]), P(b[0], b[1]), P(c[0], c[1])], fill=(shade, shade, int(shade * 0.95)))
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        for line in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))):
            if isinstance(line, LineString):
                dr.line([P(*c) for c in line.coords], fill=(60, 64, 72), width=max(1, int(road_width(cls, name, rid=rid) * s)))
    if lap:
        wp = list(zip(lap["path"][0::2], lap["path"][1::2]))
        dr.line([P(*c) for c in wp] + [P(*wp[0])], fill=(228, 0, 43), width=3)
    for l in lights:
        x, y = P(l["x"], l["z"])
        col = {"sodium": (255, 194, 13), "led_cool": (200, 225, 255), "dead": (60, 60, 60)}.get(l["kind"], (245, 240, 230))
        dr.ellipse([x - 2, y - 2, x + 2, y + 2], fill=col)
    img.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slice", default="data/work/slice", help="folder from overture_slice.py export")
    ap.add_argument("--name", default="osu-poc", choices=sorted(AREAS))
    ap.add_argument("--out", default="data/work/osu-poc")
    ap.add_argument("--exclude", help="file of building ids to leave out (from build_lookdev.py)")
    ap.add_argument("--typologies", action="store_true",
                    help="the city-wide typologies of typology.py (churches, mosques, towers, flats, storey buildings, "
                         "warehouses and kiosks), paused since 20 Nov 2026: without it every footprint outside the "
                         "avenue's corridor takes the generic modules, as on main")
    ap.add_argument("--avenue-kit", nargs="?", const=avenue_kit.DATA, default=None, metavar="JSON",
                    help="Independence Avenue's kit (avenue_kit.py; circle-game docs/independence-avenue-plan.md, "
                         "stage 2): the typologies T1 to T16 on the corridor's generic buildings, from "
                         "aoi/independence-avenue-kit.json by default; without it they are built as before")
    ap.add_argument("--elevated", default=ELEVATED,
                    help="the interchanges and flyovers (scripts/elevated.py); without the file every road is on the ground")
    ap.add_argument("--no-massing", action="store_true",
                    help="plain extruded footprints, no roofs, bulkheads, frames or compound walls (the old district)")
    ap.add_argument("--no-ev", action="store_true", help="no electric-vehicle chargers (no evChargers), as before")
    ap.add_argument("--ev-documented", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aoi", "ev-chargers.json"),
                    help="the chargers documented by their operators but not mapped, each at a host OpenStreetMap maps; "
                         "'' for OpenStreetMap's and Overture's only")
    ap.add_argument("--roofs", help="imagery.py roofs output: each roof's material as the Maxar 2019 mosaic shows it, "
                    "and its swimming pools (CC BY-NC 4.0: leave it out for a commercial build); without it, the draw")
    ap.add_argument("--no-fuel", action="store_true",
                    help="no fuel stations: their canopies extruded as buildings, walls and parked cars over their ground, as before")
    ap.add_argument("--landmark-footprints", default=landmark_footprints.FOOTPRINTS,
                    help="the outlines of the landmarks the game builds (landmark_footprints.py), left out of the buildings")
    ap.add_argument("--keep-landmarks", action="store_true",
                    help="draw the landmarks' footprints as generic massing and keep the places as the data has them, as before")
    build(ap.parse_args())


if __name__ == "__main__":
    import shapely.ops  # noqa: F401  (substring)
    main()
