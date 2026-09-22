"""Adapter evidence using the distributed canonical protocol JSON schema.

The operation's schema is distributed separately from transport dependencies, so
adding an observation contract does not require replacing live transport wheels.
Core validates the shared protocol model on receipt.
"""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, RootModel
from tyvrana_protocol import JsonValue, ProtocolError

from .models import Model


class DocumentAttestationResult(RootModel[dict[str, JsonValue]]):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(
            json.loads(Path(__file__).with_name("attestation.schema.json").read_text())
        )


class AttestationJobArguments(Model):
    job_id: str = Field(min_length=1, max_length=128)


class AttestationJobStatus(Model):
    job_id: str
    state: Literal["queued", "running", "completed", "failed", "cancelled"]
    revision: int = 0
    elapsed_seconds: float = 0
    poll_after_seconds: float = 0.5
    progress: dict[str, JsonValue] | None = None
    result: DocumentAttestationResult | None = None
    error: ProtocolError | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(
            json.loads(
                Path(__file__).with_name("attestation-job.schema.json").read_text()
            )
        )
