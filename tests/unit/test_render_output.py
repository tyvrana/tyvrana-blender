"""Production output resource budgets, formats and safe artifact handling."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.artifacts import ArtifactSpool, artifact_path
from tyvrana_blender.models import RenderArguments


@pytest.mark.parametrize(
    "args",
    [
        {"width": 4096, "height": 4096},
        {"width": 2048, "height": 2048, "frames": [1, 2, 3, 4, 5]},
        {"format": "exr"},
        {"format": "png", "bit_depth": 32},
        {"format": "exr", "bit_depth": 16, "passes": ["normal"]},
        {"frames": [1, 1]},
        {"frames": [1, 2], "show_result": True},
        {"format": "exr_multilayer", "bit_depth": 16, "passes": ["z", "z"]},
        {"budget": {"max_artifact_bytes": 134217729}},
        {"budget": {"max_seconds": float("inf")}},
        {
            "format": "exr_multilayer",
            "bit_depth": 32,
            "width": 2048,
            "height": 2048,
            "passes": ["z"] * 9,
        },
    ],
)
def test_explicit_output_admission(args: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RenderArguments.model_validate(args)


def test_explicit_production_budget_and_media_spool() -> None:
    args = RenderArguments.model_validate(
        dict(
            width=4096,
            height=4096,
            format="exr",
            bit_depth=16,
            budget=dict(max_pixels=16777216),
        )
    )
    assert args.width == 4096
    with_spool = ArtifactSpool()
    try:
        for media in ("image/png", "image/x-exr", "application/zip"):
            with with_spool.reserve(media) as (identifier, path):
                path.write_bytes(b"binary fixture")
                descriptor = with_spool.describe(identifier, media)
                assert artifact_path(with_spool.root, descriptor) == path
                assert descriptor.media_type == media
                with_spool.release((descriptor,))
                assert not path.exists()
    finally:
        with_spool.close()
