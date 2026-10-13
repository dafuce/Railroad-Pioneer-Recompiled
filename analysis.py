import re, struct
from collections import namedtuple, Counter, defaultdict
from pathlib import Path

GAME = Path("C:/Program Files (x86)/Steam/steamapps/common/Railroad Pioneer/Data/database.res")
U32 = struct.Struct("<I")

# ====================== low-level readers ======================
def rbytes(d, p):
    n, = U32.unpack_from(d, p)
    if n > 100000 or p + 4 + n > len(d):
        raise ValueError(f"bad string length {n} at {p:#x}")
    return bytes(d[p+4:p+4+n]), p + 4 + n

def rstr(d, p):
    b, p = rbytes(d, p)
    return b.decode("cp1252"), p

# ====================== loading ======================
class StrRef(namedtuple("StrRef", "id text")):
    def __str__(self): return f"{self.text} [#{self.id}]"

Obj = namedtuple("Obj", "base cls id name rtcls props refs tail children extra")

def read_strings(d):
    sp = d.find(b"Locomotive Sixwheeler Name") - 8
    count, = U32.unpack_from(d, sp - 4)
    strings, p = {}, sp
    for _ in range(count):
        sid, = U32.unpack_from(d, p)
        key, p = rstr(d, p + 4)
        text, p = rstr(d, p)
        strings[sid] = (key, text)
    return strings, p                     # p = end of table

def parse_body(base, cls, body, strings):
    oid, = U32.unpack_from(body, 4)
    name, q = rstr(body, 8)
    rtcls, q = rstr(body, q)
    rest = body[q:]
    p = 1                                 # flag byte
    props, refs = {}, {}
    n, = U32.unpack_from(rest, p); p += 4
    for _ in range(n):                    # scalar properties
        pname, p = rstr(rest, p)
        tag = chr(rest[p]); p += 4
        if tag in "fiSlL":
            raw = rest[p:p+4]; p += 4
            if   tag == "f": v = round(struct.unpack("<f", raw)[0], 4)
            elif tag == "L": v = U32.unpack(raw)[0]
            elif tag == "S":
                sid, = U32.unpack(raw)
                v = None if sid == 0xffffffff else StrRef(sid, strings.get(sid, ("?", "?"))[1])
            else: v = struct.unpack("<i", raw)[0]
        elif tag == "b": v = bool(rest[p]); p += 1
        elif tag == "s": v, p = rstr(rest, p)
        elif tag == "-": v = None
        else: raise ValueError(f"unknown tag {tag!r} on {pname}")
        props[pname] = v
    n, = U32.unpack_from(rest, p); p += 4
    for _ in range(n):                    # reference properties
        pname, p = rstr(rest, p)
        rid, = struct.unpack_from("<i", rest, p)
        rcls, p = rstr(rest, p + 4)
        refs[pname] = (rid, rcls)
    n, = U32.unpack_from(rest, p); p += 4
    tail = struct.unpack_from(f"<{n}I", rest, p); p += 4 * n
    extra, children = rest[p:], ()
    if base == "CKKWindowRes":
        _, m = struct.unpack_from("<II", extra, 0)
        assert 8 + 4 * m == len(extra), "window trailer mismatch"
        children, extra = struct.unpack_from(f"<{m}I", extra, 8), b""
    return Obj(base, cls, oid, name, rtcls, props, refs, tail, children, extra)

def load(path=GAME):
    d = path.read_bytes()
    strings, table_end = read_strings(d)
    objs, pos = [], table_end + 4         # 4 undecoded bytes after the table
    while pos < len(d):
        base, p = rstr(d, pos)
        cls, p = rstr(d, p)
        size, = U32.unpack_from(d, p); p += 4
        objs.append(parse_body(base, cls, d[p:p+size], strings))
        pos = p + size
    assert pos == len(d)
    return d, strings, objs

data, strings, objs = load()
byid = {o.id: o for o in objs}
print(f"{len(strings)} strings, {len(objs)} objects")

# ====================== formatting ======================
def fmt(v):
    if isinstance(v, StrRef):
        return str(v).replace("\r", "\\r").replace("\n", "\\n")
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int):
        return f"->{v[0]}:{v[1]}"
    return str(v).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")

def jname(ref):
    rid, cls = ref
    return f"{rid}:{byid[rid].name!r}" if rid in byid else f"{rid}:{cls}"

