"""Checks the scenes' binary twin (scripts/export_bin.py): every list comes back as the JSON had it, number for number
and key for key; what cannot be written exactly stays in the JSON part; the sections are aligned for typed arrays.

    python3 tests/test_scene_bin.py
"""
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import export_bin as eb  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


scene = {
    "name": "test", "axes": "glTF", "bounds_m": {"xmin": -1.5, "xmax": 2.5, "zmin": 0, "zmax": 3},
    "files": {"district": "t.glb", "look": "look.json", "tiles": [{"file": "t/t_0_0.glb", "bounds": [0, 0, 500, 500], "data": "t/t_0_0.data.json"}]},
    "streetLights": [
        {"kind": "led", "road": "Oxford Street", "foot": [1234.567, 0.0, -98.765], "forward": [0.70711, 0.0, -0.70711], "head": [1235.8, 10.0, -100.003]},
        {"kind": "dead", "road": "", "foot": [1.0, 0.25, 2.0], "forward": [1.0, 0.0, 0.0], "head": [2.75, 10.25, 2.0], "pole": "solar",
         "light": [2.75, 10.12, 2.0], "direction": [0.0, -1.0, 0.0]},
    ],
    "lap": {"length_m": 12.5, "points": [[0, 0], [1, 1]], "spawn": {"position": [0, 0, 0], "forward": [0, 0, 1]}},
    "parked": [{"colour": "#aa0000", "position": [5.12, 0.0, 6.34], "forward": [0.0, 0.0, 1.0]}],
    "roads": [
        {"cls": "primary", "w": 9.5, "name": "Ring Road East", "oneway": True, "lanes": 3, "pts": [[0.123456789, 1.1], [16384.1, -16384.1]]},
        {"cls": "residential", "w": 5, "name": "Kɔkɔ Lane", "pts": [[1e-7, 0.1], [2, 3]]},
    ],
    "places": [{"name": "Makola", "cat": "market", "at": [10.1, 20.2], "rank": 0.987, "brand": True},
               {"name": "Osu Castle", "cat": "landmark", "at": [-1.0, 2.0], "rank": 0.5, "person": True, "aliases": ["Christiansborg"]}],
    "solid": {"buildings": [[0, 0, 1, 0, 1, 1], [5.5, 5.5, 6.5, 5.5]], "walls": [[0.1, 0.2, 0.3, 0.4]], "gates": [[3.3, 4.4]]},
    "hiding": [{"kind": "yard", "position": [1.0, 0.0, 2.0], "forward": [0.0, 0.0, 1.0], "w": 6.0, "d": 8.5}],
    "medians": [{"pts": [[0, 0], [1, 1]], "w": 2.0, "h": 0.2, "name": "x"}],
    "garages": [{"name": "Kwame Fitting", "kind": "garage", "at": [1, 2], "door": [1, 3]}],
}

data, left = eb.encode(scene)
back = eb.decode(data)
check("a scene reads back as its JSON (the same doubles, keys in the same order)", eb.same(scene, back))
check("a value moved by one ulp is not the same", not eb.same(scene, {**scene, "bounds_m": {**scene["bounds_m"], "xmin": -1.5000000000000002}}))
check("nothing left in the JSON part", not left, str(left))
check("the keys keep their order", json.dumps(list(back["roads"][0])) == json.dumps(list(scene["roads"][0])) and list(back) == list(scene))
sec = eb.sections_of(data)
check("the roads' points as doubles when one needs it (0.123456789)", sec["roads.pts"][0] == eb.T_F64,
      f"type {sec['roads.pts'][0]}")
check("the lamps' feet as int32 thousandths", sec["streetLights.foot"][:2] == (eb.T_I32, 3), str(sec["streetLights.foot"][:2]))
check("the outlines in tenths", sec["solid.buildings"][1] == 1)
check("the forward vectors in hundred-thousandths", sec["streetLights.forward"][1] == 5)
check("the roads' names in the strings, once", json.loads(sec["schema"][2])["lists"]["roads"]["n"] == 2)

# Every section starts on 8 bytes, so a Float64Array can view it in place.
count = struct.unpack_from("<I", data, 8)[0]
offs = [struct.unpack_from("<28sBBHII", data, 16 + 40 * i)[4] for i in range(count)]
check("every section aligned to 8 bytes", all(o % 8 == 0 for o in offs))
check("the header says CHSB version 1", data[:4] == b"CHSB" and struct.unpack_from("<H", data, 4)[0] == 1)

# A number column picks the fewest decimals that come back exactly.
t, d, _ = eb.number_column([1.5, 2.25, -3.125])
check("1.5, 2.25, -3.125 in thousandths", (t, d) == (eb.T_I16, 3), f"{t}, {d}")
t, d, _ = eb.number_column([0.1 + 0.2])
check("0.30000000000000004 kept as a double", t == eb.T_F64)
t, d, _ = eb.number_column([-0.0, 1.0])
check("-0 kept as a double", t == eb.T_F64)
t, d, _ = eb.number_column([3000000.123])
check("3,000,000.123 does not fit 32 bits in thousandths: a double", t == eb.T_F64)

# What cannot be written exactly stays in the JSON part, as it was.
odd = json.loads(json.dumps(scene))
odd["roads"][1] = {"name": "Odd", "cls": "residential", "w": 5, "pts": [[0, 0], [1, 1]]}   # keys out of order
odd["parked"][0]["colour"] = 7   # a colour that is not a string
data2, left2 = eb.encode(odd)
check("a list with keys out of order stays JSON", "roads" in left2)
check("a list with a value of another type stays JSON", "parked" in left2)
check("and still reads back as its JSON", eb.same(odd, eb.decode(data2)))

# Empty lists (a chunked area's scene) stay as they are.
empty = {"streetLights": [], "parked": [], "solid": {"buildings": [], "walls": [], "gates": []}, "hiding": []}
check("a tile with nothing in it", eb.same(empty, eb.decode(eb.encode(empty)[0])))

# The command: a scene and its tile's data, beside the JSON or into --out.
d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, "t"))
with open(os.path.join(d, "test-scene.json"), "w") as f:
    json.dump(scene, f)
with open(os.path.join(d, "t", "t_0_0.data.json"), "w") as f:
    json.dump({"streetLights": scene["streetLights"], "parked": scene["parked"], "solid": scene["solid"], "hiding": scene["hiding"]}, f)
out = os.path.join(d, "out")
sys.argv = ["export_bin.py", os.path.join(d, "test-scene.json"), "--out", out]
eb.main()
check("the scene's binary written", os.path.exists(os.path.join(out, "test-scene.bin")))
check("its tile's binary written at the same relative path", os.path.exists(os.path.join(out, "t", "t_0_0.data.bin")))
tile = json.load(open(os.path.join(d, "t", "t_0_0.data.json")))
check("the tile reads back as its JSON", eb.same(tile, eb.decode(open(os.path.join(out, "t", "t_0_0.data.bin"), "rb").read())))

sys.exit(1 if failures else 0)
