"""Checks that a slice's files are written whole or not at all (scripts/overture_slice.py write, write_json; #16).

    python3 tests/test_atomic_write.py
"""
import os
import sys
import tempfile

import pyarrow as pa

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import overture_slice as os_  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


d = tempfile.mkdtemp()
schema = pa.schema([("id", pa.string())])

roads = os.path.join(d, "roads.parquet")
os_.write([{"id": "a"}], schema, roads)
check("a complete layer lands under its name", os.path.exists(roads) and not os.path.exists(roads + ".tmp"))

# A write that dies half way (the network cut after the read, a killed build): nothing under the final name, which
# circle-game's build-city.sh would take for a whole slice.
places = os.path.join(d, "places.parquet")
real = os_.pq.write_table


def dying(table, path, **kw):
    with open(path, "wb") as f:
        f.write(b"PAR1 half")
    raise OSError("network cut")


os_.pq.write_table = dying
try:
    os_.write([{"id": "b"}], schema, places)
except OSError:
    pass
os_.pq.write_table = real
check("a layer cut short leaves nothing under its name", not os.path.exists(places))

summary = os.path.join(d, "roads-summary.json")
os_.write_json({"road_km_total": 1.0}, summary)
check("the roads' summary is written whole", open(summary).read().lstrip().startswith("{") and not os.path.exists(summary + ".tmp"))

sys.exit(1 if failures else 0)
