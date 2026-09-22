"""Adapter evidence using the distributed canonical protocol JSON schema.

The operation's schema is distributed separately from transport dependencies, so
adding an observation contract does not require replacing live transport wheels.
Core validates the shared protocol model on receipt.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import RootModel
from tyvrana_protocol import JsonValue


class DocumentAttestationResult(RootModel[dict[str, JsonValue]]):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(
            json.loads(Path(__file__).with_name("attestation.schema.json").read_text())
        )
