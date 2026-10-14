import csv, math, os, re, struct
from pathlib import Path

GAME = Path("C:/Program Files (x86)/Steam/steamapps/common/Railroad Pioneer/Data/database.res")
PROPS, STRINGS = Path("props.tsv"), Path("strings.tsv")

# (fieldmap id, x, y, expected old object, new object, new flags or None)
FIELD_PATCHES = [
    (6586, 14, 31, 4104, 3867, None) # put a refinery in el paso
]

OBJLIST_PATCHES = [ # make it look like a refinery
    (6588, 1741, 2412, 'b907000004b44b43c357de430000000000000000000000000000b4420000'),
    (6588, 1742, 2412, 'bf070000521048431b1ddc43000000000000000000000000000000000000'),
    (6588, 1743, 1969, 'ba070000208c4f43530ede43000000000000000000000000000087430000'),
    (6588, 1744, 1966, 'b7070000d6245143f9b7dc43000000000000000000000000000034430000'),
    (6588, 974, 6100, 'bc0700000ab646434ab2dc43000000000000000000000000000000000000'),
    (6588, 1699, 6098, 'c00700009ef04a43225edb430000000000000000000000000080ac430000'),
    (6588, 1700, 6100, 'c1070000284750432ae3d943000000000000000000000000000000000000'),
    (6588, 1703, 6098, 'bd070000ccec4c43c2acda430000000000000000000000000080a2430000'),
    (6588, 1704, 6099, 'b6070000c2594743c3cedb43000000000000000000000000000000000000'),
    (6588, 1705, 6097, 'b60700001a1848435155db430000000000000000000000000000b4420000'),
    (6588, 1706, 6098, '40070000f03948436997dd43000000000000000000000000000000000000'),
    (6588, 1729, 6097, '4107000002315143eb1bda43000000000000000000000000000000000000'),
    (6588, 1731, 6098, '4207000096684843d0b7db43000000000000000000000000000000000000'),
    (6588, 1732, 6100, '4307000014714f439324dd430000000000000000000000000000b4420000'),
    (6588, 1733, 6096, 'b80700004a914a431fb4dd43000000000000000000000000000000000000'),
    (9039, 6, 5274, '74090000603c2a4000f4ac40000000000000000000000000000034439800')   #Attempt at fixing the goldmine crash, changing the fence
]
# (object id, reference property, expected old id, new id)
REF_PATCHES = [
    (6490, "ConnectedJunction2", 6500, -1),  # Tell the cpu in sp4 that it is not connected to mine 1 and 2 at the beginning of the game so it builds the tracks
    (6490, "ConnectedJunction3", 6502, -1),  # sp4 cpu: not connected to mine 2 at the start
    (6490, "Locomotive2", -1, 360),          # second Consolidation
    (6490, "LocomotiveJunction2", -1, 6495), # delivered at SilverCity
    (6333, "ConnectedJunction2", 6362, -1),  # sp2 cpu: not connected to lumberyard3 at the start
     (6333, "Locomotive2", 351, 372)  # sp2 cpu: second locomotive Fireless -> Decapod
]
U32, I32, F32 = struct.Struct("<I"), struct.Struct("<i"), struct.Struct("<f")

# ---------- helpers ----------
def rbytes(d, p):
    n, = U32.unpack_from(d, p)
    if n > 100000 or p + 4 + n > len(d): raise ValueError(f"bad string length at {p:#x}")
    return bytes(d[p+4:p+4+n]), p + 4 + n

def rstr(d, p):
    b, p = rbytes(d, p); return b.decode("cp1252"), p

def esc(b):
    return b.decode("cp1252").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")

def unesc(s):
    return re.sub(r"\\([rnt])", lambda m: {"r": "\r", "n": "\n", "t": "\t"}[m.group(1)], s)

# ---------- string table ----------
def read_table(d):
    sp = d.find(b"Locomotive Sixwheeler Name") - 8
    count, = U32.unpack_from(d, sp - 4)
    entries, p = [], sp
    for _ in range(count):
        sid, = U32.unpack_from(d, p)
        key, p = rbytes(d, p + 4)
        val, p = rbytes(d, p)
        entries.append((sid, key, val))
    return sp, p, entries

def build_table(entries, edits):
    out = bytearray()
    for sid, key, val in entries:
        val = edits.get(sid, val)
        out += U32.pack(sid) + U32.pack(len(key)) + key + U32.pack(len(val)) + val
    return bytes(out)

# ---------- object walk ----------
def skip_props(rest):
    """Walk an object's property block; yield (name, tag, value_pos) and return the end position."""
    p = 1
    n, = U32.unpack_from(rest, p); p += 4
    items = []
    for _ in range(n):
        name, p = rstr(rest, p)
        tag = chr(rest[p]); p += 4
        items.append((name, tag, p))
        if tag in "filLS": p += 4
        elif tag == "b": p += 1
        elif tag == "s": _, p = rstr(rest, p)
        elif tag != "-": raise ValueError(f"unknown tag {tag!r} on {name}")
    return items, p

