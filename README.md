# circle-geodata

The open-data pipeline that rebuilds Accra for **CHALE**: roads, buildings, heights and terrain, from sources that allow commercial use. It feeds the Unity game in `circle-game` and produces the merged map database that the ODbL requires us to publish.

**Never used, not even as a tracing reference:** Google Earth, Google Photorealistic 3D Tiles, Google imagery and Street View, Esri or Mapbox imagery outside OSM editing, and any data whose licence forbids commercial use (FABDEM, Maxar Open Data). See [SOURCES.md](SOURCES.md).

## Status

Pre-production. The area files and the Overture counting and extraction script work. The merge in PostGIS and the Houdini steps are specified in [pipeline/README.md](pipeline/README.md), not yet built.

## Contents

| Path | What it is |
| --- | --- |
| [SOURCES.md](SOURCES.md) | Every source, with licence, commercial use, obligations and status |
| [ATTRIBUTION.md](ATTRIBUTION.md) | The attribution block that ships in the game's credits and splash screen |
| [aoi/slices.geojson](aoi/slices.geojson) | The vertical slice (milestone 1) and its milestone 2 extension to Circle |
| [aoi/landmarks.geojson](aoi/landmarks.geojson) | Hero landmarks, with how each one is built |
| [scripts/overture_slice.py](scripts/overture_slice.py) | Counts or extracts Overture buildings and roads inside an area |
| [tests/](tests/) | A test of the script against a synthetic release |
| [pipeline/README.md](pipeline/README.md) | The ten pipeline steps, from download to baked tiles |

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
- Road length in kilometres is still the earlier estimate (200–350 km, of which 40–60 km arterial) until `scripts/overture_slice.py export` measures it.
- Milestone 2 moves the west edge to −0.220, which adds Circle through 2.8 km of Ring Road Central.

## Quick start

```sh
python3 -m pip install -r requirements.txt
python3 tests/test_overture_slice.py                       # offline check
python3 scripts/overture_slice.py count --release latest   # needs internet
python3 scripts/overture_slice.py export --release latest --out data/work/slice
```

`latest` picks the newest release on Overture's public bucket; a fixed name from the [release list](https://docs.overturemaps.org/release/) works too. Downloads and working files go under `data/`, which is not committed.

## Publishing under ODbL

The game itself (meshes, textures, code) is a "produced work" and stays proprietary, with the attribution block in its credits. The merged roads, footprints and heights are a "derivative database": once the game is public, that database must be offered free online under ODbL 1.0. The plan is to publish it on day one, as a release of this repository made public, or of a public mirror, with a README that lists sources and processing.

This is not legal advice. Counsel confirms the licence readings before release.
