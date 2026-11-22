"""Write the binary twin of each scene and tile data file: the same data as flat typed arrays (circle-game
docs/render-plan.md, workstream C, item 1).

    python3 scripts/export_bin.py data/work/web/*-scene.json            # each scene and its tiles' data files
    python3 scripts/export_bin.py data/work/web/*-scene.json --out DIR  # into DIR, the same relative paths

Each `<name>-scene.json` gets a `<name>-scene.bin` beside it, and each chunked tile's `<tile>.data.json` a
`<tile>.data.bin`; the JSON stays, so a game that knows nothing of the binary still reads it. Run it last, after
export_world.py (which names the world in home's scene) and relight_scene.py (which rewrites home's lamps).

The game reads it with `?bintiles=1` (circle-game src/world/scenebin.ts): parsed in a worker, the arrays handed over
as transferables, the same scene built from them. The same means the same: every number comes back as the double
JSON.parse would give, every object with its keys in the JSON's order. This script checks it before it writes (it
decodes what it encoded and compares it with the JSON) and leaves a list in the JSON part of the file when it cannot
encode it exactly (a key out of the usual order, a value of another type).

The format, version 1, little-endian:

    header, 16 bytes: "CHSB", u16 version (1), u16 flags (0), u32 section count, u32 header bytes
    section table, 40 bytes each: name (28 bytes ASCII, NUL padded), u8 type, u8 decimals, u16 0, u32 offset, u32 bytes
    sections, each at an offset that is a multiple of 8, so a typed array can view it in place

Types: 1 u8, 2 u16, 3 u32, 4 i8, 5 i16, 6 i32, 7 f32, 8 f64, 9 UTF-8 text. An integer section with `decimals` d holds
numbers as integers times 10^d: k / 10^d is exactly the double the JSON's decimal text parses to (both sides divide
correctly rounded), so 1234.5 is stored as the integer 12345 and comes back as 1234.5, not as 1234.4999...

Sections:
  - `schema` (text): JSON {"lists": {path: {"n": records, "shape": "object"|"flat", "fields": [[name, kind, size, optional]]}}}
  - `scene` (text): the file's JSON with every encoded list replaced by 0, so the keys keep their order and everything
    not encoded (the lap, the garages, the fuel stations, the medians...) comes as it was
  - `strings` (text) and `strings.end` (u32): every distinct string, UTF-8, and where each ends (in bytes)
  - per list `<path>.<field>`: the field's values over the records that have it, in order; `<path>.<field>.end` (u32)
    for a field of variable length, the running count of values at the end of each record that has it; `<path>.$has`
    (u8, u16 or u32) the bits of the optional fields each record has, in the schema's order
  - a list of shape "flat" (the solid outlines: one flat list of numbers per record) is `<path>` and `<path>.end`

Field kinds: `str` (an index into the strings), `num` (one number), `vec` (`size` numbers: a point or a direction),
`true` (a flag present only when true: nothing stored but its bit), `flat` (a list of numbers), `pairs` (a list of
[x, z] pairs, stored flat), `strs` (a list of strings).
"""
import argparse
import json
import math
import os
import struct
import sys

MAGIC = b"CHSB"
VERSION = 1
NAME_BYTES = 28
ENTRY_BYTES = 40
HEADER_BYTES = 16
T_U8, T_U16, T_U32, T_I8, T_I16, T_I32, T_F32, T_F64, T_TEXT = range(1, 10)
FORMATS = {T_U8: "B", T_U16: "H", T_U32: "I", T_I8: "b", T_I16: "h", T_I32: "i", T_F32: "f", T_F64: "d"}
SIZES = {T_U8: 1, T_U16: 2, T_U32: 4, T_I8: 1, T_I16: 2, T_I32: 4, T_F32: 4, T_F64: 8, T_TEXT: 1}

