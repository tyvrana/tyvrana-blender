"""Restore lazy file-image decoding after read-only inspection."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def decoded(image: Any) -> Iterator[None]:
    # Accessing size/channels also decodes a file image. Capture this before any
    # such property access. Never discard an existing buffer or unsaved pixels.
    release = (
        getattr(image, "source", None) == "FILE"
        and not image.has_data
        and not image.is_dirty
    )
    try:
        yield
    finally:
        if release and not image.is_dirty:
            image.buffers_free()
