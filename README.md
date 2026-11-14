# circle-geodata

The open-data pipeline that rebuilds Accra for **CHALE**: roads, buildings, heights and terrain, from sources that allow commercial use. It feeds the browser game in `circle-game` (three.js) and produces the merged map database that the ODbL requires us to publish.

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
| [scripts/export_glb.py](scripts/export_glb.py), [scripts/export_scene.py](scripts/export_scene.py) | What the game loads: indexed GLB per 250 m cell with glTF PBR materials, and a scene JSON (lights, lap, parked cars, models) in glTF axes |
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

Then, for the proof-of-concept district (about 1.3 km² around Danquah Circle, Oxford Street and Ring Road East):

```sh
python3 -m pip install -r requirements-district.txt
python3 scripts/build_lookdev.py --slice data/work/slice --out data/work/osu-poc
python3 scripts/build_district.py --slice data/work/slice --out data/work/osu-poc --exclude data/work/osu-poc/osu-lookdev-ids.txt
```

The look-dev script runs first: its 22 detailed buildings replace the plain ones, which `--exclude` leaves out of the district.

It writes `osu-poc.obj`/`.mtl` (3,162 buildings, 26 km of road, 137,000 triangles), `osu-poc.json` (72 streetlights before relighting, a 2.55 km benchmark lap, the spawn point) and a top-down preview. Building heights there come from typology rules until the 2.5D heights are merged.

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
gltfpack -i data/work/web/osu-lookdev.glb -o data/work/web/osu-lookdev.pack.glb -cc -kn -km -vpf -vtf
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
- **The benchmark lap** is 1.53 km: High Street, Ga Mashie Street, Nii Tackie Tawiah Street, Okai Pesemaku Street, Nii Owoo Street, Lutterodt Street and back to High Street.
- **In the game:** `?district=jamestown`. The district has no dressed block, so it carries no `--lookdev`.
- **Not built yet:** James Fort, Ussher Fort, the harbour, kiosks along the street edges, timber poles with tangled wires, the white-over-blue band on long walls.

`export_scene.py` names the scene's GLB after the district file (`jamestown.json` gives `jamestown.glb`); `--glb` overrides it.

`latest` picks the newest release on Overture's public bucket; a fixed name from the [release list](https://docs.overturemaps.org/release/) works too. Downloads and working files go under `data/`, which is not committed.

## Publishing under ODbL

The game itself (meshes, textures, code) is a "produced work" and stays proprietary, with the attribution block in its credits. The merged roads, footprints and heights are a "derivative database": once the game is public, that database must be offered free online under ODbL 1.0. The plan is to publish it on day one, as a release of this repository made public, or of a public mirror, with a README that lists sources and processing.

This is not legal advice. Counsel confirms the licence readings before release.