def walk(d, start):
    """Yield (class, object id, body start, body end, body-relative start of 'rest')."""
    pos = start
    while pos < len(d):
        _, p = rstr(d, pos)
        cls, p = rstr(d, p)
        size, = U32.unpack_from(d, p)
        bs, be = p + 4, p + 4 + size
        if be > len(d): raise ValueError(f"bad object size at {pos:#x}")
        oid, = U32.unpack_from(d, bs + 4)
        _, q = rstr(d[bs:be], 8); _, q = rstr(d[bs:be], q)
        yield cls, oid, bs, be, q
        pos = be
    if pos != len(d): raise ValueError("object walk did not end exactly at EOF")

def index_properties(d, start):
    res, count = {}, 0
    for cls, oid, bs, be, q in walk(d, start):
        items, _ = skip_props(d[bs+q:be])
        for name, tag, p in items:
            res[(oid, name)] = (cls, tag, bs + q + p)
        count += 1
    return res, count

def fieldmap_cell_offset(d, start, fm_id, x, y):
    for cls, oid, bs, be, q in walk(d, start):
        if oid != fm_id: continue
        rest = d[bs+q:be]
        _, p = skip_props(rest)
        n, = U32.unpack_from(rest, p); p += 4
        for _ in range(n):                              # reference properties
            _, p = rstr(rest, p); _, p = rstr(rest, p + 4)
        n, = U32.unpack_from(rest, p); p += 4 + 4 * n   # tail
        ex = bs + q + p                                 # trailer start
        _, sx, sy = struct.unpack_from("<III", d, ex)
        assert ex + 12 + sx * sy * 12 == be, "fieldmap trailer size mismatch"
        return ex + 12 + (y * sx + x) * 12
    raise ValueError(f"fieldmap {fm_id} not found")

def pack_change(tag, value, off, d):
    if tag == "f":
        v = float(value)
        if not math.isfinite(v): raise ValueError("non-finite float")
        return None if round(F32.unpack_from(d, off)[0], 4) == v else F32.pack(v)
    if tag in "il":
        v = int(value); return None if I32.unpack_from(d, off)[0] == v else I32.pack(v)
    if tag == "L":
        v = int(value); return None if U32.unpack_from(d, off)[0] == v else U32.pack(v)
    if tag == "b":
        s = value.strip().lower()
        if s not in ("true", "false", "1", "0"): raise ValueError(f"bad bool {value!r}")
        v = s in ("true", "1"); return None if bool(d[off]) == v else bytes([int(v)])
    return None
    
def objlist_record_offset(d, start, ol_id, index):
    for cls, oid, bs, be, q in walk(d, start):
        if oid != ol_id: continue
        rest = d[bs+q:be]
        _, p = skip_props(rest)
        n, = U32.unpack_from(rest, p); p += 4
        for _ in range(n):
            _, p = rstr(rest, p); _, p = rstr(rest, p + 4)
        n, = U32.unpack_from(rest, p); p += 4 + 4 * n
        ex = bs + q + p
        _, cnt = struct.unpack_from("<II", d, ex)
        assert ex + 8 + cnt * 30 + 4 == be, "object list size mismatch"
        assert 0 <= index < cnt
        return ex + 8 + index * 30
    raise ValueError(f"object list {ol_id} not found")

def ref_offset(d, start, obj_id, pname):
    for cls, oid, bs, be, q in walk(d, start):
        if oid != obj_id: continue
        rest = d[bs+q:be]
        _, p = skip_props(rest)
        n, = U32.unpack_from(rest, p); p += 4
        for _ in range(n):
            name, p = rstr(rest, p)
            if name == pname: return bs + q + p          # offset of the i32 id
            _, p = rstr(rest, p + 4)                     # skip id + class string
        raise ValueError(f"{pname} not found on {obj_id}")
    raise ValueError(f"object {obj_id} not found")