def describe(o):
    """Print one object: props, refs, tail."""
    o = byid[o] if isinstance(o, int) else o
    print(f"\n{o.cls} [{o.id}] {o.name!r}  (runtime {o.rtcls})")
    print("  props:", {k: fmt(v) for k, v in o.props.items()})
    print("  refs: ", {k: jname(v) for k, v in o.refs.items() if v[0] >= 0})
    if o.tail: print("  tail: ", list(o.tail)[:20])

# ====================== lookups ======================
def find(text="", cls=None):
    """Objects whose name contains text (case-insensitive), optionally of a class."""
    t = text.lower()
    return [o for o in objs if t in o.name.lower() and (cls is None or o.cls == cls)]

def table(cls, *cols, n=60):
    """Quick table: table('CLocomotiveRes','BuildPrice','MilesPerHour')"""
    for o in [o for o in objs if o.cls == cls][:n]:
        print(f"{o.id:6} {o.name:30}",
              *[f"{c}={fmt(o.props.get(c, o.refs.get(c)))}" for c in cols])

_referrers = None
def refs_to(oid):
    """Reverse links built from forward references (the tail lists are unreliable)."""
    global _referrers
    if _referrers is None:
        _referrers = defaultdict(list)
        for o in objs:
            for k, (rid, _) in o.refs.items():
                if rid >= 0: _referrers[rid].append((o, k))
            for c in o.children: _referrers[c].append((o, "<child>"))
    return _referrers.get(oid, [])

def show_refs_to(oid, limit=30):
    for o, k in refs_to(oid)[:limit]:
        print(f"  {o.cls} [{o.id}] {o.name!r}  via {k}")

def diff_objects(a, b):
    """Property-by-property comparison of two objects."""
    a = byid[a] if isinstance(a, int) else a
    b = byid[b] if isinstance(b, int) else b
    ka = {**a.props, **{k: fmt(v) for k, v in a.refs.items()}}
    kb = {**b.props, **{k: fmt(v) for k, v in b.refs.items()}}
    print(f"{a.cls} [{a.id}] {a.name!r}   vs   {b.cls} [{b.id}] {b.name!r}")
    for k in sorted(set(ka) | set(kb)):
        if ka.get(k) != kb.get(k):
            print(f"  {k}: {fmt(ka.get(k))}  |  {fmt(kb.get(k))}")

# ====================== maps ======================
def cells(o):
    """(width, cells). Fieldmap cell = (object id, junction id, flags); railmap cell = 5 ints.
    Index = y*width + x."""
    x = o.extra
    if o.cls == "CFieldmapRes":
        _, sx, sy = struct.unpack_from("<III", x, 0); n, w, off = sx * sy, 3, 12
    else:
        _, n = struct.unpack_from("<II", x, 0); sx, w, off = int(n ** 0.5), 5, 8
    assert len(x) == off + n * w * 4, (o.name, len(x))
    return sx, [struct.unpack_from(f"<{w}i", x, off + i * w * 4) for i in range(n)]

def fieldmap(prefix):
    return next(o for o in objs if o.cls == "CFieldmapRes" and o.name.startswith(prefix))

