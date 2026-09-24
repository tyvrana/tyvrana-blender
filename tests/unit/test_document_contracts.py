"""Packaged lazy schemas must describe the canonical wire envelopes exactly."""

import json
from pathlib import Path

import pytest
import tyvrana_protocol as protocol
from pydantic import BaseModel

from tyvrana_blender import attestation_model


@pytest.mark.parametrize(
    "name,model",
    [
        ("attestation", protocol.DocumentAttestationResponse),
        ("attestation-job", protocol.DocumentAttestationJob),
        ("mutation-request", protocol.DocumentMutationRequest),
        ("mutation-job", protocol.DocumentMutationJob),
        ("restore-request", protocol.DocumentRestoreRequest),
        ("restore-job", protocol.DocumentRestoreJob),
    ],
)
def test_canonical_document_schema(name: str, model: type[BaseModel]) -> None:
    path = Path(attestation_model.__file__).with_name(name + ".schema.json")
    assert json.loads(path.read_text()) == model.model_json_schema()
