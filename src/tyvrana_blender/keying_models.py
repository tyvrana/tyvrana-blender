"""Shared intent for atomic pose matching and bounded native action keys."""

from pydantic import Field

from .models import Model
from .reference_models import Name


class MatchKeying(Model):
    action_name: Name = Field(
        description="Create or extend this owned active action without detaching it."
    )
    anchor_frame: int = Field(
        ge=-1048574,
        le=1048573,
        description=(
            "Frame before the current integer frame. Sample destination controls here "
            "and add hold keys before the switch. Existing keys are retained; the "
            "interval from this anchor to the switch is intentionally revised."
        ),
    )
