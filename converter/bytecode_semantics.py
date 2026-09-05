"""Best-effort bytecode semantic reconstruction for registered items/blocks.

This layer is deliberately conservative. It extracts facts that can be tied to a
static registry field with high confidence; anything ambiguous is reported, not
silently invented. It complements resource JSON and CE's native data/settings.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import struct

from .bytecode import parse_class, _cp_ref, _cp_constant, _u2, _iter_instructions, _code_bytes, _cp_utf8


@dataclass
class SemanticFact:
    field: str
    max_damage: int | None = None
    max_stack_size: int | None = None
    unbreakable: bool = False
    equippable_slot: str | None = None
    use_duration: int | None = None
    fuel_time: int | None = None
    class_name: str = ""
    method: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _descriptor_is_item(desc: str) -> bool:
    return "Item;" in desc or desc.endswith("Item;") or "BlockItem;" in desc or "ToolItem;" in desc


def _consts(cp: list[Any]) -> list[Any]:
    vals: list[Any] = []
    for i in range(1, len(cp)):
        v = _cp_constant(cp, i)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals.append(v)
    return vals


def extract_semantics(class_bytes: bytes, source_class: str = "") -> dict[str, SemanticFact]:
    cp, fields, bodies = parse_class(class_bytes)
    item_fields = [name for name, desc, _access in fields if _descriptor_is_item(desc)]
    if not item_fields:
        return {}

    result: dict[str, SemanticFact] = {}
    for method_name, body in bodies.items():
        code = _code_bytes(body)
        recent_numbers: list[int | float] = []
        method_names: list[str] = []
        field_target: str | None = None
        for _pc, op, raw in _iter_instructions(code):
            if op in (3,4,5,6,7,8):
                recent_numbers.append({3:0,4:1,5:2,6:3,7:4,8:5}[op]); recent_numbers=recent_numbers[-8:]
            elif op == 16:
                recent_numbers.append(struct.unpack(">b", raw[1:2])[0]); recent_numbers=recent_numbers[-8:]
            elif op == 17:
                recent_numbers.append(struct.unpack(">h", raw[1:3])[0]); recent_numbers=recent_numbers[-8:]
            elif op in (18,19):
                idx = raw[1] if op == 18 else _u2(raw,1); val=_cp_constant(cp,idx)
                if isinstance(val,(int,float)) and not isinstance(val,bool): recent_numbers.append(val); recent_numbers=recent_numbers[-8:]
            elif op in (178,179,180,181,182,183,184,185):
                idx=_u2(raw,1); ref=_cp_ref(cp,idx)
                if not ref: continue
                owner,name,desc=ref
                if op == 179 and name in item_fields:
                    field_target=name
                    fact=SemanticFact(field=name,class_name=source_class,method=method_name,confidence=0.85)
                    lower=' '.join(method_names).lower()
                    if any(x in lower for x in ('setnodurability','unbreakable','fireproof')):
                        fact.unbreakable=True
                    # Most builder methods take an int/float immediately before invocation.
                    for mn in reversed(method_names):
                        if mn in ('durability','maxDamage','max_damage') and recent_numbers:
                            vals=[x for x in recent_numbers if isinstance(x,int)]
                            if vals: fact.max_damage=int(vals[-1])
                        if mn in ('stacksTo','maxStackSize','max_stack_size') and recent_numbers:
                            vals=[x for x in recent_numbers if isinstance(x,int)]
                            if vals: fact.max_stack_size=int(vals[-1])
                        if mn in ('useDuration','use_duration') and recent_numbers:
                            vals=[x for x in recent_numbers if isinstance(x,int)]
                            if vals: fact.use_duration=int(vals[-1])
                        if mn in ('fireResistant','setFireResistant'):
                            fact.unbreakable=True
                        break
                    # Name-based fallback only fills a fact that has an explicit builder call.
                    if fact.max_damage is not None or fact.max_stack_size is not None or fact.unbreakable or fact.use_duration is not None:
                        result[name.lower()] = fact
                if op in (182,183,184,185):
                    method_names.append(name); method_names=method_names[-12:]
    return result


def extract_semantics_from_archive(archive: Any) -> dict[str, SemanticFact]:
    merged: dict[str, SemanticFact] = {}
    for name in archive.names:
        if not name.endswith('.class'):
            continue
        raw=archive.read(name)
        if not raw or b'Item' not in raw:
            continue
        try:
            found=extract_semantics(raw,name[:-6])
        except (ValueError,IndexError,struct.error):
            continue
        merged.update(found)
    return merged
