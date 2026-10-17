"""Per-request structured-output schemas for Gemini.

A static schema only fixes the shape of a response. These schemas are built
for each request from the actual layout, so the decoder can only produce
values that exist: room IDs, fact IDs and floor numbers are enums, and each
change action is its own variant with its required fields. Gemini's output is
constrained by the JSON schema and then re-validated with the same Pydantic
models; a response that still does not conform is retried once and otherwise
reported as an error, never repaired by guesswork.
"""

from typing import Literal, Union

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from . import gemini
from .program import RULES, REMOVABLE, Change, Program

ADDABLE_TYPES = tuple(t for t in RULES if t not in ("hall", "corridor", "terrace"))


class AIOutputInvalid(RuntimeError):
    """Gemini returned output that does not satisfy the request's schema."""


def _enum(values) -> object:
    values = tuple(dict.fromkeys(values))
    if not values:
        raise ValueError("an enum needs at least one value")
    return Literal[values]  # type: ignore[valid-type]


def change_variants(program: Program) -> type:
    """The union of change actions that are possible for this programme, with IDs as enums."""
    SpaceId = _enum(s.id for s in program.spaces)
    Zoneable = _enum(s.id for s in program.spaces if not s.vertical_core and s.type != "garage")

    class SetMinArea(BaseModel):
        action: Literal["set_min_area"]
        space_id: SpaceId
        value_m2: float = Field(gt=0, le=200, description="Absolute minimum area in m²")

    class SetZone(BaseModel):
        action: Literal["set_zone"]
        space_id: Zoneable
        zone: Literal["front", "middle", "rear"]

    class RequireExterior(BaseModel):
        action: Literal["require_exterior"]
        space_id: SpaceId

    class RequireAdjacent(BaseModel):
        action: Literal["require_adjacent"]
        space_id: SpaceId
        other_space_id: SpaceId

    class AddRoom(BaseModel):
        action: Literal["add_room"]
        room_type: _enum(ADDABLE_TYPES)
        name: str = Field(min_length=1, max_length=60)
        floor: _enum(range(program.storeys))

    variants: list[type] = [SetMinArea, SetZone, RequireExterior, RequireAdjacent, AddRoom]
    movable = [s.id for s in program.spaces if s.source == "brief" and not s.vertical_core and s.type != "garage"]
    if program.storeys > 1 and movable:
        class SetFloor(BaseModel):
            action: Literal["set_floor"]
            space_id: _enum(movable)
            floor: _enum(range(program.storeys))
        variants.append(SetFloor)
    removable = [s.id for s in program.spaces if s.id in REMOVABLE]
    if removable:
        class RemoveRoom(BaseModel):
            action: Literal["remove_room"]
            space_id: _enum(removable)
        variants.append(RemoveRoom)
    return Union[tuple(variants)]  # type: ignore[return-value]


def to_change(variant: BaseModel) -> Change:
    return Change(**variant.model_dump())


def gemini_schema(node):
    """Rewrite a Pydantic JSON schema into the subset Gemini's decoder enforces.

    Gemini constrains `enum` but not `const`, so a single-value literal (each change
    variant's `action`) is sent as a one-value enum. The meaning is unchanged and the
    response is still validated against the original Pydantic model.
    """
    if isinstance(node, dict):
        out = {k: gemini_schema(v) for k, v in node.items() if k != "const"}
        if "const" in node:
            out["enum"] = [node["const"]]
        return out
    if isinstance(node, list):
        return [gemini_schema(v) for v in node]
    return node


def generate(model_cls: type[BaseModel], *, model: str, contents, system: str, temperature: float) -> BaseModel:
    """Call Gemini with `model_cls` as the enforced response schema and validate the result."""
    config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                         response_json_schema=gemini_schema(model_cls.model_json_schema()),
                                         temperature=temperature)
    last: Exception | None = None
    for _ in range(2):
        response = gemini.generate_content(model=model, contents=contents, config=config)
        try:
            return model_cls.model_validate_json(response.text or "")
        except ValidationError as e:
            last = e
    raise AIOutputInvalid(f"Gemini's response did not match the required schema: {last}")
