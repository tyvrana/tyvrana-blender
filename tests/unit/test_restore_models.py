"""Restore discovery preserves the canonical guarded contract and explicit consent."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.operations import REGISTRY
from tyvrana_blender.restore_models import RestoreArguments


def test_restore_is_guarded_and_reuses_document_open() -> None:
    assert "document_restore" in REGISTRY["blender.document.restore"].contract.tags
    assert "document_open" in REGISTRY["blender.file.open"].contract.tags
    schema = RestoreArguments.model_json_schema()
    assert schema["properties"]["discard_current"]["const"] is True
    assert {"current", "target", "discard_current"} <= set(schema["required"])
    with pytest.raises(ValidationError):
        RestoreArguments(
            mutation_id="restore",
            operation="blender.file.open",
            locator="/trusted.blend",
            discard_current=False,  # type: ignore[arg-type]
            current={},
            target={},
        )
