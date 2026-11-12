# Sources

Checked 12 November 2026, from the landscape research in `circle-bible/research/landscape.md`. Archive the licence text with every download, because licences can differ between the original release and a redistribution (Microsoft footprints are CDLA-Permissive-2.0 on GitHub but ODbL inside Overture).

## Used

| Source | What it gives | Accra coverage | Resolution / date | Licence | Obligations |
| --- | --- | --- | --- | --- | --- |
| [OpenStreetMap](https://www.openstreetmap.org/) (Geofabrik Ghana extract) | Road graph (class, one-way, bridge levels), names, land use, some buildings | Roads good; buildings sparse: 292,038 polygons in Greater Accra | Live; some speed tags unreliable | ODbL 1.0 | Credit "© OpenStreetMap contributors"; offer the derivative database free online |
| [Google Open Buildings v3](https://sites.research.google/open-buildings/) | Footprints with confidence scores | 2,619,136 polygons in Greater Accra | Inference May 2023 | CC BY 4.0 or ODbL | Attribution; keep only footprints above the 90%-precision threshold |
| [Open Buildings 2.5D Temporal](https://sites.research.google/gr/open-buildings/temporal) | Building presence, count and height rasters | Ghana covered | 4 m effective, yearly 2016–2023, heights 0–100 m | CC BY 4.0 or ODbL | Attribution plus the Sentinel notice; heights never validated in Africa |
| [Microsoft Global ML Building Footprints](https://github.com/microsoft/globalmlbuildingfootprints) | Footprints, heights where available | 1,316,415 polygons in Greater Accra | Imagery 2014–2025 | CDLA-Permissive-2.0 (repository) / ODbL (in Overture) | Keep the licence notice |
| [Overture Maps](https://docs.overturemaps.org/) | Conflated buildings, roads, places | Global | Monthly releases | Buildings and roads ODbL; places per source | OSM terms plus each source's credit ([attribution](https://docs.overturemaps.org/attribution/)) |
| [Copernicus DEM GLO-30](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf) | Elevation, surface model (includes buildings and trees) | Global; tiles N05 W001 and N05 E000 | 30 m | Copernicus WorldDEM-30 licence | The exact credit string in ATTRIBUTION.md, plus the liability disclaimer |
| [NASADEM](https://www.earthdata.nasa.gov/data/catalog/lpcloud-nasadem-hgt-001) / SRTM | Elevation, cross-check | Global | 30 m, 2000 | Openly shared | Cite the dataset |
| [Sentinel-2 L2A](https://ads.atmosphere.copernicus.eu/licences/ec-sentinel) | Ground colour, land cover (dry-season composite) | Global, 5-day revisit | 10 m | Copernicus Sentinel terms | "Contains modified Copernicus Sentinel data [year]" |

## Reference only

| Source | Use | Why not more |
| --- | --- | --- |
| Mapillary, KartaView | Looking at streets | CC BY-SA: share-alike makes derived textures risky; coverage thin in Accra |
| Ghana official data (2016 Greater Accra roads shapefile, 2021 census: 1.90 M structures) | Census figures as a sanity check | The shapefile has no licence; not usable without written permission |
| Esri, Mapbox imagery | Tracing only to improve OSM itself, under their OSM terms | No direct use in the game |
| Cesium ion curated assets (terrain, OSM Buildings, Bing) | Checking geometry while authoring | Streaming and display only; the game never ships a dependency on them |

## Never used

| Source | Why |
| --- | --- |
| Google Photorealistic 3D Tiles, Google imagery, Street View, Google Earth | The Map Tiles API policies forbid derived content and caching |
| FABDEM | Non-commercial licence, unless a commercial licence is bought |
| Maxar Open Data | CC BY-NC 4.0, and no Accra event found |

## Own capture

Phone video walks, ground photogrammetry of hero sites with the site authorities' permission, and drone work only with GCAA registration and a permit (every downtown hero site is 5–8 km from the airport, inside the 10 km zone). Faces blurred, every brand repainted. Street captures go back to Mapillary or Panoramax, field corrections to OSM under the Organised Editing Guidelines with two weeks' notice to the Ghana OSM community.
