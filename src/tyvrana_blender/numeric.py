"""Finite inputs rounded and validated at Blender's stored precision."""

import struct
from typing import Annotated

from pydantic import AfterValidator, Field, FiniteFloat

from .models import Vector

FLOAT32_MAX = float.fromhex("0x1.fffffep+127")


def binary32(value: float) -> float:
    """Validate float32 precision without clamping or underflow to zero."""
    if not -FLOAT32_MAX <= value <= FLOAT32_MAX:
        raise ValueError("Value exceeds Blender's finite float32 range")
    rounded = float(struct.unpack("f", struct.pack("f", value))[0])
    if value != 0 and rounded == 0:
        raise ValueError("Value underflows Blender's float32 precision")
    return rounded


def vector32(value: list[float]) -> list[float]:
    return [binary32(component) for component in value]


type Float32 = Annotated[
    FiniteFloat, Field(ge=-FLOAT32_MAX, le=FLOAT32_MAX), AfterValidator(binary32)
]
type Nonnegative32 = Annotated[
    FiniteFloat, Field(ge=0, le=FLOAT32_MAX), AfterValidator(binary32)
]
type Vector32 = Annotated[Vector, AfterValidator(vector32)]
