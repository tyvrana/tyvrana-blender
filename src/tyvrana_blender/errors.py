"""Small adapter error boundary without operation-catalog initialization."""

from pydantic import ValidationError
from tyvrana_protocol import JsonValue, ProtocolError


class OperationError(Exception):
    def __init__(self, code: str, message: str, details: JsonValue = None) -> None:
        super().__init__(message)
        self.error = ProtocolError(code=code, message=message, details=details)


def constraint_error(code: str, exc: ValidationError) -> OperationError:
    """Report bounded failures when valid edits compose invalid geometry."""
    details: list[JsonValue] = [
        {
            "field": ".".join(map(str, item["loc"]))[:500],
            "message": item["msg"][:500],
            "reason": item["type"],
        }
        for item in exc.errors(include_url=False, include_context=False)[:8]
    ]
    return OperationError(
        code, "Composed construction constraints are invalid", details
    )
