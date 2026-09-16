"""Small adapter error boundary without operation-catalog initialization."""

from tyvrana_protocol import JsonValue, ProtocolError


class OperationError(Exception):
    def __init__(self, code: str, message: str, details: JsonValue = None) -> None:
        super().__init__(message)
        self.error = ProtocolError(code=code, message=message, details=details)
