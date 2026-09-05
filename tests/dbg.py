import struct
from converter.bytecode import parse_class, _u2, _u4, _cp_entry_str

d = open('tests/_ModFoods.class','rb').read()
cp, field_names, bodies = parse_class(d)
print('fields', len(field_names), 'bodies', len(bodies))
print('field[0:5]', field_names[:5])

# re-parse manually after fields to inspect method offsets
cp_count = _u2(d, 8)
cp = [None]; o = 10; i = 1
while i < cp_count:
    tag = d[o]; o += 1
    if tag == 1:
        ln = _u2(d, o); o += 2; cp.append(d[o:o+ln].decode('utf-8','replace')); o += ln
    elif tag in (3,4): cp.append((tag, _u4(d,o))); o += 4
    elif tag in (5,6): cp.append((tag, 0.0)); o += 8; cp.append(None); i += 1
    elif tag == 7: cp.append(('Class',_u2(d,o))); o += 2
    elif tag == 8: cp.append(('String',_u2(d,o))); o += 2
    elif tag in (9,10,11): cp.append((tag,_u2(d,o),_u2(d,o+2))); o += 4
    elif tag == 12: cp.append(('NameAndType',_u2(d,o),_u2(d,o+2))); o += 4
    elif tag == 15: cp.append(('MH',_u2(d,o))); o += 3
    elif tag == 16: cp.append(('MT',_u2(d,o))); o += 2
    elif tag == 18: cp.append(('InvDyn',_u2(d,o),_u2(d,o+2))); o += 4
    elif tag in (19,20): cp.append((tag,_u2(d,o))); o += 2
    else: cp.append(('?',tag))
    i += 1

o += 6
ifc = _u2(d, o); o += 2 + 2*ifc
fcount = _u2(d, o); o += 2
print('after pool header offset', o, 'ifc', ifc, 'fcount', fcount)
for k in range(fcount):
    fa = _u2(d,o); fn = _u2(d,o+2); fd = _u2(d,o+4)
    ac = _u2(d, o+6)
    print(f'field {k}: fa={fa} fn={fn}({_cp_entry_str(cp,fn)}) ac={ac}')
    o += 6
    # skip attributes properly
    for _ in range(ac):
        al = _u4(d, o+2)
        o += 6 + al

mcount = _u2(d, o); o += 2
print('mcount', mcount, 'offset', o)
for k in range(mcount):
    ma = _u2(d,o); mn = _u2(d,o+2); md = _u2(d,o+4); mac = _u2(d,o+6)
    mname = _cp_entry_str(cp, mn)
    print(f'method {k}: name={mname} mac={mac}')
    o += 8
    for _ in range(mac):
        an = _u2(d,o); al = _u4(d,o+2)
        aname = _cp_entry_str(cp, an)
        print(f'   attr {aname} len={al} at {o}')
        o += 6 + al
    break