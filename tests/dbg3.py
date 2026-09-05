from converter.bytecode import parse_class, _u4

d = open('tests/_ModFoods.class','rb').read()
cp, fields, bodies = parse_class(d)
print('n bodies', len(bodies))
c = bodies.get(37)
print('c len', len(c) if c else None)
if c:
    print('code_len field', _u4(c, 2))
    print(c.hex())
    # the first few entries in constant pool (Integer/Float)
    for i in range(1, min(len(cp), 120)):
        e = cp[i]
        if isinstance(e, tuple) and e[0] in (3, 4):
            print('cp', i, e)