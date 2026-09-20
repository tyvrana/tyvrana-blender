from pathlib import Path

import pytest

from tyvrana_blender.errors import OperationError
from tyvrana_blender.models import RenderArguments
from tyvrana_blender.review_packet import Packet, inventory


def test_publish_replace_and_preserve_modified_evidence(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    source.write_bytes(b"image-test")
    target = tmp_path / "review"
    packet = Packet(target, overwrite=False, maximum=4096)
    packet.add(source, "01_front.png", width=2048)
    packet.publish()
    packet.close()
    before = inventory(target)
    with pytest.raises(OperationError, match="already exists"):
        Packet(target, overwrite=False, maximum=4096)
    replacement = Packet(target, overwrite=True, maximum=4096)
    replacement.add(source, "02_left.png")
    (target / "01_front.png").write_bytes(b"user-edit")
    with pytest.raises(OperationError, match="intact"):
        replacement.publish()
    replacement.close()
    assert (target / "01_front.png").read_bytes() == b"user-edit"
    assert not list(tmp_path.glob(".tyvrana-review-*"))
    (target / "01_front.png").write_bytes(b"image-test")
    assert inventory(target) == before
    replacement = Packet(target, overwrite=True, maximum=4096)
    replacement.add(source, "02_left.png")
    replacement.publish()
    replacement.close()
    assert set(inventory(target)) == {"02_left.png", "manifest.json"}


def test_packet_rejects_user_directory_symlink_and_budget(tmp_path: Path) -> None:
    target = tmp_path / "user"
    target.mkdir()
    (target / "note.txt").write_text("keep")
    with pytest.raises(OperationError):
        Packet(target, overwrite=True, maximum=4096)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OperationError):
        Packet(link, overwrite=True, maximum=4096)
    packet = Packet(tmp_path / "new", overwrite=False, maximum=2)
    with pytest.raises(OperationError, match="max_artifact_bytes"):
        packet.add(target / "note.txt", "01_view.png")
    packet.close()
    assert (target / "note.txt").read_text() == "keep"


def test_native_review_requires_explicit_total_budget() -> None:
    args = {
        "width": 2048,
        "height": 2048,
        "inspection": {"packet": {"directory": "/tmp/review"}},
    }
    with pytest.raises(ValueError, match="max_total_pixels"):
        RenderArguments.model_validate(args)
    value = RenderArguments.model_validate(
        {**args, "budget": {"max_total_pixels": 40000000}}
    )
    assert value.width == 2048
    with pytest.raises(ValueError):
        RenderArguments.model_validate({**args, "inspection": {}})
    nine_views = {
        "width": 1600,
        "height": 1600,
        "inspection": {
            "views": [{"name": f"view{i}", "orientation": "front"} for i in range(9)],
            "packet": {"directory": "/tmp/review", "preview_size": 512},
        },
        "budget": {"max_total_pixels": 24000000},
    }
    with pytest.raises(
        ValueError,
        match="requires 25399296 total pixels.*max_total_pixels=24000000",
    ):
        RenderArguments.model_validate(nine_views)
    assert (
        RenderArguments.model_validate(
            {**nine_views, "budget": {"max_total_pixels": 25399296}}
        ).budget.max_total_pixels
        == 25399296
    )
