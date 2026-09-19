"""Delivery audit bounds avoid implicit arbitrary filesystem traversal."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.delivery_models import FileAuditArguments


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 65},
        {"max_resources": 16385},
        {"required_files": [{"filepath": "bad\0path"}]},
        {"required_files": [{"filepath": "/tmp/file", "sha256": "anything"}]},
        {"required_actions": ["Action"] * 65},
    ],
)
def test_bounded_delivery_arguments(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FileAuditArguments.model_validate(arguments)
