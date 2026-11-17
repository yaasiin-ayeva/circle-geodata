"""Runs overture_slice.py count against a tiny synthetic release with Overture's column layout.

    python3 tests/test_overture_slice.py
"""
import json
import os
import subprocess
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "overture_slice.py")


def bbox(g):
    x0, y0, x1, y1 = g.bounds
    return {"xmin": x0, "xmax": x1, "ymin": y0, "ymax": y1}


def write(path, rows):
    os.makedirs(path)
    for r in rows:
        r["bbox"] = bbox(shapely.from_wkt(r["geometry"]))
        r["geometry"] = shapely.to_wkb(shapely.from_wkt(r["geometry"]))
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "part-0.parquet"))


def build_fixture(root):
    """A tiny release with Overture's column layout: no DuckDB, no extension."""
    sq = lambda x, y: f"POLYGON(({x} {y}, {x + 0.001} {y}, {x + 0.001} {y + 0.001}, {x} {y + 0.001}, {x} {y}))"
    # Two buildings inside the vertical slice (Osu), one outside (Tema).
    write(os.path.join(root, "theme=buildings", "type=building"), [
        {"id": "b1", "sources": [{"dataset": "OpenStreetMap"}], "height": 9.0, "num_floors": 3, "class": None,
         "subtype": None, "names": {"primary": "Shop"}, "geometry": sq(-0.185, 5.560)},
        {"id": "b2", "sources": [{"dataset": "Google Open Buildings"}], "height": None, "num_floors": None,
         "class": None, "subtype": None, "names": None, "geometry": sq(-0.170, 5.555)},
        {"id": "b3", "sources": [{"dataset": "Microsoft ML Buildings"}], "height": None, "num_floors": None,
         "class": None, "subtype": None, "names": None, "geometry": sq(-0.010, 5.660)},
    ])
    # A primary road and a residential road inside, a rail segment inside, a motorway outside.
    write(os.path.join(root, "theme=transportation", "type=segment"), [
        {"id": "r1", "subtype": "road", "class": "primary", "subclass": None, "names": {"primary": "Oxford Street"},
         "geometry": "LINESTRING(-0.190 5.558, -0.180 5.560)"},
        {"id": "r2", "subtype": "road", "class": "residential", "subclass": None, "names": None,
         "geometry": "LINESTRING(-0.181 5.556, -0.179 5.557)"},
        {"id": "r3", "subtype": "rail", "class": "standard_gauge", "subclass": None, "names": None,
         "geometry": "LINESTRING(-0.181 5.556, -0.179 5.557)"},
        {"id": "r4", "subtype": "road", "class": "motorway", "subclass": None, "names": None,
         "geometry": "LINESTRING(-0.100 5.640, -0.050 5.650)"},
    ])
    write(os.path.join(root, "theme=places", "type=place"), [
        {"id": "p1", "names": {"primary": "Kiosk"}, "basic_category": "shop", "confidence": 0.8,
         "operating_status": "open", "brand": None, "websites": ["https://x"], "socials": None, "phones": None,
         "sources": [{"dataset": "meta"}], "geometry": "POINT(-0.184 5.559)"},
        {"id": "p2", "names": None, "basic_category": "shop", "confidence": 0.8, "operating_status": None,
         "brand": None, "websites": None, "socials": None, "phones": None, "sources": None,
         "geometry": "POINT(-0.183 5.559)"},
    ])
    write(os.path.join(root, "theme=base", "type=land_use"), [
        {"id": "l1", "names": {"primary": "Ridge Hospital"}, "class": "hospital", "geometry": sq(-0.186, 5.561)},
        {"id": "l2", "names": {"primary": "A field"}, "class": "grass", "geometry": sq(-0.186, 5.562)},
    ])
    write(os.path.join(root, "theme=base", "type=infrastructure"), [
        {"id": "i1", "names": {"primary": "Tudu Station"}, "class": "bus_station", "geometry": "POINT(-0.182 5.558)"},
    ])


def check_latest_release_parsing():
    import importlib.util

    spec = importlib.util.spec_from_file_location("overture_slice", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    xml = (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<CommonPrefixes><Prefix>release/2026-09-17.0/</Prefix></CommonPrefixes>"
        "<CommonPrefixes><Prefix>release/2026-10-22.0/</Prefix></CommonPrefixes>"
        "<CommonPrefixes><Prefix>release/beta/</Prefix></CommonPrefixes>"
        "</ListBucketResult>"
    )
    assert mod.parse_releases(xml)[-1] == "2026-10-22.0"


def check_road_summary():
    import importlib.util

    spec = importlib.util.spec_from_file_location("overture_slice", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "roads.parquet")
        pq.write_table(pa.table({"class": ["primary", "primary", "residential", None],
                                 "length_m": [1200.0, 800.0, 500.0, 250.0]}), path)
        res = mod.summarize_roads(path)
    assert res["road_km_total"] == 2.8, res
    assert res["road_km_arterial"] == 2.0, res
    assert res["road_km_by_class"][0] == {"class": "primary", "segments": 2, "km": 2.0}, res


def check_s3_patience():
    """The bucket is read with long timeouts: the SDK's defaults failed city builds on a slow line."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("overture_slice", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    opts = mod.s3_filesystem().__reduce__()[1][0]
    assert opts["anonymous"] and opts["region"] == "us-west-2", opts
    assert opts["connect_timeout"] >= 30 and opts["request_timeout"] >= 300, opts


def main():
    check_latest_release_parsing()
    check_s3_patience()
    check_road_summary()
    with tempfile.TemporaryDirectory() as root:
        build_fixture(root)
        out = subprocess.run(
            [sys.executable, SCRIPT, "count", "--source", root],
            check=True, capture_output=True, text=True,
        ).stdout
        res = json.loads(out)
        assert res["buildings_total"] == 2, res
        sources = {x["source"]: x for x in res["buildings_by_source"]}
        assert set(sources) == {"OpenStreetMap", "Google Open Buildings"}, sources
        assert sources["OpenStreetMap"]["with_height"] == 1
        classes = {x["class"]: x["segments"] for x in res["road_segments_by_class"]}
        assert classes == {"primary": 1, "residential": 1}, classes
        out = os.path.join(root, "out")
        subprocess.run([sys.executable, SCRIPT, "export", "--source", root, "--out", out], check=True,
                       capture_output=True, text=True)
        b = pq.read_table(os.path.join(out, "buildings.parquet"))
        assert sorted(b["id"].to_pylist()) == ["b1", "b2"], b
        assert b.schema.metadata and b"geo" in b.schema.metadata
        assert shapely.from_wkb(b["geometry"][0].as_py()).geom_type == "Polygon"
        r = pq.read_table(os.path.join(out, "roads.parquet")).to_pylist()
        assert sorted(x["id"] for x in r) == ["r1", "r2"], r
        oxford = next(x for x in r if x["id"] == "r1")
        assert oxford["name"] == "Oxford Street" and 1100 < oxford["length_m"] < 1150, oxford
        p = {x["id"]: x for x in pq.read_table(os.path.join(out, "places.parquet")).to_pylist()}
        assert set(p) == {"p1", "l1", "i1"}, p
        assert p["p1"]["websites"] == 1 and p["p1"]["datasets"] == ["meta"], p["p1"]
        assert 10000 < p["l1"]["area_m2"] < 13000, p["l1"]
        assert shapely.from_wkb(p["l1"]["geometry"]).geom_type == "Point"
    print("ok: count and export filter the slice bbox, clip, measure and keep named places, with no DuckDB extension; "
          "road summary adds up")


if __name__ == "__main__":
    main()
