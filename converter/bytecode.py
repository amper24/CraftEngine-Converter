"""Bytecode-level extraction of food properties from compiled Minecraft mods.

The resource pack tells us how an item looks, but food values are normally
stored in Java/Kotlin bytecode.  This module intentionally performs a small
JVM class-file parse instead of depending on a JDK/decompiler being installed.

Supported patterns include both Mojang/NeoForge style::

    new FoodProperties.Builder().nutrition(4).saturationModifier(0.4F).build()

and Fabric/Yarn style::

    new FoodComponent.Builder().nutrition(4).saturationModifier(0.4F).build()

The extracted ``saturation_modifier`` is retained as source evidence.  The
``saturation`` value is calculated using vanilla's formula:

    2 * nutrition * saturation_modifier

which is the value represented by CraftEngine's ``data.food.saturation``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, asdict
from typing import Any, Iterator
import re


@dataclass(frozen=True)
class FoodValue:
    field: str
    nutrition: int
    saturation_modifier: float
    can_always_eat: bool = False
    source_class: str = ""
    source_method: str = ""

    @property
    def saturation(self) -> float:
        return float(2.0 * self.nutrition * self.saturation_modifier)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["saturation"] = self.saturation
        return data


# JVM opcodes used directly by the tiny abstract interpreter.
ICONST_VALUES = {2: -1, 3: 0, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5}
FCONST_VALUES = {11: 0.0, 12: 1.0, 13: 2.0}
RETURNISH = {172, 173, 174, 175, 176, 177}


@dataclass
class CpEntry:
    tag: int
    a: Any = None
    b: Any = None


def _u1(b: bytes, o: int) -> int:
    return b[o]


def _u2(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def _u4(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def _s4(b: bytes, o: int) -> int:
    return struct.unpack_from(">i", b, o)[0]


def _cp_utf8(cp: list[CpEntry | None], index: int) -> str | None:
    if index <= 0 or index >= len(cp):
        return None
    e = cp[index]
    return e.a if e and e.tag == 1 else None


def _cp_name_type(cp: list[CpEntry | None], index: int) -> tuple[str, str] | None:
    if index <= 0 or index >= len(cp):
        return None
    e = cp[index]
    if not e or e.tag != 12:
        return None
    name = _cp_utf8(cp, e.a)
    desc = _cp_utf8(cp, e.b)
    return (name or "", desc or "")


def _cp_ref(cp: list[CpEntry | None], index: int) -> tuple[str, str, str] | None:
    if index <= 0 or index >= len(cp):
        return None
    e = cp[index]
    if not e or e.tag not in (9, 10, 11):
        return None
    owner_e = cp[e.a]
    owner = _cp_utf8(cp, owner_e.a) if owner_e and owner_e.tag == 7 else None
    nt = _cp_name_type(cp, e.b)
    if not nt:
        return None
    return owner or "", nt[0], nt[1]


def _cp_constant(cp: list[CpEntry | None], index: int) -> Any:
    if index <= 0 or index >= len(cp):
        return None
    e = cp[index]
    if not e:
        return None
    if e.tag in (3, 4, 5, 6):
        if e.tag == 3:
            return int(e.a)
        if e.tag == 4:
            return struct.unpack(">f", struct.pack(">I", e.a))[0]
        return e.a
    if e.tag == 8:
        return _cp_utf8(cp, e.a)
    if e.tag == 1:
        return e.a
    return None


def parse_class(b: bytes) -> tuple[list[CpEntry | None], list[tuple[str, str, int]], dict[str, bytes]]:
    """Return constant pool, fields and Code-attribute bodies by method name."""
    if len(b) < 10 or b[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a JVM class file")

    cp_count = _u2(b, 8)
    cp: list[CpEntry | None] = [None]
    o = 10
    i = 1
    while i < cp_count:
        tag = b[o]
        o += 1
        if tag == 1:
            ln = _u2(b, o)
            o += 2
            cp.append(CpEntry(tag, b[o:o + ln].decode("utf-8", "replace")))
            o += ln
        elif tag in (3, 4):
            cp.append(CpEntry(tag, _u4(b, o)))
            o += 4
        elif tag in (5, 6):
            raw = b[o:o + 8]
            value = struct.unpack(">q" if tag == 5 else ">d", raw)[0]
            cp.append(CpEntry(tag, value))
            cp.append(None)
            o += 8
            i += 1
        elif tag in (7, 8, 16, 19, 20):
            cp.append(CpEntry(tag, _u2(b, o)))
            o += 2
        elif tag in (9, 10, 11, 12, 17, 18):
            cp.append(CpEntry(tag, _u2(b, o), _u2(b, o + 2)))
            o += 4
        elif tag == 15:
            cp.append(CpEntry(tag, _u1(b, o), _u2(b, o + 1)))
            o += 3
        else:
            raise ValueError(f"unsupported constant-pool tag {tag}")
        i += 1

    o += 6  # access_flags, this_class, super_class
    interfaces = _u2(b, o)
    o += 2 + interfaces * 2

    field_count = _u2(b, o)
    o += 2
    fields: list[tuple[str, str, int]] = []
    for _ in range(field_count):
        access = _u2(b, o)
        name_i = _u2(b, o + 2)
        desc_i = _u2(b, o + 4)
        o += 6
        attr_count = _u2(b, o)
        o += 2
        for _ in range(attr_count):
            _an = _u2(b, o)
            al = _u4(b, o + 2)
            o += 6 + al
        fields.append((_cp_utf8(cp, name_i) or "", _cp_utf8(cp, desc_i) or "", access))

    method_count = _u2(b, o)
    o += 2
    bodies: dict[str, bytes] = {}
    for _ in range(method_count):
        _access = _u2(b, o)
        name_i = _u2(b, o + 2)
        _desc_i = _u2(b, o + 4)
        attr_count = _u2(b, o + 6)
        o += 8
        method_name = _cp_utf8(cp, name_i) or ""
        for _ in range(attr_count):
            attr_name_i = _u2(b, o)
            al = _u4(b, o + 2)
            info_start = o + 6
            if _cp_utf8(cp, attr_name_i) == "Code":
                # Keep the complete Code attribute body; caller handles the header.
                bodies[method_name] = b[info_start:info_start + al]
            o += 6 + al

    return cp, fields, bodies


def _opcode_width(code: bytes, pc: int) -> int:
    """Return JVM instruction width; handles all fixed-width opcodes and switches."""
    op = code[pc]
    fixed_1 = {
        0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,46,47,48,49,50,51,52,53,79,80,81,82,83,84,85,86,
        87,88,89,90,91,92,93,94,95,96,97,98,99,100,101,102,103,104,105,106,107,108,109,110,111,
        112,113,114,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,130,131,133,134,
        135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152,172,173,174,175,
        176,177,190,191,194,195,202,254,255
    }
    fixed_2 = {16,18,21,25,54,55,56,57,58,169,188}
    fixed_3 = {17,19,20,132,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,178,179,180,181,187,189,192,193,198,199}
    fixed_4 = {18 + 256}  # unreachable sentinel; keeps table explicit below
    if op in fixed_1:
        return 1
    if op in fixed_2:
        return 2
    if op in fixed_3:
        return 3
    if op in (182, 183, 184, 185, 186):
        return 4 if op == 185 else 3
    if op == 197:
        return 4
    if op == 196:  # wide
        sub = code[pc + 1]
        return 6 if sub == 132 else 4
    if op == 200 or op == 201:
        return 5
    if op == 170:  # tableswitch
        pad = (4 - ((pc + 1) & 3)) & 3
        p = pc + 1 + pad
        low = _s4(code, p + 4)
        high = _s4(code, p + 8)
        return 1 + pad + 12 + max(0, high - low + 1) * 4
    if op == 171:  # lookupswitch
        pad = (4 - ((pc + 1) & 3)) & 3
        p = pc + 1 + pad
        npairs = _s4(code, p + 4)
        return 1 + pad + 8 + max(0, npairs) * 8
    # xaload/xastore and the remaining no-operand instructions are 1 byte.
    # Most JVM lambdas/static initializers use only the above set.
    return 1


def _code_bytes(code_attr: bytes) -> bytes:
    if len(code_attr) < 8:
        return b""
    code_len = _u4(code_attr, 4)
    return code_attr[8:8 + code_len]


def _iter_instructions(code: bytes) -> Iterator[tuple[int, int, bytes]]:
    pc = 0
    while pc < len(code):
        width = _opcode_width(code, pc)
        if width <= 0 or pc + width > len(code):
            width = 1
        yield pc, code[pc], code[pc:pc + width]
        pc += width


def _builder_owner(owner: str) -> bool:
    return owner.endswith("/FoodProperties$Builder") or owner.endswith("/FoodComponent$Builder") or \
        owner.endswith("/FoodProperties$Builder") or owner.endswith("/FoodComponent$Builder")


def _extract_method_foods(cp: list[CpEntry | None], method_name: str, code_attr: bytes) -> list[FoodValue]:
    code = _code_bytes(code_attr)
    stack: list[Any] = []
    current_builder = False
    nutrition: int | None = None
    saturation_modifier: float | None = None
    always_edible = False
    results: list[FoodValue] = []

    def pop(default: Any = None) -> Any:
        return stack.pop() if stack else default

    for _pc, op, raw in _iter_instructions(code):
        if op in ICONST_VALUES:
            stack.append(ICONST_VALUES[op])
            continue
        if op in FCONST_VALUES:
            stack.append(FCONST_VALUES[op])
            continue
        if op == 16:  # bipush
            stack.append(struct.unpack(">b", raw[1:2])[0])
            continue
        if op == 17:  # sipush
            stack.append(struct.unpack(">h", raw[1:3])[0])
            continue
        if op in (18, 19):
            idx = raw[1] if op == 18 else _u2(raw, 1)
            stack.append(_cp_constant(cp, idx))
            continue
        if op in (1,):
            stack.append(None)
            continue
        if op == 187:  # new
            idx = _u2(raw, 1)
            owner_e = cp[idx] if idx < len(cp) else None
            owner = _cp_utf8(cp, owner_e.a) if owner_e and owner_e.tag == 7 else ""
            marker = "builder" if owner and _builder_owner(owner) else "object"
            stack.append(marker)
            if marker == "builder":
                current_builder = True
                nutrition = None
                saturation_modifier = None
                always_edible = False
            continue
        if op in (89,):  # dup
            stack.append(stack[-1] if stack else None)
            continue
        if op == 87:  # pop
            pop()
            continue
        if op in (178, 179, 180, 181, 182, 183, 184, 185, 186):
            idx = _u2(raw, 1)
            ref = _cp_ref(cp, idx) if idx < len(cp) else None
            owner, name, desc = ref if ref else ("", "", "")
            if op == 186:  # invokedynamic: opaque result
                stack.append(None)
                continue
            # Field operations.
            if op == 178:  # getstatic
                stack.append(("field", owner, name, desc))
                continue
            if op in (179,):  # putstatic
                value = pop()
                if current_builder and name and desc and ("FoodProperties" in desc or "FoodComponent" in desc):
                    # A builder uses vanilla defaults when nutrition/saturation
                    # were omitted, so retain zeroes rather than dropping a food.
                    results.append(FoodValue(
                        field=name,
                        nutrition=int(nutrition if nutrition is not None else 0),
                        saturation_modifier=float(saturation_modifier if saturation_modifier is not None else 0.0),
                        can_always_eat=always_edible,
                        source_method=method_name,
                    ))
                current_builder = False
                continue

            if op in (182, 183, 184, 185):
                # Invoke arguments from the descriptor.  We only care about
                # Builder methods; preserve a representative return marker.
                args_n = desc.count("[") + desc.count("L") + desc.count("F") + desc.count("I") + desc.count("Z") + desc.count("J") + desc.count("D") + desc.count("B") + desc.count("C") + desc.count("S")
                # Descriptor parser above is intentionally conservative; for
                # the supported methods exact arities are known.
                if name in ("nutrition",):
                    value = pop()
                    pop()  # builder receiver
                    if isinstance(value, int):
                        nutrition = value
                    stack.append("builder")
                    current_builder = True
                    continue
                if name in ("saturationModifier", "saturationMod"):
                    value = pop()
                    pop()
                    if isinstance(value, (int, float)):
                        saturation_modifier = float(value)
                    stack.append("builder")
                    current_builder = True
                    continue
                if name in ("alwaysEdible",):
                    pop()
                    always_edible = True
                    stack.append("builder")
                    current_builder = True
                    continue
                if name in ("fast", "effect", "usingConvertsTo", "usingConvertsTo"):
                    # effect has supplier + probability; usingConvertsTo has a
                    # single item. Consume only enough to keep builder on top.
                    if name == "effect":
                        pop(); pop(); pop()
                    else:
                        pop(); pop()
                    stack.append("builder")
                    current_builder = True
                    continue
                if name == "build":
                    pop()
                    stack.append("food_object")
                    continue
                # Generic invocation: consume receiver plus a best-effort
                # number of arguments and push an unknown return value if needed.
                if desc.startswith("("):
                    close = desc.find(")")
                    argdesc = desc[1:close]
                    argc = _descriptor_arity(argdesc) + (0 if op == 184 else 1)
                    for _ in range(argc):
                        pop()
                    if not desc.endswith(")V"):
                        stack.append(None)
                continue
        # Local loads are opaque values; don't accidentally treat them as constants.
        if op in (21,22,23,24,25):
            stack.append(None)
            continue
        if op in (54,55,56,57,58):
            pop()
            continue
        if op in RETURNISH:
            continue
        # Most other operations do not matter for food extraction.

    return results


def _descriptor_arity(desc: str) -> int:
    """Count JVM descriptor arguments (array/reference primitives)."""
    i = 0
    count = 0
    while i < len(desc):
        c = desc[i]
        if c == "[":
            i += 1
            while i < len(desc) and desc[i] == "[":
                i += 1
            if i < len(desc) and desc[i] == "L":
                end = desc.find(";", i)
                i = len(desc) if end < 0 else end + 1
            else:
                i += 1
            count += 1
        elif c == "L":
            end = desc.find(";", i)
            i = len(desc) if end < 0 else end + 1
            count += 1
        else:
            i += 1
            count += 1
    return count


def extract_foods(class_bytes: bytes, source_class: str = "") -> dict[str, tuple[int, float]]:
    """Backward-compatible API: return ``{FIELD: (nutrition, saturationModifier)}``."""
    foods = extract_food_properties(class_bytes, source_class=source_class)
    return {name: (v.nutrition, v.saturation_modifier) for name, v in foods.items()}


def extract_food_properties(class_bytes: bytes, source_class: str = "") -> dict[str, FoodValue]:
    cp, _fields, bodies = parse_class(class_bytes)
    result: dict[str, FoodValue] = {}
    for method_name, body in bodies.items():
        for value in _extract_method_foods(cp, method_name, body):
            result[value.field.lower()] = FoodValue(
                field=value.field,
                nutrition=value.nutrition,
                saturation_modifier=value.saturation_modifier,
                can_always_eat=value.can_always_eat,
                source_class=source_class,
                source_method=method_name,
            )
    return result


def _camel_to_snake(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def extract_crop_relations_from_archive(archive: Any) -> dict[str, dict[str, Any]]:
    """Extract crop seed/max-age relations from CropBlock subclasses.

    Looks for methods named getBaseSeedId/getMaxAge in classes whose classfile
    references Minecraft's CropBlock and resolves the getstatic field name used
    by getBaseSeedId. Registry field names normally mirror item IDs exactly,
    which lets us reconstruct otherwise ambiguous crops such as sweet potato
    and garlic without guessing from filename suffixes.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in getattr(archive, 'names', []):
        if not str(name).endswith('.class'):
            continue
        raw = archive.read(name)
        if not raw:
            continue
        try:
            cp, _fields, bodies = parse_class(raw)
        except Exception:
            continue
        if 'getBaseSeedId' not in bodies or 'getMaxAge' not in bodies:
            continue
        # Filter to crop subclasses without requiring a JVM/decompiler.
        cp_text = []
        for e in cp:
            if e and e.tag == 1 and isinstance(e.a, str):
                cp_text.append(e.a)
        if not any('CropBlock' in t for t in cp_text):
            continue

        simple = str(name).rsplit('/', 1)[-1][:-6]
        crop_name = _camel_to_snake(simple)
        if crop_name.endswith('_crop_block'):
            crop_path = crop_name[:-6]  # remove _block
        elif crop_name.endswith('cropblock'):
            crop_path = crop_name[:-5] + '_crop'
        else:
            crop_path = crop_name
        # Resolve seed from the first getstatic field reference in getBaseSeedId.
        seed_field = None
        seed_owner = None
        for i, op, raw_ins in _iter_instructions(_code_bytes(bodies['getBaseSeedId'])):
            if op in (178, 179):  # getstatic/putstatic; getstatic is expected
                if len(raw_ins) >= 3:
                    ref = _cp_ref(cp, _u2(raw_ins, 1))
                    if ref:
                        seed_owner, seed_field, _ = ref
                        break
        # Resolve max age from the tiny method body by interpreting constant load.
        max_age = None
        code = _code_bytes(bodies['getMaxAge'])
        for _pc, op, raw_ins in _iter_instructions(code):
            if op in ICONST_VALUES:
                max_age = ICONST_VALUES[op]
                break
            if op == 16 and len(raw_ins) >= 2:
                max_age = struct.unpack('>b', raw_ins[1:2])[0]
                break
            if op == 17 and len(raw_ins) >= 3:
                max_age = struct.unpack('>h', raw_ins[1:3])[0]
                break
            if op in (18, 19) and len(raw_ins) >= (2 if op == 18 else 3):
                idx = raw_ins[1] if op == 18 else _u2(raw_ins, 1)
                value = _cp_constant(cp, idx)
                if isinstance(value, int):
                    max_age = value
                    break
        if not seed_field:
            continue
        seed_id = _camel_to_snake(seed_field)
        ns = getattr(archive, 'metadata_namespace', None)
        # The analyzer supplies the namespace later; keep a namespaced-agnostic path here.
        out[crop_path] = {
            'seed_path': seed_id,
            'max_age': int(max_age) if isinstance(max_age, int) else None,
            'source_class': name,
            'seed_owner': seed_owner,
        }
    return out


def extract_foods_from_archive(archive: Any) -> dict[str, FoodValue]:
    """Scan all class files in a ModArchive and merge discovered food definitions."""
    result: dict[str, FoodValue] = {}
    for name in archive.names:
        if not name.endswith(".class"):
            continue
        raw = archive.read(name)
        if not raw:
            continue
        # Cheap prefilter: avoids parsing thousands of unrelated Minecraft/mod classes.
        if b"FoodProperties" not in raw and b"FoodComponent" not in raw:
            continue
        try:
            found = extract_food_properties(raw, source_class=name[:-6])
        except (ValueError, IndexError, struct.error):
            continue
        result.update(found)
    return result
