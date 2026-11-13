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
| [scripts/build_lookdev.py](scripts/build_lookdev.py) | Dresses the first 220 m of Oxford Street at target quality: facades with windows, bars, AC units and shopfronts, invented shop signs, rooms and shop interiors behind lit glass, sidewalks, gutters, power lines, kiosks, umbrellas |
| [scripts/build_district.py](scripts/build_district.py) | Turns an export into a drivable district for the engines: OBJ meshes, streetlights, a benchmark lap ([requirements-district.txt](requirements-district.txt)) |
| [scripts/export_glb.py](scripts/export_glb.py), [scripts/export_scene.py](scripts/export_scene.py) | What the game loads: indexed GLB per 250 m cell with glTF PBR materials, and a scene JSON (lights, lap, parked cars, models) in glTF axes |
| [look/look.json](look/look.json) | The look as data: materials, light kinds, night and day environments, taken from the Unity proof of concept |
| [tests/](tests/) | A test of the script against a synthetic release |
| [pipeline/README.md](pipeline/README.md) | The ten pipeline steps, from download to streamed district tiles |

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

It writes `osu-poc.obj`/`.mtl` (3,184 buildings, 26 km of road, 41,000 triangles), `osu-poc.json` (143 streetlights, a 2.55 km benchmark lap, the spawn point) and a top-down preview. Building heights there come from typology rules until the 2.5D heights are merged. These files were first built for the Unity proof of concept; the game now loads them through the GLB export below.

For the game (three.js, decision of 13 November 2026), the same files become compressed GLB and a scene JSON:

```sh
python3 scripts/export_glb.py data/work/osu-poc/osu-poc.obj --out data/work/web/osu-poc.glb
python3 scripts/export_glb.py data/work/osu-poc/osu-lookdev.obj --textures ../circle-game/art/textures --out data/work/web/osu-lookdev.glb
python3 scripts/export_scene.py --district data/work/osu-poc/osu-poc.json --lookdev data/work/osu-poc/osu-lookdev.json --out data/work/web/osu-scene.json
# Scenes exported before 13 Nov 2026: apply the street survey's lighting rules without rebuilding
python3 scripts/relight_scene.py data/work/web/osu-scene.json
python3 tests/test_street_lights.py
gltfpack -i data/work/web/osu-poc.glb -o data/work/web/osu-poc.pack.glb -cc -kn -km -vpf -vtf
gltfpack -i data/work/web/osu-lookdev.glb -o data/work/web/osu-lookdev.pack.glb -cc -kn -km -vpf -vtf
cd ../circle-game && npm run data    # copies the packed GLB, the scene JSON and look.json into the game
```

On 13 November the district came to 0.53 MB and the look-dev block to 1.79 MB after meshopt compression (0.4 MB of geometry, 1.4 MB of atlases still in JPEG and PNG); KTX2 textures come next, through ktx2-encoder and glTF-Transform (decision of 13 November 2026), since the npm gltfpack has no BasisU. Both files pass the Khronos glTF validator with no errors or warnings. Generated textures from the Unity proof of concept (pavers, lattice, foliage cards, puddles, skies, now in `circle-game/art/textures/Generated`) are not ported yet; `look.json` marks them.

`latest` picks the newest release on Overture's public bucket; a fixed name from the [release list](https://docs.overturemaps.org/release/) works too. Downloads and working files go under `data/`, which is not committed.

## Publishing under ODbL

The game itself (meshes, textures, code) is a "produced work" and stays proprietary, with the attribution block in its credits. The merged roads, footprints and heights are a "derivative database": once the game is public, that database must be offered free online under ODbL 1.0. The plan is to publish it on day one, as a release of this repository made public, or of a public mirror, with a README that lists sources and processing.

This is not legal advice. Counsel confirms the licence readings before release.
