import struct, csv
from collections import namedtuple, Counter, defaultdict

PATH = r"C:/Program Files (x86)/Steam/steamapps/common/Railroad Pioneer/Data/database.res"
data = open(PATH, "rb").read()
U32 = struct.Struct("<I")

def rstr(b, p):
    n, = U32.unpack_from(b, p)
    if n > 100000 or p + 4 + n > len(b):
        raise ValueError(f"bad string length {n} at {p}")
    return b[p+4:p+4+n].decode("cp1252"), p + 4 + n

# ---------- string table ----------
sp = data.find(b"Locomotive Sixwheeler Name") - 8
count, = struct.unpack_from("<I", data, sp - 4)
strings, p = {}, sp
for _ in range(count):
    sid, = U32.unpack_from(data, p)
    key, p2 = rstr(data, p + 4)
    text, p = rstr(data, p2)
    strings[sid] = (key, text)
table_end = p

class StrRef(namedtuple("StrRef", "id text")):
    def __str__(self): return f"{self.text} [#{self.id}]"

# ---------- objects ----------
Obj = namedtuple("Obj", "base cls id name rtcls props refs tail children extra")

def parse_body(base, cls, body):
    oid, = U32.unpack_from(body, 4)
    name, q = rstr(body, 8)
    rtcls, q = rstr(body, q)
    rest = body[q:]
    p = 1                                    # byte 0 is a flag, always 0 so far
    props, refs = {}, {}

    n, = U32.unpack_from(rest, p); p += 4
    for _ in range(n):                       # scalar properties
        pname, p = rstr(rest, p)
        tag = chr(rest[p]); p += 4           # tag letter + 3 zero bytes
        if tag in "fiSlL":
            raw = rest[p:p+4]; p += 4
            if   tag == "f": v = round(struct.unpack("<f", raw)[0], 4)
            elif tag in "L": v = U32.unpack(raw)[0]
            elif tag == "S":
                sid, = U32.unpack(raw)
                v = None if sid == 0xffffffff else StrRef(sid, strings.get(sid, ("?", "?"))[1])
            else:            v = struct.unpack("<i", raw)[0]
        elif tag == "b": v = bool(rest[p]); p += 1
        elif tag == "s": v, p = rstr(rest, p)
        elif tag == "-": v = None
        else: raise ValueError(f"unknown tag {tag!r} on {pname}")
        props[pname] = v

    n, = U32.unpack_from(rest, p); p += 4
    for _ in range(n):                       # reference properties
        pname, p = rstr(rest, p)
        rid, = struct.unpack_from("<i", rest, p)
        rcls, p = rstr(rest, p + 4)
        refs[pname] = (rid, rcls)

    n, = U32.unpack_from(rest, p); p += 4
    tail = struct.unpack_from(f"<{n}I", rest, p); p += 4 * n
    extra, children = rest[p:], ()
    if base == "CKKWindowRes":               # [u32 version][u32 n][n ids]
        _, m = struct.unpack_from("<II", extra, 0)
        assert 8 + 4 * m == len(extra), "window trailer mismatch"
        children, extra = struct.unpack_from(f"<{m}I", extra, 8), b""
    return Obj(base, cls, oid, name, rtcls, props, refs, tail, children, extra)

objs, pos = [], table_end + 4  # 4 undecoded bytes between the table and the first object
while pos < len(data):
    base, p = rstr(data, pos)
    cls, p = rstr(data, p)
    size, = U32.unpack_from(data, p); p += 4
    objs.append(parse_body(base, cls, data[p:p+size]))
    pos = p + size
assert pos == len(data)
byid = {o.id: o for o in objs}
print(len(strings), "strings,", len(objs), "objects")

# ---------- known trailers ----------
def dec_texture(x):
    _, p = U32.unpack_from(x, 0)[0], 4
    path, p = rstr(x, p); assert p == len(x); return path

def dec_mesh(x):                              # CKKMeshVisualRes, CAnimVisualRes
    a, p = rstr(x, 4); t, p = rstr(x, p)
    bb = struct.unpack_from("<6f", x, p); p += 24
    n1, n2, _ = struct.unpack_from("<III", x, p); p += 12
    c, p = rstr(x, p); assert p == len(x)
    return f"{a} | {t} | {c} | n={n1},{n2} | bbox={[round(v, 2) for v in bb]}"

def dec_geo(x):
    _, n = struct.unpack_from("<II", x, 0)
    assert 8 + 4 * n == len(x)
    return ",".join(map(str, struct.unpack_from(f"<{n}I", x, 8)))

DECODERS = {"CTextureRes": dec_texture, "CKKMeshVisualRes": dec_mesh,
            "CAnimVisualRes": dec_mesh, "CGeoInfoRes": dec_geo}
decoded = {}
for o in objs:
    if o.extra and o.cls in DECODERS:
        decoded[o.id] = DECODERS[o.cls](o.extra)
raw_left = Counter(o.cls for o in objs if o.extra and o.cls not in DECODERS)
print("still raw:", dict(raw_left))

# ---------- export ----------
def fmt(v):
    if isinstance(v, StrRef): return str(v).replace("\r","\\r").replace("\n","\\n")
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int): return f"->{v[0]}:{v[1]}"
    return str(v).replace("\r","\\r").replace("\n","\\n").replace("\t","\\t")

with open("props.tsv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["base", "class", "id", "name", "props", "tail", "children", "trailer"])
    for o in objs:
        w.writerow([o.base, o.cls, o.id, o.name,
                    "; ".join(f"{k}={fmt(v)}" for k, v in {**o.props, **o.refs}.items()),
                    ",".join(map(str, o.tail)), ",".join(map(str, o.children)),
                    decoded.get(o.id, o.extra[:48].hex(" "))])

with open("strings.tsv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t"); w.writerow(["id", "key", "text"])
    for i, (k, t) in strings.items(): w.writerow([i, k, fmt(t)])

# analisis
    for label, lst, cx, cy in (("El Paso (14,31)", "MP4", 14, 31), ("Provo (24,31)", "M8", 24, 31)):
        print("\n", label)
        for r in meshes_near(lst, cx, cy): print("  ", r)

# what differs between the lot and the refinery definitions
    for oid in (4104, 3867):
        o = byid[oid]
        print("\n", o.cls, o.id, o.name)
        print("  props:", {k: fmt(v) for k, v in o.props.items()})
        print("  refs: ", {k: jname(v) for k, v in o.refs.items() if v[0] >= 0})

    print("\nEl Paso junction:", {k: fmt(v) for k, v in byid[6610].props.items()})
    print("Provo junction:  ", {k: fmt(v) for k, v in byid[4490].props.items()})    