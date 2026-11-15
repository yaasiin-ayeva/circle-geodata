# circle-geodata

The open-data pipeline that rebuilds Accra for **CHALE**: roads, buildings, heights and terrain, from sources that allow commercial use. It feeds the browser game in `circle-game` (three.js) and produces the merged map database that the ODbL requires us to publish.

New to the project? Start with circle-bible's [CONTRIBUTING.md](https://github.com/yaasiin-ayeva/circle-bible/blob/main/CONTRIBUTING.md). This repository is public: nothing confidential goes in it.

Sources and their licences are listed in [SOURCES.md](SOURCES.md).

## Status

Pre-production. The area files, the Overture counting and extraction script, the Osu district and dressed block, and their export to compressed GLB for the game work. The merge in PostGIS and the later steps are specified in [pipeline/README.md](pipeline/README.md), not yet built.

## Contents

| Path | What it is |
| --- | --- |
| [SOURCES.md](SOURCES.md) | Every source, with licence, commercial use, obligations and status |
| [ATTRIBUTION.md](ATTRIBUTION.md) | The attribution block that ships in the game's credits and splash screen |
| [aoi/slices.geojson](aoi/slices.geojson) | The vertical slice (milestone 1) and its milestone 2 extension to Circle |
| [aoi/landmarks.geojson](aoi/landmarks.geojson) | Hero landmarks, with how each one is built |
| [scripts/overture_slice.py](scripts/overture_slice.py) | Counts or extracts Overture buildings and roads inside an area |
| [scripts/build_lookdev.py](scripts/build_lookdev.py) | Dresses the first 220 m of Oxford Street at target quality: facades with windows, bars, AC units and shopfronts, the shop signs and billboards transcribed on Oxford Street and the lap, rooms and shop interiors behind lit glass, sidewalks, gutters, power lines, kiosks, umbrellas |
| [scripts/build_district.py](scripts/build_district.py) | Turns an export into a drivable district for the engines: OBJ meshes, streetlights, a benchmark lap ([requirements-district.txt](requirements-district.txt)) |
| [scripts/massing.py](scripts/massing.py) | The district's building modules: hipped roofs on houses, stair bulkheads, set-back top storeys, unfinished frame storeys, compound walls with gates along residential streets |
| [scripts/export_glb.py](scripts/export_glb.py), [scripts/export_scene.py](scripts/export_scene.py) | What the game loads: indexed GLB per 250 m cell with glTF PBR materials (or, with `--tile-m`, one GLB per square of cells for streaming), and a scene JSON (lights, lap, parked cars, models, roads, solid outlines, hiding places, tiles) in glTF axes |
| [scripts/export_world.py](scripts/export_world.py) | The world the game streams: areas that join along their edges, home first, with their offsets in one frame |
| [scripts/build_kit.py](scripts/build_kit.py) | The Osu facade kit, modelled in Blender from the street survey: 13 low-poly parts (window surround, burglar bars, balcony balusters and rail, shutter box, canopy, kiosk posts, AC unit, polytank, parapet with and without rebar, pole with and without an LED head) exported to one GLB for the game, with a preview render. See [Building the facade kit](#building-the-facade-kit) |
| [look/look.json](look/look.json) | The look as data: materials, light kinds, night and day environments, taken from the Unity proof of concept |
| [tests/](tests/) | A test of the script against a synthetic release |
| [pipeline/README.md](pipeline/README.md) | The ten pipeline steps, from download to streamed district tiles |

## Building the facade kit

The kit needs Blender 5.2 or later and nothing else; it runs in the background, from a factory-fresh Blender.

```sh
blender -b --factory-startup -P scripts/build_kit.py -- \
  --out ../circle-game/public/kit/osu-kit.glb --preview kit-preview.png
```

- Each part is a root named `kit_<part>` in part space: X along the wall, Y up, Z out of the wall (Blender's −Y, which the glTF export turns into +Z), origin on the wall at the part's anchor.
- Materials are named `M_Kit_*`; the game tints `M_Kit_Trim` and `M_Kit_Sheet` per instance and swaps `M_Kit_LED` for its lamp material.
- The GLB goes to Git LFS in circle-game; on the team's Mac the runner jobs `blender-kit` and `kit-publish` build and commit it.

## Reference frame

- UTM zone 30N (EPSG:32630), metres.
- Origin at the Kwame Nkrumah Interchange ("Circle"): 5.5696° N, 0.2154° W, which is E 808 523, N 616 356 in UTM 30N. The game's world origin is the same point.

## The vertical slice

Black Star Square, Osu, Danquah Circle, Ring Road and Labadi.

- Bounding box (WGS84): south 5.542, north 5.575, west −0.1945, east −0.1370.
- In UTM 30N that is about 6.4 km east–west by 3.7 km north–south; about 16 km² of it is land.
- Counted on Overture release 2026-10-21.0, 12 November 2026 ([counts/vertical-slice-2026-10-21.0.json](counts/vertical-slice-2026-10-21.0.json)):

  | Buildings | Count | With height | With floors |
  | --- | ---: | ---: | ---: |
  | Google Open Buildings | 22,946 | 0 | 0 |
  | OpenStreetMap | 10,424 | 9 | 49 |
  | Microsoft ML Buildings | 2,806 | 0 | 0 |
  | **Total** | **36,176** | **9** | **49** |

  Road segments: 2,982 in all. Arterial: 7 trunk, 211 primary, 74 secondary, 121 tertiary (413). Local: 1,367 residential, 846 service, 248 unclassified. Paths and other: 65 footway, 32 path, 1 pedestrian, 10 unknown.
- Heights: almost none come with the footprints, so the 2.5D Temporal raster and the typology rules set nearly every height (pipeline step 3).
- Road length, from `scripts/overture_slice.py export` ([counts/vertical-slice-2026-10-21.0-roads.json](counts/vertical-slice-2026-10-21.0-roads.json)): **291.3 km**, of which **46.4 km arterial** (28.0 primary, 11.0 tertiary, 5.6 secondary, 1.8 trunk). Local: 142.5 km residential, 67.4 km service, 22.7 km unclassified. Segments that touch the area are measured whole, so the figures run slightly high. Both sit inside the earlier estimate (200–350 km, 40–60 km arterial).
- Milestone 2 moves the west edge to −0.220, which adds Circle through 2.8 km of Ring Road Central.

## Quick start

```sh
python3 -m pip install -r requirements.txt
python3 tests/test_overture_slice.py                       # offline check
python3 scripts/overture_slice.py count --release latest   # needs internet
python3 scripts/overture_slice.py export --release latest --out data/work/slice
```

The slice is read with pyarrow (Overture's S3 bucket, anonymously) and clipped with shapely: no DuckDB extension is downloaded at run time, so the build works where only the data hosts are reachable. DuckDB (core only, extensions never auto-installed) still reads the slice's Parquet in the build scripts. The shop-sign and billboard atlases are drawn with the DejaVu fonts in `look/fonts/` (checked against `SHA256SUMS`), so they come out the same on Linux, macOS and Windows.

Then, for the proof-of-concept district (about 1.3 km² around Danquah Circle, Oxford Street and Ring Road East):

```sh
python3 -m pip install -r requirements-district.txt
python3 scripts/build_lookdev.py --slice data/work/slice --out data/work/osu-poc
python3 scripts/build_district.py --slice data/work/slice --out data/work/osu-poc --exclude data/work/osu-poc/osu-lookdev-ids.txt
```

The look-dev script runs first: its 22 detailed buildings replace the plain ones, which `--exclude` leaves out of the district.

It writes `osu-poc.obj`/`.mtl` (3,162 buildings, 26 km of road, 137,000 triangles), `osu-poc.json` (71 streetlights before relighting, a 2.55 km benchmark lap, the spawn point) and a top-down preview. Building heights there come from typology rules until the 2.5D heights are merged.

The buildings are assembled from modules ([scripts/massing.py](scripts/massing.py), 14 November 2026), from the street survey in circle-reference and the roof census in circle-bible's `research/landscape-look.md`. On the 14 November build:

| Module | Rule | Count |
| --- | --- | ---: |
| Hipped roof | Houses of 1–2 storeys off Oxford Street on a near-rectangular footprint, 0.5 m overhang, soffit and fascia. Grey sheet, rusted sheet or red tile, tile mostly on 2 storeys. Other districts take their own palette (`ROOF_PALETTES`, counted from above in circle-reference `district-roofs.md`): Jamestown nearly all grey sheet at a 12° pitch, North Ridge and Spintex more red and some painted purple, blue or green sheet | 1,467 (659 grey, 446 rusted, 362 tile) |
| Shed roof | Districts whose palette asks for it (Jamestown): the other buildings of up to 3 storeys, whatever their footprint, under a single-slope sheet roof at 5°, rising 1.5 m at most, 0.3 m over the walls | Osu: none |
| Stair bulkhead | 3.4 × 2.6 × 2.6 m on most flat roofs of 2 storeys or more | 358 |
| Set-back top storey | 2 m behind a terrace on half the blocks of 4 storeys or more | 21 |
| Unfinished frame storey | Bare concrete columns on top of 1 block in 10 off Oxford Street | 55 (1,008 columns) |
| Compound walls | 2.25 m along the street frontage of residential blocks, 1.2 m back from the road edge, stopping at buildings and shopfronts | 36.6 km, 1,254 steel gates |

`--no-massing` builds the old extruded boxes, for comparisons. The game tells compound walls, walls under pitched roofs and houses in yards apart by flags in the walls' texture coordinates (see the top of `massing.py`); export the district with `--uv-only M_Wall_` and pack it with gltfpack's `-kv` so they survive. These files were first built for the Unity proof of concept; the game now loads them through the GLB export below.

For the game (three.js, decision of 13 November 2026), the same files become compressed GLB and a scene JSON:

```sh
python3 scripts/export_glb.py data/work/osu-poc/osu-poc.obj --uv-only M_Wall_ --out data/work/web/osu-poc.glb
python3 scripts/export_glb.py data/work/osu-poc/osu-lookdev.obj --textures ../circle-game/art/textures --out data/work/web/osu-lookdev.glb
python3 scripts/export_scene.py --district data/work/osu-poc/osu-poc.json --lookdev data/work/osu-poc/osu-lookdev.json --out data/work/web/osu-scene.json
# Scenes exported before 13 Nov 2026: apply the street survey's lighting rules without rebuilding
python3 scripts/relight_scene.py data/work/web/osu-scene.json
python3 tests/test_street_lights.py
python3 tests/test_massing.py
gltfpack -i data/work/web/osu-poc.glb -o data/work/web/osu-poc.pack.glb -cc -kn -km -kv -vpf -vtf
gltfpack -i data/work/web/osu-lookdev.glb -o data/work/web/osu-lookdev.pack.glb -cc -kn -km -kv -vpf -vtf   # -kv: the foliage cards need their UVs
cd ../circle-game && npm run data    # copies the packed GLB, the scene JSON and look.json into the game
```

On 13 November the district came to 0.53 MB (1.94 MB with the building modules of 14 November) and the look-dev block to 1.79 MB after meshopt compression (0.4 MB of geometry, 1.4 MB of atlases still in JPEG and PNG); KTX2 textures come next, through ktx2-encoder and glTF-Transform (decision of 13 November 2026), since the npm gltfpack has no BasisU. Both files pass the Khronos glTF validator with no errors or warnings. Generated textures from the Unity proof of concept (pavers, lattice, foliage cards, puddles, skies, now in `circle-game/art/textures/Generated`) are not ported yet; `look.json` marks them.

### Jamestown, the first district beyond Osu

The old town (Jamestown, Bukom and Ussher Town), from circle-reference's district card `jamestown-identity-survey.md`. Its area is `jamestown` in [aoi/slices.geojson](aoi/slices.geojson) (about 1.3 × 1.4 km); the build rules are in `DISTRICT_RULES` in `build_district.py`.

```sh
python3 scripts/overture_slice.py export --release latest --name jamestown --out data/work/slice-jamestown
python3 scripts/build_district.py --slice data/work/slice-jamestown --name jamestown --out data/work/jamestown
python3 scripts/export_glb.py data/work/jamestown/jamestown.obj --uv-only M_Wall_ --out data/work/web/jamestown.glb
python3 scripts/export_scene.py --district data/work/jamestown/jamestown.json --out data/work/web/jamestown-scene.json
gltfpack -i data/work/web/jamestown.glb -o data/work/web/jamestown.pack.glb -cc -kn -km -kv -vpf -vtf
```

- **What differs from Osu:**
  - roofs from Jamestown's palette, which the survey counted as grey sheet on 93% of the roof area. Hipped roofs go on the near-rectangular houses and shed roofs on the rest, up to 3 storeys;
  - no compound walls and no yards: buildings stand on the street line;
  - walls painted ochre, mint, grey, concrete and pale blue;
  - the lighthouse as a landmark: an octagonal tower 28 m high, white with a red band, a red gallery and a lit lantern. The footprint under it is left out.
- **Storeys:**
  - Footprints with OpenStreetMap levels keep them: 1,525 of the 4,345, mostly one storey. In Street View, 4 of 4 checked were right.
  - The others of 150 m² or more draw their storeys from the lap count of 82 buildings (44% one, 40% two, 9% three, 7% four or more), whatever their size. Of 7 large footprints checked, 5 were two storeys or less, so the typology rules' 3 to 6 storeys and a flat roof were wrong there.
  - Smaller footprints keep the typology rules.
- **Real signs at their places:** `DISTRICT_RULES` lists the three signs read in full along the lap, by name, place and the bearing of their facade. `export_scene.py` writes them as the scene's `signs`, and the game hangs each one once. About 1 shop bay in 10 carries a sign on the lap.
- **The 14 November build** (Overture 2026-10-21.0): 4,345 buildings, 27.4 km of road, 217 street lights and 127,700 triangles.
  - **Roofs:** 2,255 hipped and 1,857 shed; 3,915 grey, 121 rusted and 76 red.
  - **Roof area:** 94% sheet, 2.2% red.
  - **Inside the two surveyed frames:** flat roofs cover 2.6% in Bukom against 5% counted, and 1.9% in Ussher Town against 2% counted. Before the storey rule, Ussher Town was 16% flat.
- **Parked cars:** with no dressed block to park them, `build_district.py` parks cars parallel to the road edge (`"parking"` in `DISTRICT_RULES`, `place_parked()`). They stand within 250 m of the lap, on residential, unclassified, tertiary and secondary roads and on High Street, one place every 6.5 m on each side. A place is taken about one time in four, one in three on High Street, if it is clear of junctions, buildings, the spawn and other cars. Those chances and the paints (taxis on about 12% of places) are estimates, not counts. The 14 November build has 240 places, 25 of them taxis; `export_scene.py` writes them as the scene's `parked`, and `tests/test_parking.py` checks the rule on a synthetic street.
- **The benchmark lap** is 1.53 km: High Street, Ga Mashie Street, Nii Tackie Tawiah Street, Okai Pesemaku Street, Nii Owoo Street, Lutterodt Street and back to High Street.
- **In the game:** `?district=jamestown`. The district has no dressed block, so it carries no `--lookdev`.
- **Not built yet:** James Fort, Ussher Fort, the harbour, kiosks along the street edges, timber poles with tangled wires, the white-over-blue band on long walls.

`export_scene.py` names the scene's GLB after the district file (`jamestown.json` gives `jamestown.glb`); `--glb` overrides it.

### The Ring Road, streamed beside Osu

For the vertical slice's drives on the Ring Road (circle-bible `bible/game-design.md`: the Civic through traffic on the Ring Road, a heat chase in Osu and on the Ring Road). The area is `ring-road` in `AREAS`, built from the Osu slice already in `data/work/slice`: no new download.

- **What:** a corridor 250 m either side of the Ring Road, from the slice's west edge on Ring Road Central (−0.1945, 2.1 km short of Kwame Nkrumah Circle) through the Ako Adjei Interchange, Ring Road East, Danquah Circle and Ring Road East to its end at La Road. The segments are found by name (`RING_ROADS`); the corridor stays inside the vertical slice's box.
- **Next to Osu, not over it:** it leaves out osu-poc's box, so each building, road ribbon and light belongs to one area. It lays its ground per 250 m cell, and not where osu-poc lays its own (its box widened by the 60 m margin). It shares Osu's origin (Danquah Circle), so the game loads both areas in one frame with no offset. What is left is two pieces: north-west of Osu's box (Ring Road East and Central) and east of it (Ring Road East to La).
- **Building rules:** Osu's (`DISTRICT_RULES["ring-road"]`): walled yards off the main roads, Osu's roof mix and paints, the typology rules for storeys. The street survey describes Ring Road East's frontage as 1–2 storeys behind walls with about 20% shopfronts, without a count. On the build, 237 of the 269 buildings within 40 m of the Ring Road (88%) have 1–2 storeys.
- **Parked cars:** the area has no dressed block, so it parks cars like Jamestown (`place_parked()`). They go on its residential, unclassified and tertiary streets (the survey: "on sandy verges and concrete aprons in the residential streets"), never on the Ring Road itself. A place is taken about one time in five; that is an estimate. The clearances and paints are Jamestown's.
- **The lap** (`route_lap()`, `RING_LAP`) is 7.02 km. It starts on the westbound carriageway leaving Danquah Circle and runs west to the slice's edge. It turns through a median opening at the west end and comes back east through the circle to La, turns at the end of Ring Road East, and returns to the circle.
  - It passes through route points placed on vertices of each carriageway. The legs between them are shortest paths.
  - The roads named in `ONE_WAY_ROADS` (the Ring Road's carriageways, Danquah Circle, the Ako Adjei Interchange) are driven along their geometry only.
  - Roads are cut only where they share a vertex, so a flyover never becomes a turn.
  - It is driven on the roads of the whole corridor, Osu's box included. The game's headless test drives it in 405 s with no reset, against both areas' solid outlines.
- **One-way roads in the scene:** OpenStreetMap tags Ring Road East and Danquah Circle `oneway=yes` (circle-reference `street-survey.md`). The slice holds each carriageway of the Ring Road as its own segment, the two running opposite ways, westbound on the north side. Overture's one-way rules are not in the slice, so `traffic_network()` flags the pieces of `ONE_WAY_ROADS` `"oneway": true` and orients each along its carriageway. This adds the flag to osu-poc's Ring Road East and Danquah Circle (30 pieces); nothing else in osu-poc or Jamestown changes.
- **The 14 November build** (Overture 2026-10-21.0):
  - 2,421 buildings, 33.5 km of road and 124,900 triangles;
  - 281 street lights, of which 197 lit (147 LED, 37 cold LED, 13 sodium) and 84 dead;
  - 600 parked places, 94 of them taxis;
  - 2,420 solid outlines and 649 wall pieces;
  - massing: 1,080 hipped roofs, 286 bulkheads, 59 frame storeys, 15 set-backs, and 44.2 km of compound wall with 1,151 gates.
- **Tiles:** `export_glb.py --tile-m 500` groups the 250 m cells in squares of 500 m: 17 tiles, 1.72 MB packed (14 to 244 KB each, 35 primitives on average).
  - A tile of fewer than 200 triangles (a sliver of ground margin) joins its nearest neighbour.
  - `tiles.json` lists each tile's file, ground bounds in glTF x and z, triangles and sizes, and `export_scene.py --tiles` copies the list into the scene.
  - The scene is 400 KB, 102 KB gzipped.
- **The world:** `export_world.py` writes `osu-world.json`, osu-poc first (home) and the Ring Road after it, with each area's offset from home's origin.
  - It also names the file in home's scene (`files.world` in `osu-scene.json`), so it runs after `export_scene.py`; a scene already relit by `relight_scene.py` (compact JSON) works too. A scene that names no world loads alone.
  - The game loads home whole, merges both scenes, and streams the tiles by distance (circle-game `docs/world.md`).
- **Rebuild** (after osu-poc and its scene, above):

  ```sh
  python3 scripts/build_district.py --slice data/work/slice --name ring-road --out data/work/ring-road
  python3 scripts/export_glb.py data/work/ring-road/ring-road.obj --uv-only M_Wall_ --tile-m 500 --gltfpack gltfpack --out data/work/web/ring-road.glb
  python3 scripts/export_scene.py --district data/work/ring-road/ring-road.json --tiles data/work/web/ring-road/tiles.json --out data/work/web/ring-road-scene.json
  python3 scripts/export_world.py --name osu data/work/web/osu-scene.json data/work/web/ring-road-scene.json --out data/work/web/osu-world.json
  python3 tests/test_ring_road.py
  cd ../circle-game && npm run data    # copies osu-world.json, ring-road-scene.json and ring-road/*.pack.glb too
  ```

- **To reach Kwame Nkrumah Circle**, the corridor needs the milestone-2 slice (west edge −0.220): `python3 scripts/overture_slice.py export --release 2026-10-21.0 --name milestone-2 --out data/work/slice-m2`.
  - That adds 2.8 × 3.7 km, about 10 km² of dense city (Adabraka, Kokomlemle, Asylum Down, Ridge), to the vertical slice.
  - At the vertical slice's 36,176 buildings in 4.4 MB of GeoParquet (16 km² of land), it should come to 7 to 9 MB in all.
  - DuckDB reads from Overture's bucket only the row groups that cover the box, but those are larger than the output, so the transfer is likely tens of MB (not measured).
  - Not downloaded: it waits for approval. Then `VERTICAL_SLICE` in the corridor's `"within"` becomes milestone-2's box and the lap's west route points move to Circle.
- **Cross-section** (`road_width()`, the user's test drive of 14 Nov 2026): each carriageway of Ring Road East, Ring Road Central and the Ako Adjei Interchange is 7 m wide, as the survey measured on Ring Road East (circle-reference `street-survey.md`; Central and the interchange's ramps are not surveyed and take the same), and Danquah Circle's ring 9.5 m (surveyed 9–10 m). The class width had drawn each 13 m: on Ring Road East the centrelines are mostly 14 to 15 m apart, so the ribbons covered the grass median and its poles stood on the asphalt. The median is now ground, 7 to 8 m wide on Ring Road East.
  - **Median poles:** on a dual carriageway a pole stands in the middle of the median with a head over each carriageway (two lamps on one foot: a twin-arm pole), about 40 m apart in one row (`place_lights()`, `median_width()`); where the median is under 2 m it stands on the outer verge. `relight_scene.py` keeps both heads of a pole together.
  - **No pole on a carriageway** (`off_asphalt()`): where roads meet or run side by side, a pole that lands on any road's asphalt moves back from the road its arm reaches over, up to 8 m, or goes; a twin pole on asphalt (a median opening) goes whole. Osu loses 8 lamps, the Ring Road corridor moves 13 and drops 44, most of them on Independence Avenue, a dual carriageway still drawn 18 m wide per carriageway, one way each not flagged: the next road to get the Ring Road's treatment.
  - The widths reach the traffic's network (`w`), the parked cars' clearances, the collision outlines' carve-out and the preview; the side streets' hiding places still clear every carriageway by its class width. In the game, Danquah's island fitted inside the ring grows from 54.9 × 41.3 m to 58.4 × 44.8 m (surveyed about 56 × 47 m).
- **Not built yet:** the billboards and gantry along the Ring Road (`texts.csv` rows `txt-osu-ringdanquah-*`).

### Liberation Road to 37, north of the Ring Road

The area is `liberation` in `AREAS`, from the slice `north-37` (`aoi/slices.geojson`, 5.570–5.610 N, −0.200 to −0.165; `overture_slice.py export --name north-37 --out data/work/slice-north`).

- **What:** a corridor 300 m either side of Independence Avenue, 37 Circle and Liberation Road (`LIBERATION_ROADS`), from the Ako Adjei Interchange to the airport end of Liberation Road, inside `LIBERATION`. It holds 37 Military Hospital's grounds and 37 Station.
- **Joined to the Ring Road:** it leaves out the Ring Road's corridor (`"minus": ["ring-road"]`), computed from this slice's roads (`corridor_region()`). The two meet along the edge of the Ring Road's buffer, about 660 m of shared edge with no gap; 105 m² overlap from simplification. Osu's origin, so the game loads the three areas in one frame.
- **The lap** (`LIBERATION_LAP`), 7.95 km: north on the avenue's northbound carriageway, 37 Circle, Liberation Road to the airport end, a turn through Dr Amilcar Cabral Road, south again, a turn at the Ako Adjei Interchange. The route points sit on graph nodes (snapped 0 to 13 m).
- **The 15 November build:** 2,209 buildings, 50 km of road, 425 street lights, 524 parked places, 43 places, 4.4 km of median; 20 tiles, 1.74 MB packed; scene 488 KB.

### Dual carriageways, found in the geometry

OpenStreetMap maps a boulevard with a central reservation as one one-way road per carriageway, drawn the way its traffic runs. Before, only the Ring Road's surveyed carriageways (`DUAL_CARRIAGEWAYS`) were treated as such; the others were drawn at their class's width (18 m for a trunk), two-way, and covered their median.

- **Detection** (`find_carriageways()`, checked by `tests/test_carriageways.py`): a motorway, trunk, primary or secondary road with, along at least 60% of its length (every 20 m), another such road 5 to 45 m to its left (Ghana drives on the right), running the opposite way within about 35 degrees, under the same name or one of the two unnamed.
- **Width:** 55% of the median centre-to-centre distance, between 6.5 m (two lanes) and 10.5 m (three); the Ring Road's surveyed 7 m win.
- **What follows:** the traffic drives each carriageway one way along its geometry (`"oneway": true`), the lap too, the poles stand in the median with a head over each carriageway, and parked cars and the collision carve-out use the real width.
- **Found (2026-10-21.0):** Osu 15 carriageway segments, the Ring Road corridor 68 (Independence Avenue, La Road, Ring Road Central and East), Liberation 59 (37 Circle, Giffard Road, Independence Avenue, Liberation Road, Olusegun Obasanjo High Street), Jamestown 13 (Cleland Road, Nii Kojo Ababio Avenue).
- **`medians`** (`find_medians()`): the central reservation of each pair, for the game's collisions and kerbs. Each entry has `pts` (its middle line, glTF axes in the scene), `w` (the width left between the two carriageways' asphalt), `h` (kerb height, 0.15 m) and `name`. It is drawn once per pair, cut where another road crosses it (half that road's width plus 2 m either side: the openings a driver turns through) and to the area. Osu 0.77 km, the Ring Road 4.25 km, Liberation 4.39 km, Jamestown 0.78 km.

**What every district's scene carries for the game's systems** (all three areas, 14 November 2026):
- **`roads`:** the drivable network, noded at every crossing, for the traffic (`traffic_network()`); pieces of one-way roads carry `"oneway": true` and run the way the traffic goes.
- **`solid`:** what the cars collide with (`solid_outlines()`, checked by `tests/test_solid.py`):
  - the outline of every building, look-dev and landmark ones included, simplified to 0.5 m;
  - the compound walls, their closed gates included;
  - **kept off the carriageway:** mapped footprints sometimes reach onto the road (kiosks, canopies, a misplaced centreline: 23 outlines in Osu and 57 in Jamestown lay within 1 m of a centreline). The part of an outline on a drivable road, up to 0.3 m short of its edge and with the junctions' corners, is left out of the collisions; the buildings are still drawn whole;
  - **counts:** Osu 3,184 outlines and 637 wall pieces, Jamestown 4,342 outlines and no walls, the Ring Road 2,420 outlines and 649 wall pieces.
- **Format:** both are written on one line each, as flat lists of numbers, so the Osu scene is 289 KB (95 KB compressed) and Jamestown's 399 KB.
- **`hiding`:** where the game's hiding places stand, for the heat chase (circle-bible `bible/game-design.md`, level 3: "a trotro station, car wash, market shed, a crew garage"). They are invented places with no names (`hiding_places()`, checked by `tests/test_hiding.py`):
  - **What a place is:** a plot 9 m along a side street (tertiary, residential, unclassified) and 11 m deep. Its open front stands 1.2 m back from the carriageway and faces it. The game builds a shed on it (circle-game `src/world/hiding.ts`). Each entry has its kind, the plot's centre, the way its front faces, and its width and depth.
  - **Where it can stand:** the plot and the strip in front of it must be clear of building outlines (0.5 m), compound walls (0.3 m), every carriageway (1 m), parked cars (2.5 m) and street-light poles (1 m). It must also lie inside the area and more than 40 m from Oxford Street's dressed block.
  - **How they are picked:** candidates every 6 m on both sides are taken in a hashed order, at least 200 m apart. Kinds take turns: car wash, market shed, crew garage.
  - **Counts:** Osu 5, Jamestown 15, the Ring Road corridor 8. Osu's compound walls line almost every street: 27 candidate plots of 5,290 clear them. From Osu's lap, the nearest hiding place is 256 m away at the median and 461 m at most.

- **`places`:** the named places the game's minimap and big map show, best first (`scripts/places.py`, checked by `tests/test_places.py`):
  - **Sources:** the slice's `places.parquet` (`overture_slice.py export`, or `--layers places` alone for a slice exported before): Overture places (Meta, Microsoft, Foursquare, AllThePlaces), and OpenStreetMap's named land use (hospitals, campuses, stadiums, parks, with their area) and stations from Overture's base theme. Stations and airports come from OpenStreetMap only: in Accra, Overture's "airports" and "bus stations" are mostly offices named after them.
  - **Categories:** 15 icons, from the most useful to a driver: hospital, landmark, transit, police, fuel, market, bank, university, embassy, hotel, school, worship, pharmacy, nightlife, food. Other kinds (shops, offices, services) are left out.
  - **Kept:** Overture places at confidence 0.65 or more (0.55 for hospitals, police and fuel), not closed, with a name that reads (spaces collapsed, all capitals in title case, a long name cut at its dash or bracket, a second script dropped beside a Latin one). Names that start with a title (Dr., Rev., Pastor...) are left out: no real private people in the game. Pins of one place (same category, within 80 m, names sharing most words) merge, and a pin merges into the OpenStreetMap site of its name within 300 m, at the site's point.
  - **Rank:** the category's weight times the confidence, plus what is known (website, socials, phone, brand, a source besides Meta), or the site's area for OpenStreetMap land use, on one scale (0 to 1) in every area so the game can merge them. `brand: true` marks a place Overture names a brand for: brands are cleared before a public release (circle-bible DECISIONS.md, 14 Nov 2026).
  - **Counts (2026-10-21.0):** Osu 93, the Ring Road corridor 32, Jamestown 22. Liberation holds 43, 37 Military Hospital and 37 Station among them.
  - **Freshness:** as current as the Overture release the city is built from (monthly); rebuilding with a newer `OVERTURE_RELEASE` refreshes them. The game asks no service for places while it runs, so no player's position leaves the machine.

`latest` picks the newest release on Overture's public bucket; a fixed name from the [release list](https://docs.overturemaps.org/release/) works too. Downloads and working files go under `data/`, which is not committed.

## Publishing under ODbL

The game itself (meshes, textures, code) is a "produced work" and stays proprietary, with the attribution block in its credits. The merged roads, footprints and heights are a "derivative database": once the game is public, that database must be offered free online under ODbL 1.0. The plan is to publish it on day one, as a release of this repository made public, or of a public mirror, with a README that lists sources and processing.

This is not legal advice. Counsel confirms the licence readings before release.
