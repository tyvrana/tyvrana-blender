"""Packaged lazy schemas must describe the canonical wire envelopes exactly."""

import json
from pathlib import Path
from typing import Any

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
        ("restore-request", protocol.DocumentRestoreRequest),
        ("restore-job", protocol.DocumentRestoreJob),
    ],
)
def test_canonical_document_schema(name: str, model: type[BaseModel]) -> None:
    path = Path(attestation_model.__file__).with_name(name + ".schema.json")
    assert json.loads(path.read_text()) == model.model_json_schema()


def test_native_execution_contract() -> None:
    from tyvrana_blender.mutation_models import MutationJobStatus

    schema = MutationJobStatus.model_json_schema()
    # Transport wheels stay fixed during reload; all existing envelope fields
    # must still match, while the separately distributed schema adds execution.
    transport = protocol.DocumentMutationJob.model_json_schema()
    without_execution = {**schema, "properties": dict(schema["properties"])}
    without_execution["properties"].pop("native_execution")
    assert without_execution == transport
    assert schema["properties"]["native_execution"]["enum"] == [
        "not_started",
        "started",
        "completed",
        "unknown",
    ]
    value = MutationJobStatus(
        job_id="edit", state="failed", native_execution="completed"
    )
    assert value.model_dump()["native_execution"] == "completed"


def test_proof_request_sources() -> None:
    from pydantic import ValidationError

    from tyvrana_blender.proof_models import ProofHostStart

    args = dict(
        lease=dict(lease_id="proof", token="a" * 64, parent_adapter_id="host"),
        expected_build="b" * 64,
        ttl_seconds=60,
    )
    snapshot = dict(
        host_session_id="host",
        document_session_id="doc",
        project_id="project",
        format="fixture",
        digest="c" * 64,
    )
    artifact = dict(locator="/fixture.blend", project_id="project", sha256="d" * 64)
    for source in [dict(snapshot=snapshot), dict(artifact=artifact)]:
        assert ProofHostStart.model_validate(dict(**args, **source))
    invalid_sources: list[dict[str, Any]] = [
        {},
        dict(snapshot=snapshot, artifact=artifact),
        dict(snapshot={**snapshot, "project_id": None}),
    ]
    for invalid_source in invalid_sources:
        with pytest.raises(ValidationError):
            ProofHostStart.model_validate(dict(**args, **invalid_source))
