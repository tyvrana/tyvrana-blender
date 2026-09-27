"""Canonical proof request distributed independently of transport wheels.

Like the attestation and mutation contracts, this observation schema can be
activated without replacing Blender-managed transport dependencies.
"""

from typing import Any

from pydantic import Field, model_validator
from tyvrana_protocol import DocumentState, ProofArtifact, ProofLease

from .models import Model
from .mutation_models import schema


class ProofHostStart(Model):
    lease: ProofLease
    artifact: ProofArtifact | None = None
    snapshot: DocumentState | None = None
    expected_build: str = Field(pattern="^[0-9a-f]{64}$")
    ttl_seconds: int = Field(ge=1, le=900)

    @model_validator(mode="after")
    def source(self) -> "ProofHostStart":
        if (self.artifact is None) == (self.snapshot is None):
            raise ValueError("Choose exactly one durable artifact or live snapshot")
        if self.snapshot is not None and self.snapshot.project_id is None:
            raise ValueError("A proof snapshot requires document identity")
        return self

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return schema("proof-start.schema.json")
