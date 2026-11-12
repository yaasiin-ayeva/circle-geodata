# Pipeline

From open data to streamed Unity tiles. Houdini turns the data into assets; Houdini Engine for Unity brings them into `circle-game`; Blender is for quick blockouts. Steps 1–4 belong to this repository; steps 5–10 produce assets that are committed in `circle-game`.

| # | Step | Tool | Output | Status |
| --- | --- | --- | --- | --- |
| 1 | Reference frame | — | UTM 30N (EPSG:32630), origin at Circle, tile grid | Done (see the main README) |
| 2 | Download | `scripts/`, DuckDB, Geofabrik | Raw extracts under `data/raw/`, with each licence archived beside them | Overture done; OSM, Google, Microsoft, DEM, Sentinel to do |
| 3 | Merge | PostGIS | One database of roads, footprints and heights, every value tagged with its source | To do |
| 4 | Publish | GeoPackage + README | The merged database under ODbL 1.0 | To do, before any public build |
| 5 | Terrain | Houdini | Building and road footprints masked out of the surface model, interpolated, resampled to Unity terrain or terrain meshes per tile | To do |
| 6 | Roads | Houdini → Unity Splines | Centrelines with class, lanes, one-way and bridge level; procedural surfaces, kerbs and open drains; lane graph for traffic | To do |
| 7 | Building typologies | Houdini | A type per footprint, by rule from area, height, land use, road class and place category | To do |
| 8 | Facade kits | Blender, Substance | Concrete-block buildings of 1–4 storeys, grilles, louvre windows, corrugated roofs, roller-shutter shops, kiosks, compound walls, curtain-wall towers; fictional signs only | To do |
| 9 | Street furniture | Houdini or Unity tools along splines | Streetlights (some stretches dark on purpose), trotro stops, hawker tables, gas cylinders, billboards, drain crossings, painted kerbs, palms | To do |
| 10 | Bake | Unity | Static meshes, HLODs, colliders and probes per tile, as Addressables groups; race routes polished by hand | To do |

## Merge rules (step 3)

- **Roads** come from OSM only.
- **Buildings:** OSM first; then Google Open Buildings above the 90%-precision threshold where OSM has none; then Microsoft.
- **Heights:** OSM `height` or `building:levels` tags; then Microsoft; then the median of the 2.5D Temporal raster inside the footprint; then typology rules.
- Record the source of every geometry and every height, so the published database says where each value came from.
- Use height classes rather than exact heights where the data is weak: 2.5D heights have never been validated in Africa. Hand-model the blocks that matter.

## Data folders (not committed)

```
data/raw/      downloads, each with its licence text and download date
data/work/     intermediate files and extracts
data/publish/  the ODbL database as released
```

## Checks

- Compare building counts with the 2021 census (1.90 M structures in Greater Accra) as a sanity check.
- Check speed and lane tags against field capture before tuning traffic.
- Freshness: footprints and heights stop in 2023; field capture fills the gaps, such as the Beach Road works.
