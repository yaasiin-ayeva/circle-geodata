"""Runs overture_slice.py count against a tiny synthetic release with Overture's column layout.

    python3 tests/test_overture_slice.py
"""
import json
import os
import subprocess
import sys
import tempfile

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "overture_slice.py")


def build_fixture(root):
    con = duckdb.connect()
    bdir = os.path.join(root, "theme=buildings", "type=building")
    rdir = os.path.join(root, "theme=transportation", "type=segment")
    os.makedirs(bdir)
    os.makedirs(rdir)
    # Two buildings inside the vertical slice (Osu), one outside (Tema).
    con.sql(
        f"""
        COPY (SELECT * FROM (VALUES
          ('b1', {{'xmin': -0.185, 'xmax': -0.184, 'ymin': 5.560, 'ymax': 5.561}},
                 [{{'dataset': 'OpenStreetMap'}}], 9.0, 3),
          ('b2', {{'xmin': -0.170, 'xmax': -0.169, 'ymin': 5.555, 'ymax': 5.556}},
                 [{{'dataset': 'Google Open Buildings'}}], NULL, NULL),
          ('b3', {{'xmin': -0.010, 'xmax': -0.009, 'ymin': 5.660, 'ymax': 5.661}},
                 [{{'dataset': 'Microsoft ML Buildings'}}], NULL, NULL)
        ) t(id, bbox, sources, height, num_floors))
        TO '{bdir}/part-0.parquet' (FORMAT parquet)
        """
    )
    # A primary road and a residential road inside, a rail segment inside, a motorway outside.
    con.sql(
        f"""
        COPY (SELECT * FROM (VALUES
          ('r1', {{'xmin': -0.190, 'xmax': -0.180, 'ymin': 5.558, 'ymax': 5.560}}, 'road', 'primary'),
          ('r2', {{'xmin': -0.181, 'xmax': -0.179, 'ymin': 5.556, 'ymax': 5.557}}, 'road', 'residential'),
          ('r3', {{'xmin': -0.181, 'xmax': -0.179, 'ymin': 5.556, 'ymax': 5.557}}, 'rail', 'standard_gauge'),
          ('r4', {{'xmin': -0.100, 'xmax': -0.050, 'ymin': 5.640, 'ymax': 5.650}}, 'road', 'motorway')
        ) t(id, bbox, subtype, class))
        TO '{rdir}/part-0.parquet' (FORMAT parquet)
        """
    )


def main():
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
    print("ok: count filters the slice bbox, groups buildings by source and roads by class")


if __name__ == "__main__":
    main()
