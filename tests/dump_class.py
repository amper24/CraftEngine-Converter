"""Dump a .class file: constant pool + method bytecode (research helper)."""
import struct, sys

def u1(b, o): return b[o]
def u2(b, o): return struct.unpack_from('>H', b, o)[0]
def u4(b, o): return struct.unpack_from('>I', b, o)[0]

def parse(path):
    b = open(path,'rb').read()
    cp_count = u2(b, 8)
    cp = [None]
    o = 10
    i = 1
    while i < cp_count:
        tag = u1(b, o); o += 1
        if tag == 1:
            ln = u2(b, o); o += 2
            cp.append(('Utf8', b[o:o+ln].decode('utf-8','replace'))); o += ln
        elif tag in (3,4):
            v = u4(b, o); o += 4; cp.append((tag, v))
        elif tag in (5,6):
            v = struct.unpack_from('>d' if tag==6 else '>Q', b, o)[0] if False else None
            # long/double take two slots
            if tag == 5:
                v = struct.unpack_from('>q', b, o)[0]; o += 8
            else:
                v = struct.unpack_from('>d', b, o)[0]; o += 8
            cp.append((tag, v)); cp.append(None); i += 1
        elif tag == 7:
            cp.append(('Class', u2(b,o))); o += 2
        elif tag == 8:
            cp.append(('String', u2(b,o))); o += 2
        elif tag in (9,10,11):
            cp.append((tag, u2(b,o), u2(b,o+2))); o += 4
        elif tag == 12:
            cp.append(('NameAndType', u2(b,o), u2(b,o+2))); o += 4
        elif tag == 15:
            cp.append(('MethodHandle', u1(b,o), u2(b,o+1))); o += 3
        elif tag == 16:
            cp.append(('MethodType', u2(b,o))); o += 2
        elif tag == 18:
            cp.append(('InvokeDynamic', u2(b,o), u2(b,o+2))); o += 4
        elif tag == 19 or tag == 20:
            cp.append((tag, u2(b,o))); o += 2
        else:
            raise Exception(f'unknown tag {tag} at {o}')
        i += 1

    access, this, super_ = u2(b,o), u2(b,o+2), u2(b,o+4); o += 6
    ifc = u2(b, o); o += 2 + 2*ifc
    fcount = u2(b, o); o += 2
    fields = []
    for _ in range(fcount):
        fa, fn, fd = u2(b,o), u2(b,o+2), u2(b,o+4); o += 6
        ac = u2(b, o); o += 2
        fields.append((cp[fn][1], ac))
    mcount = u2(b, o); o += 2
    methods = []
    for _ in range(mcount):
        ma, mn, md, mac = u2(b,o), u2(b,o+2), u2(b,o+4), u2(b,o+6); o += 8
        attrs = []
        for _ in range(mac):
            an, al = u2(b,o), u4(b,o+2); o += 6
            attrs.append((cp[an][1], b[o:o+al])); o += al
        methods.append((cp[mn][1], attrs))
    return cp, fields, methods

def main():
    cp, fields, methods = parse(sys.argv[1])
    print('=== FIELDS ===')
    for name, ac in fields:
        print(name)
    print('=== METHODS ===')
    for name, attrs in methods:
        print(name, '->', [a[0] for a in attrs])

if __name__ == '__main__':
    main()