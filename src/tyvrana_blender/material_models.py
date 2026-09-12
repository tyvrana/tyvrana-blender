"""Named material resources, numeric shader inputs, and object slot results."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .models import Model, ObjectName
from .numeric import Float32, Nonnegative32, Vector32

type ShaderColor = Annotated[list[Nonnegative32], Field(min_length=3, max_length=3)]
type SlotIndex = Annotated[int, Field(ge=0)]
type Surface = Literal["principled", "custom", "none"]

PRINCIPLED_SOCKETS = {
    "base_color": "Base Color",
    "metallic": "Metallic",
    "roughness": "Roughness",
    "ior": "IOR",
    "alpha": "Alpha",
    "subsurface_weight": "Subsurface Weight",
    "subsurface_radius": "Subsurface Radius",
    "subsurface_scale": "Subsurface Scale",
    "transmission_weight": "Transmission Weight",
    "coat_weight": "Coat Weight",
    "coat_roughness": "Coat Roughness",
    "emission_color": "Emission Color",
    "emission_strength": "Emission Strength",
}


class PrincipledSummary(Model):
    base_color: ShaderColor
    metallic: Float32
    roughness: Float32
    ior: Float32
    alpha: Float32
    subsurface_weight: Float32
    subsurface_radius: Vector32
    subsurface_scale: Float32
    transmission_weight: Float32
    coat_weight: Float32
    coat_roughness: Float32
    emission_color: ShaderColor
    emission_strength: Float32


class MaterialAssignment(Model):
    object: str
    slot: SlotIndex


class MaterialSummary(Model):
    name: str
    surface: Surface
    principled: PrincipledSummary | None
    assignments: list[MaterialAssignment]

    @model_validator(mode="after")
    def matching_surface(self) -> Self:
        if (self.surface == "principled") != (self.principled is not None):
            raise ValueError(
                "Principled values require a recognized Principled surface"
            )
        return self


class MaterialInspectResult(Model):
    materials: list[MaterialSummary]


class PrincipledPatch(Model):
    base_color: ShaderColor | None = None
    metallic: Float32 | None = None
    roughness: Float32 | None = None
    ior: Float32 | None = None
    alpha: Float32 | None = None
    subsurface_weight: Float32 | None = None
    subsurface_radius: Vector32 | None = None
    subsurface_scale: Float32 | None = None
    transmission_weight: Float32 | None = None
    coat_weight: Float32 | None = None
    coat_roughness: Float32 | None = None
    emission_color: ShaderColor | None = None
    emission_strength: Float32 | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an unchanged property; null is invalid")
        return value


class MaterialCreateArguments(PrincipledPatch):
    name: ObjectName | None = None


class MaterialConfigureArguments(PrincipledPatch):
    name: ObjectName


class MaterialAssignArguments(Model):
    object_name: ObjectName
    material_name: ObjectName
    slot_index: SlotIndex = 0

    def checked_slot(self, count: int) -> int:
        if self.slot_index > count:
            raise ValueError("slot_index cannot leave a gap in the material slots")
        return self.slot_index


class MaterialAssignResult(Model):
    object_name: str
    assigned_slot: SlotIndex
    material_name: str
    slots: list[str | None]

    @model_validator(mode="after")
    def assigned_material(self) -> Self:
        if (
            self.assigned_slot >= len(self.slots)
            or self.slots[self.assigned_slot] != self.material_name
        ):
            raise ValueError("Assigned slot must contain the requested material")
        return self