# The lists written as arrays, with their fields in the order export_scene.py writes them: (name, kind, size, optional).
LISTS = {
    "streetLights": [("kind", "str", 1, False), ("road", "str", 1, False), ("foot", "vec", 3, False), ("forward", "vec", 3, False),
                     ("head", "vec", 3, False), ("pole", "str", 1, True), ("light", "vec", 3, True), ("direction", "vec", 3, True)],
    "parked": [("colour", "str", 1, False), ("position", "vec", 3, False), ("forward", "vec", 3, False),
               ("model", "str", 1, True), ("paint", "flat", 0, True)],
    "roads": [("cls", "str", 1, False), ("w", "num", 1, False), ("name", "str", 1, False), ("oneway", "true", 0, True),
              ("lanes", "num", 1, True), ("pts", "pairs", 0, False)],
    "places": [("name", "str", 1, False), ("cat", "str", 1, False), ("at", "vec", 2, False), ("rank", "num", 1, False),
               ("brand", "true", 0, True), ("person", "true", 0, True), ("aliases", "strs", 0, True)],
    "hiding": [("kind", "str", 1, False), ("position", "vec", 3, False), ("forward", "vec", 3, False), ("w", "num", 1, False),
               ("d", "num", 1, False)],
    "solid.buildings": None, "solid.walls": None, "solid.gates": None,   # flat: one list of numbers per record
}


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_nums(v, n=None):
    return isinstance(v, list) and (n is None or len(v) == n) and all(is_num(x) for x in v)


def field_ok(kind, size, v):
    if kind == "str":
        return isinstance(v, str)
    if kind == "num":
        return is_num(v)
    if kind == "vec":
        return is_nums(v, size)
    if kind == "true":
        return v is True
    if kind == "flat":
        return is_nums(v)
    if kind == "pairs":
        return isinstance(v, list) and all(is_nums(p, 2) for p in v)
    if kind == "strs":
        return isinstance(v, list) and all(isinstance(s, str) for s in v)
    return False


def why_not(records, fields):
    """None when every record can be written with these fields and read back with the same keys in the same order,
    else why not."""
    if not isinstance(records, list):
        return "not a list"
    names = [f[0] for f in fields]
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            return f"record {i} is not an object"
        keys = list(r.keys())
        # The record's keys must be the schema's, in its order, with only optional ones missing.
        want = [n for n in names if n in r]
        if keys != want:
            return f"record {i}'s keys {keys} are not in the schema's order {names}"
        for name, kind, size, opt in fields:
            if name not in r:
                if not opt:
                    return f"record {i} lacks {name}"
                continue
            if not field_ok(kind, size, r[name]):
                return f"record {i}'s {name} is not a {kind}"
    return None


def number_column(values):
    """The tightest exact storage of these numbers: (type, decimals, bytes). Integers times 10^d when every value comes
    back exactly (and none is -0), else float64."""
    if not values:
        return T_I32, 0, b""
    neg_zero = any(v == 0 and math.copysign(1.0, v) < 0 for v in values if isinstance(v, float))
    if not neg_zero and all(math.isfinite(v) for v in values):
        for d in range(0, 7):
            scale = 10 ** d
            ks = []
            for v in values:
                k = round(v * scale)
                if k / scale != v:
                    break
                ks.append(k)
            else:
                lo, hi = min(ks), max(ks)
                for t, lim in ((T_I8, 1 << 7), (T_I16, 1 << 15), (T_I32, 1 << 31)):
                    if -lim <= lo and hi < lim:
                        return t, d, struct.pack(f"<{len(ks)}{FORMATS[t]}", *ks)
                break   # too wide for 32 bits at this precision: more decimals only widen it
    return T_F64, 0, struct.pack(f"<{len(values)}d", *values)


def index_column(values, top):
    t = T_U8 if top < 1 << 8 else T_U16 if top < 1 << 16 else T_U32
    return t, struct.pack(f"<{len(values)}{FORMATS[t]}", *values)


class Strings:
    def __init__(self):
        self.ids, self.list = {}, []

    def id(self, s):
        if s not in self.ids:
            self.ids[s] = len(self.list)
            self.list.append(s)
        return self.ids[s]


def get_path(obj, path):
    for k in path.split("."):
        if not isinstance(obj, dict) or k not in obj:
            return None
        obj = obj[k]
    return obj