def main():
    original = GAME.read_bytes()
    sp, table_end, entries = read_table(original)
    assert build_table(entries, {}) == original[sp:table_end], "string table does not round-trip"
    first_obj = table_end + 4
    props, n_objs = index_properties(original, first_obj)
    print(f"{GAME.name}: {len(entries)} strings, {n_objs} objects")

    # 1. numbers/bools: only rows that differ from the current file
    patches = {}
    with PROPS.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            oid = int(row["id"])
            for item in row["props"].split("; "):
                name, sep, value = item.partition("=")
                e = props.get((oid, name)) if sep else None
                if not e: continue
                cls, tag, off = e
                if cls != row["class"]: raise ValueError(f"class mismatch on object {oid}")
                rep = pack_change(tag, value, off, original)
                if rep: patches[off] = (rep, oid, cls, name, value)

    # 2. text: only rows that differ from the current file
    old = {sid: (key, val) for sid, key, val in entries}
    edits = {}
    with STRINGS.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            sid = int(row["id"])
            if sid not in old: raise ValueError(f"string #{sid} does not exist; adding is not supported")
            if row["key"].encode("cp1252") != old[sid][0]: raise ValueError(f"key of #{sid} changed")
            if row["text"] == esc(old[sid][1]): continue
            new = unesc(row["text"]).encode("cp1252")
            if new != old[sid][1]: edits[sid] = new

    # 3. build the output
    out = bytearray(original)
    for off, (rep, *_) in patches.items():
        out[off:off+len(rep)] = rep

    # field patches
    
    field_offs, field_log = [], []
    for fm_id, x, y, old_obj, new_obj, new_flags in FIELD_PATCHES:
        off = fieldmap_cell_offset(original, first_obj, fm_id, x, y)
        cur, jn, fl = struct.unpack_from("<iii", original, off)
        want_flags = fl if new_flags is None else new_flags
        if cur == new_obj and fl == want_flags: continue            # already applied
        if cur not in (old_obj, new_obj):
            raise ValueError(f"cell ({x},{y}) holds {cur}, expected {old_obj} or {new_obj}")
        out[off:off+4] = I32.pack(new_obj); field_offs.append(off)
        if new_flags is not None:
            out[off+8:off+12] = I32.pack(new_flags); field_offs.append(off + 8)
        field_log.append(f"fieldmap {fm_id} ({x},{y}): obj {cur} -> {new_obj}, flags {fl} -> {want_flags}")

    ref_offs, ref_log = [], []
    for oid, pname, old_id, new_id in REF_PATCHES:
        off = ref_offset(original, first_obj, oid, pname)
        cur, = I32.unpack_from(original, off)
        if cur == new_id: continue                       # already applied
        if cur != old_id:
            raise ValueError(f"{oid}.{pname} holds {cur}, expected {old_id} or {new_id}")
        out[off:off+4] = I32.pack(new_id)
        ref_offs += [off + i for i in range(4)]
        ref_log.append(f"{oid}.{pname}: {cur} -> {new_id}")

    #object list patches

    objlist_offs, objlist_log = [], 0
    for ol_id, idx, old_id, hexrec in OBJLIST_PATCHES:
        new = bytes.fromhex(hexrec); assert len(new) == 30
        off = objlist_record_offset(original, first_obj, ol_id, idx)
        cur = bytes(original[off:off+30])
        if cur == new: continue                                   # already applied
        if U32.unpack_from(cur, 0)[0] != old_id:
            raise ValueError(f"list {ol_id} record {idx} holds mesh {U32.unpack_from(cur,0)[0]}, expected {old_id}")
        out[off:off+30] = new
        objlist_offs += [off + i for i in range(30)]; objlist_log += 1

    # 4. verify before writing
    delta = 0
    if edits:
        new_table = build_table(entries, edits)
        delta = len(new_table) - (table_end - sp)
        h = original.find(b"\x19\x00\x00\x00CKKStringDatabaseResource")
        if not 0 <= h < sp: raise ValueError("string database header not found")
        _, p = rbytes(original, h); _, q = rbytes(original, p)
        old_size, = U32.unpack_from(original, q)
        if not 0 <= q + 4 + old_size - table_end <= 0x100:
            raise ValueError("size field does not cover the string table")
        out = bytearray(bytes(out[:sp]) + new_table + bytes(out[table_end:]))
        U32.pack_into(out, q, old_size + delta)

    out = bytes(out)
    if out == original:
        print("no changes: the file already matches the TSVs, field patches and object patches; nothing written")
        return
    
    sp2, te2, ent2 = read_table(out)
    got = {s: v for s, _, v in ent2}
    assert len(ent2) == len(entries) and all(got[s] == v for s, v in edits.items())
    _, n2 = index_properties(out, te2 + 4)
    assert n2 == n_objs, "object count changed"
    a, b = out[te2:], original[table_end:]
    assert len(a) == len(b)
    allowed = {off - table_end + i for off, (rep, *_) in patches.items() for i in range(len(rep))}
    allowed |= {o - table_end for o in ref_offs}
    allowed |= {off - table_end + i for off in field_offs for i in range(4)}
    allowed |= {off - table_end for off in objlist_offs}   # <- add this (each entry is already one byte offset)
    bad = [i for i in range(len(a)) if a[i] != b[i] and i not in allowed]
    assert not bad, f"unexpected byte changes after the table, first at +{bad[0]:#x}"

    # 5. write in place (temp file, then atomic replace; no copies are kept)
    tmp = GAME.with_name(GAME.name + ".tmp")
    tmp.write_bytes(out)
    os.replace(tmp, GAME)

    print(f"wrote {GAME.name}: {len(out)} bytes ({len(out)-len(original):+d})")
    for off, (rep, oid, cls, name, value) in patches.items():
        print(f"  object {oid} ({cls}) {name} = {value}")
    for sid, nb in edits.items():
        print(f"  #{sid}: {old[sid][1]!r} -> {nb!r}")
    for line in field_log: print("  " + line)
    print(f" {objlist_log} object-list records patched")

if __name__ == "__main__":
    main()