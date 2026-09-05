import struct
from converter.bytecode import parse_class

def u2(b, o): return struct.unpack_from('>H', b, o)[0]
def u4(b, o): return struct.unpack_from('>I', b, o)[0]

d = open('tests/_ModFoods.class','rb').read()
cp, field_names, bodies = parse_class(d)

for idx in sorted(bodies):
    code = bodies[idx]
    code_len = u4(code, 2)
    bc = code[6:6+code_len]
    # print first 60 bytes hex + ascii
    print(f"=== lambda$static${idx}  ({field_names[idx] if idx < len(field_names) else '?'}) len={code_len} ===")
    print(bc[:80].hex())
    print(''.join(chr(b) if 32 <= b < 127 else '.' for b in bc[:80]))