def set_path(obj, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        obj = obj[k]
    obj[keys[-1]] = value


def encode(doc):
    """The binary of one scene or tile data file, and the paths of the lists left in its JSON (with why)."""
    sections = []   # (name, type, decimals, bytes)
    strings = Strings()
    schema = {"lists": {}}
    rest = json.loads(json.dumps(doc))   # a copy, its lists replaced below
    left = {}
    for path, fields in LISTS.items():
        records = get_path(doc, path)
        if records is None:
            continue
        if not isinstance(records, list) or not records:
            continue   # absent or empty: as it is in the JSON
        if fields is None:
            if not all(is_nums(r) for r in records):
                left[path] = "a record is not a list of numbers"
                continue
            values, ends, n = [], [], 0
            for r in records:
                values.extend(r)
                n += len(r)
                ends.append(n)
            t, d, data = number_column(values)
            sections.append((path, t, d, data))
            sections.append((path + ".end", T_U32, 0, struct.pack(f"<{len(ends)}I", *ends)))
            schema["lists"][path] = {"n": len(records), "shape": "flat"}
            set_path(rest, path, 0)
            continue
        why = why_not(records, fields)
        if why:
            left[path] = why
            continue
        optional = [f[0] for f in fields if f[3]]
        if optional:
            bits = []
            for r in records:
                b = 0
                for j, name in enumerate(optional):
                    if name in r:
                        b |= 1 << j
                bits.append(b)
            t, data = index_column(bits, 1 << len(optional))
            sections.append((f"{path}.$has", t, 0, data))
        for name, kind, size, opt in fields:
            present = [r[name] for r in records if name in r]
            sec = f"{path}.{name}"
            if kind == "true":
                continue
            if kind == "str":
                ids = [strings.id(v) for v in present]
                t, data = index_column(ids, max(ids, default=0) + 1)
                sections.append((sec, t, 0, data))
            elif kind in ("num", "vec"):
                values = present if kind == "num" else [x for v in present for x in v]
                t, d, data = number_column(values)
                sections.append((sec, t, d, data))
            else:   # flat, pairs, strs: values and running ends
                values, ends, n = [], [], 0
                for v in present:
                    vals = [x for p in v for x in p] if kind == "pairs" else v
                    values.extend(vals)
                    n += len(vals)
                    ends.append(n)
                if kind == "strs":
                    ids = [strings.id(s) for s in values]
                    t, data = index_column(ids, max(ids, default=0) + 1)
                    sections.append((sec, t, 0, data))
                else:
                    t, d, data = number_column(values)
                    sections.append((sec, t, d, data))
                sections.append((sec + ".end", T_U32, 0, struct.pack(f"<{len(ends)}I", *ends)))
        schema["lists"][path] = {"n": len(records), "shape": "object", "fields": [list(f) for f in fields]}
        set_path(rest, path, 0)
    blob, ends, n = bytearray(), [], 0
    for s in strings.list:
        b = s.encode("utf-8")
        blob += b
        n += len(b)
        ends.append(n)
    head = [("schema", T_TEXT, 0, json.dumps(schema, separators=(",", ":")).encode()),
            ("scene", T_TEXT, 0, json.dumps(rest, separators=(",", ":"), ensure_ascii=False).encode()),
            ("strings", T_TEXT, 0, bytes(blob)), ("strings.end", T_U32, 0, struct.pack(f"<{len(ends)}I", *ends))]
    return pack(head + sections), left


def pack(sections):
    table = HEADER_BYTES + ENTRY_BYTES * len(sections)
    at = (table + 7) // 8 * 8
    entries, body = [], bytearray()
    for name, t, d, data in sections:
        nb = name.encode("ascii")
        if len(nb) > NAME_BYTES:
            raise ValueError(f"section name too long: {name}")
        entries.append(struct.pack("<28sBBHII", nb, t, d, 0, at + len(body), len(data)))
        body += data
        body += b"\0" * ((-len(body)) % 8)
    out = bytearray(MAGIC + struct.pack("<HHII", VERSION, 0, len(sections), table))
    for e in entries:
        out += e
    out += b"\0" * (at - len(out))
    return bytes(out + body)


# ---- The reader, the same as the game's (src/world/scenebin.ts): to check every file before it is written.

def sections_of(buf):
    if buf[:4] != MAGIC:
        raise ValueError("not a CHALE scene binary")
    version, _flags, count, _table = struct.unpack_from("<HHII", buf, 4)
    if version != VERSION:
        raise ValueError(f"version {version}, this reader knows {VERSION}")
    out = {}
    for i in range(count):
        nb, t, d, _r, off, size = struct.unpack_from("<28sBBHII", buf, HEADER_BYTES + ENTRY_BYTES * i)
        name = nb.rstrip(b"\0").decode("ascii")
        raw = buf[off:off + size]
        if t == T_TEXT:
            out[name] = (t, d, raw)
        else:
            vals = struct.unpack(f"<{size // SIZES[t]}{FORMATS[t]}", raw)
            if d:
                scale = 10 ** d
                vals = [k / scale for k in vals]
            out[name] = (t, d, list(vals))
    return out


def decode(buf):
    sec = sections_of(buf)
    schema = json.loads(sec["schema"][2])
    doc = json.loads(sec["scene"][2])
    blob, ends = sec["strings"][2], sec["strings.end"][2]
    strs, start = [], 0
    for e in ends:
        strs.append(blob[start:e].decode("utf-8"))
        start = e
    for path, spec in schema["lists"].items():
        n = spec["n"]
        if spec["shape"] == "flat":
            vals, ends = sec[path][2], sec[path + ".end"][2]
            out, start = [], 0
            for e in ends:
                out.append(vals[start:e])
                start = e
            set_path(doc, path, out)
            continue
        fields = spec["fields"]
        optional = [f[0] for f in fields if f[3]]
        has = sec[f"{path}.$has"][2] if optional else [0] * n
        cur = {f[0]: 0 for f in fields}
        endcur = {f[0]: 0 for f in fields}
        out = []
        for i in range(n):
            r = {}
            for name, kind, size, opt in fields:
                if opt and not has[i] >> optional.index(name) & 1:
                    continue
                if kind == "true":
                    r[name] = True
                    continue
                col = sec[f"{path}.{name}"][2]
                if kind == "str":
                    r[name] = strs[col[cur[name]]]
                    cur[name] += 1
                elif kind == "num":
                    r[name] = col[cur[name]]
                    cur[name] += 1
                elif kind == "vec":
                    r[name] = col[cur[name]:cur[name] + size]
                    cur[name] += size
                else:
                    e = sec[f"{path}.{name}.end"][2][endcur[name]]
                    endcur[name] += 1
                    vals = col[cur[name]:e]
                    cur[name] = e
                    r[name] = ([[vals[k], vals[k + 1]] for k in range(0, len(vals), 2)] if kind == "pairs"
                               else [strs[k] for k in vals] if kind == "strs" else vals)
            out.append(r)
        set_path(doc, path, out)
    return doc


def same(a, b):
    """Equal as JSON.parse would see them: the same keys in the same order, the same doubles (ints and floats alike)."""
    if isinstance(a, dict):
        return isinstance(b, dict) and list(a.keys()) == list(b.keys()) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if is_num(a):
        return is_num(b) and float(a) == float(b) and math.copysign(1.0, a) == math.copysign(1.0, b)
    return type(a) is type(b) and a == b


def write_bin(src, dst):
    doc = json.load(open(src))
    data, left = encode(doc)
    if not same(doc, decode(data)):
        raise SystemExit(f"{src}: the binary does not read back as the JSON")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    for path, why in left.items():
        print(f"{os.path.basename(src)}: {path} left in the JSON part ({why})", file=sys.stderr)
    return os.path.getsize(src), len(data)


def bin_name(path):
    return os.path.splitext(path)[0] + ".bin"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenes", nargs="+", help="scene JSON files from export_scene.py (their tiles' data files come with them)")
    ap.add_argument("--out", help="write the binaries here, at the same paths relative to each scene's folder, instead of beside the JSON")
    args = ap.parse_args()
    for scene in args.scenes:
        folder = os.path.dirname(scene)
        out = args.out or folder
        js, bs = write_bin(scene, os.path.join(out, bin_name(os.path.basename(scene))))
        tiles = [t["data"] for t in json.load(open(scene))["files"].get("tiles", []) if t.get("data")]
        tj = tb = 0
        for rel in tiles:
            a, b = write_bin(os.path.join(folder, rel), os.path.join(out, bin_name(rel)))
            tj += a
            tb += b
        print(f"{os.path.basename(scene)}: {js / 1024:.0f} KB of JSON -> {bs / 1024:.0f} KB"
              + (f"; {len(tiles)} tile data files {tj / 1048576:.2f} MB -> {tb / 1048576:.2f} MB" if tiles else ""))


if __name__ == "__main__":
    main()
