import struct
from converter.bytecode import parse_class, _u4

d = open('tests/_ModFoods.class','rb').read()
cp, fields, bodies = parse_class(d)

# print code bytes of a few food lambdas fully decoded as opcode hints
NAMES = {'BAKED_SWEET_POTATO','CARROT_CAKE_SLICE','BEETROOT_BROWNIE'}
for idx in sorted(bodies):
    name = fields[idx].lower() if idx < len(fields) else '?'
    if name not in {n.lower() for n in NAMES}:
        continue
    code = bodies[idx]
    code_len = _u4(code, 2)
    bc = code[6:6+code_len]
    print(f'=== {name} len={code_len} ===')
    print(bc.hex())