def junction_cells(prefix, junction_id, include_terrain=False):
    """Everything on a junction's cells: [(x, y, object, flags)]."""
    sx, c = cells(fieldmap(prefix))
    out = []
    for i, (oid, jn, fl) in enumerate(c):
        o = byid.get(oid)
        if jn == junction_id and o and (include_terrain or o.cls != "CFieldRes"):
            out.append((i % sx, i // sx, o, fl))
    return out

def find_cells(object_id):
    """Every map cell holding a given object id (e.g. 3867 = Oilrefinery TradeField)."""
    for o in objs:
        if o.cls == "CFieldmapRes":
            sx, c = cells(o)
            for i, (oid, jn, fl) in enumerate(c):
                if oid == object_id:
                    print(f"{o.name} ({i % sx},{i // sx}) junction={jn} flags={fl}")

SYMBOLS = {"CCityHallRes": "H", "CMarketPlaceRes": "M", "CGeographicQuartersRes": "G",
           "CWorksQuartersRes": "W", "CEngineersQuartersRes": "E",
           "CBuildingLotRes": "b", "CTradefieldRes": "t"}

def text_map(prefix):
    """ASCII map of a mission's fieldmap; + = pre-built rail."""
    fm = fieldmap(prefix); sx, c = cells(fm)
    rail = next((o for o in objs if o.cls == "CRailmapRes" and o.name.startswith(prefix)), None)
    rc = cells(rail)[1] if rail else None
    common = [k for k, _ in Counter(r[0] for r in c).most_common(8)
              if byid.get(k) and byid[k].cls == "CFieldRes"][:5]
    chars = dict(zip(common, ".:~#%"))
    print("legend:", {ch: byid[k].name for k, ch in chars.items()})
    for y in range(sx):
        row = ""
        for x in range(sx):
            oid = c[y * sx + x][0]
            ch = SYMBOLS.get(byid[oid].cls if oid in byid else "?") or chars.get(oid, "?")
            if rc and rc[y * sx + x][4]: ch = "+"
            row += ch
        print(row)

# ====================== placed scenery (object lists) ======================
# 30-byte records; the last u16 and the 3 spare floats are not fully understood.
REC = struct.Struct("<I2f3ffH")

def placed(o):
    x = o.extra
    ver, n = struct.unpack_from("<II", x, 0)
    recs = [REC.unpack_from(x, 8 + i * 30) for i in range(n)]
    return ver, recs, len(x) - (8 + n * 30)

def meshes_near(listname, cx, cy, size=14.0, pad=0.0, flip_y=False, grid=None):
    """Placed objects whose x/y fall inside field cell (cx, cy).
    ASSUMES one field cell = `size` world units (70 cells -> 980 units) and no border offset.
    If nothing shows up, try pad=7, flip_y=True (with grid = map width), or another size."""
    ol = next(o for o in objs if o.cls == "CObjectListRes" and o.name.startswith(listname))
    _, recs, _ = placed(ol)
    if flip_y and grid: cy = grid - 1 - cy
    x0, y0 = cx * size - pad, cy * size - pad
    out = []
    for i, q in enumerate(recs):
        if x0 <= q[1] < x0 + size + 2 * pad and y0 <= q[2] < y0 + size + 2 * pad:
            o = byid.get(q[0])
            out.append((i, q[0], o.cls if o else "?", o.name if o else "",
                        round(q[1], 1), round(q[2], 1)))
    return out

# ====================== small trailers ======================
def dec_texture(x):
    path, p = rstr(x, 4); assert p == len(x); return path

def dec_mesh(x):                          # CKKMeshVisualRes / CAnimVisualRes
    a, p = rstr(x, 4); t, p = rstr(x, p)
    bb = struct.unpack_from("<6f", x, p); p += 24
    n1, n2, _ = struct.unpack_from("<III", x, p); p += 12
    c, p = rstr(x, p); assert p == len(x)
    return f"{a} | {t} | {c} | n={n1},{n2} | bbox={[round(v, 2) for v in bb]}"

def dec_geo(x):
    _, n = struct.unpack_from("<II", x, 0); assert 8 + 4 * n == len(x)
    return list(struct.unpack_from(f"<{n}I", x, 8))

# ====================== missions ======================
def mission_chain(prefix="M1 "):
    for o in sorted(objs, key=lambda o: o.id):
        if o.cls == "CMissionGoalRes" and o.name.startswith(prefix):
            print(f"\n[{o.id}] {o.name}  main={o.props.get('IsMainGoal')}")
            for k, (rid, _) in o.refs.items():
                t = byid.get(rid)
                if not t: continue
                ty = t.props.get("ConditionType", t.props.get("CommandType"))
                used = {a: fmt(b) for a, b in t.props.items()
                        if a not in ("ConditionType", "CommandType") and b not in (0, 0.0, None, False)}
                used.update({a: jname(b) for a, b in t.refs.items() if b[0] >= 0})
                print(f"   {k}: type {ty} {str(used)[:160]}")

# ====================== analyses ======================
def check_tails():
    """Which references are NOT mirrored in the target's tail list?"""
    miss, extra = Counter(), Counter()
    for o in objs:
        have = set(o.tail)
        want = {s.id for s, _ in refs_to(o.id)}
        for s, k in refs_to(o.id):
            if s.id not in have: miss[(s.cls, k)] += 1
        for t in have - want:
            extra[(byid[t].cls if t in byid else "?", o.cls)] += 1
    print("missing from tail:", miss.most_common(15))
    print("extra in tail:", extra.most_common(15))

def swap_mesh(list_id, index, new_mesh_id):
    ol = byid[list_id]
    raw = bytearray(ol.extra[8 + index*30 : 8 + index*30 + 30])
    old, = struct.unpack_from("<I", raw, 0)
    struct.pack_into("<I", raw, 0, new_mesh_id)
    print(f"({list_id}, {index}, {old}, '{bytes(raw).hex()}'),   # {byid[old].name} -> {byid[new_mesh_id].name}")

def compare_placement(label, listname, prefix, junction_id, object_cls_filter=None):
    """Show meshes at each non-terrain cell of a junction (for the 'lot look' question)."""
    print(f"\n== {label}")
    for x, y, o, fl in junction_cells(prefix, junction_id):
        if object_cls_filter and o.cls not in object_cls_filter: continue
        print(f" cell ({x},{y}) {o.cls} {o.name!r} flags={fl}")
        for r in meshes_near(listname, x, y): print("    ", r)

def build_refinery_patch(src_list="M8", dst_list="MP4", src_first=441, src_count=15,dx=-140.0, lot_slots=(1741, 1742, 1743, 1744)):
        src = next(o for o in objs if o.cls == "CObjectListRes" and o.name.startswith(src_list))
        dst = next(o for o in objs if o.cls == "CObjectListRes" and o.name.startswith(dst_list))
        _, drecs, _ = placed(dst)

        # donor slots: the lot's lumber meshes, then border trees/cacti (outside the 10-cell border)
        border = 10 * 14.0
        donors = list(lot_slots)
        for i, q in enumerate(drecs):
            if len(donors) >= src_count: break
            o = byid.get(q[0])
            if o and o.name.startswith(("Tree", "Cactus")) and (q[1] < border or q[2] < border) and i not in donors:
                donors.append(i)
        if len(donors) < src_count:
            raise SystemExit(f"only {len(donors)} donor slots found; widen the border test")

        patches = []
        for k in range(src_count):
            raw = bytearray(src.extra[8 + (src_first + k) * 30: 8 + (src_first + k) * 30 + 30])
            x, = struct.unpack_from("<f", raw, 4)
            struct.pack_into("<f", raw, 4, x + dx)          # only x changes; y is the same row
            idx = donors[k]
            patches.append((dst.id, idx, drecs[idx][0], bytes(raw).hex()))
        return patches


if __name__ == "__main__":
    # El Paso (MP4, junction 6610) vs Provo (M8, junction 4490): why does the lot look remain?
    compare_placement("El Paso", "MP4", "MP4", 6610)
    compare_placement("Provo", "M8", "M8", 4490, {"CTradefieldRes"})
    diff_objects(4104, 3867)              # lot vs refinery definition
    print("\nEl Paso junction:", {k: fmt(v) for k, v in byid[6610].props.items()})
    print("Provo junction:  ", {k: fmt(v) for k, v in byid[4490].props.items()})

    for p in build_refinery_patch():
        print(p)

    GD_NAMES = ("Gold", "Boom")

    # 1. junctions that look like secret cities, and what sits on their cells
    gd_junctions = [o for o in objs if o.cls == "CJunctionRes" and any(n in o.name for n in GD_NAMES)]
    for j in gd_junctions:
        print(f"\n[{j.id}] {j.name}", {k: fmt(v) for k, v in j.props.items()})
        mp = j.name.split()[0]                       # M7, M10, SP5 ...
        try:
            for x, y, o, fl in junction_cells(mp, j.id):
                print(f"   cell ({x},{y}) {o.cls} [{o.id}] {o.name!r} flags={fl}")
        except StopIteration:
            print("   (no fieldmap with that prefix)")

    # 2. the gold-digger tradefields and lots against normal ones
    for oid in (6951, 6600, 8349, 9515):
        describe(oid)
    diff_objects(6951, 3863)                         # GoldDiggerCamp vs Oremine tradefield
    diff_objects(6600, 3294)                         # GoldDiggerCamp WorkersArea vs WorkersArea

    # 3. null references: slots that peers fill but these objects leave empty
    def null_slots(oid):
        o = byid[oid]; peers = [p for p in objs if p.cls == o.cls and p.id != oid]
        for k, (rid, c) in o.refs.items():
            if rid < 0:
                filled = sum(1 for p in peers if p.refs.get(k, (-1,))[0] >= 0)
                if peers and filled / len(peers) > 0.8:
                    print(f"  [{oid}] {o.name!r}: {k} is empty, {filled}/{len(peers)} peers have it")
    for oid in (6951, 6600, 8349, 9515):
        null_slots(oid)

    # 4. meshes of the mineshaft: names, bounding boxes, textures
    for o in objs:
        if o.cls == "CKKMeshVisualRes" and any(w in o.name for w in ("Gold", "Mine", "Shaft")):
            print(o.id, o.name, "|", dec_mesh(o.extra) if o.extra else "no trailer")

    # 5. trade orders aimed at these junctions
    ids = {j.id for j in gd_junctions}
    for o in objs:
        if o.cls == "CTradeOrderRes" and any(v[0] in ids for v in o.refs.values()):
            print("\ntrade order:", o.id, o.name, {k: fmt(v) for k, v in o.props.items() if v},
                {k: jname(v) for k, v in o.refs.items() if v[0] >= 0})
    for o in objs:
        if o.cls == "CTradeOrderSetRes" and any(byid.get(v[0]) and byid[v[0]].id in ids for v in o.refs.values()):
            print("order set:", o.id, o.name)

    for lid in (9039, 9514):
        ol = byid[lid]; _, recs, _ = placed(ol)
        print(f"\n{ol.name} [{lid}] {len(recs)} records")
        for q in recs:
            o = byid.get(q[0])
            print("  ", q[0], o.cls if o else "MISSING", o.name if o else "", round(q[1],1), round(q[2],1), round(q[6],0))

    # how do other maps place a normal mine? meshes near an Oremine cell
    find_cells(3863)
    # the secret-city event set
    describe(8169); describe(8154)
    # junctions that look like converted industry junctions
    for j in objs:
        if j.cls == "CJunctionRes" and j.props.get("City") and j.props.get("Inhabitants") == 12500:
            print(j.id, j.name, j.props.get("Name"))

            # 1. every fence-like mesh and its data
    for o in objs:
        if o.cls in ("CKKMeshVisualRes", "CMeshParticleRes") and "ence" in o.name:
            print(o.id, o.cls, o.name, "|", dec_mesh(o.extra) if o.extra else "no trailer",
                "| props:", {k: fmt(v) for k, v in o.props.items() if v})

    # 2. fence records inside the mine groups, with all record fields
    for lid in (9039, 9514):
        ol = byid[lid]; _, recs, _ = placed(ol)
        print(f"\n{ol.name} [{lid}]")
        for i, q in enumerate(recs):
            o = byid.get(q[0])
            if o is None or "ence" in o.name or o.cls != "CKKMeshVisualRes":
                print(" ", i, q[0], o.cls if o else "MISSING", o.name if o else "",
                    [round(v, 2) if isinstance(v, float) else v for v in q[1:]])

    # 3. the same fence in a working city (El Paso), for comparison
    for r in meshes_near("MP4", 13, 33):
        if "ence" in r[3]: print("city fence:", r)
    _, el = placed(next(o for o in objs if o.cls == "CObjectListRes" and o.name.startswith("MP4")))[:2]

    # 1. everything in both mine groups, all records
    for lid in (9039, 9514):
        ol = byid[lid]; _, recs, _ = placed(ol)
        print(f"\n{ol.name} [{lid}] {len(recs)} records")
        for i, q in enumerate(recs):
            o = byid.get(q[0])
            print(f"  {i:3} {q[0]:5} {o.name if o else 'MISSING':38} "
                f"x={q[1]:5.1f} y={q[2]:5.1f} ang={q[6]:5.0f}  "
                f"{dec_mesh(o.extra) if o and o.extra else ''}")

    # 2. any mesh whose name suggests a barrier
    for o in objs:
        if o.cls == "CKKMeshVisualRes" and any(w in o.name for w in ("Wall", "Border", "Barrier", "Gate", "Rail")):
            print(o.id, o.name, "|", dec_mesh(o.extra)[-60:] if o.extra else "")


    swap_mesh(9039, 6, 2420)
    
    #swap_mesh(9039, 6, 6096)   # B: replace with a tree, so the fence disappears