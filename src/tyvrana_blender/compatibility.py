"""Minimum host version for the single current adapter implementation."""

MINIMUM_BLENDER = (5, 2, 1)


def require_blender(version: tuple[int, int, int]) -> None:
    if version < MINIMUM_BLENDER:
        raise RuntimeError("Tyvrana requires Blender 5.2.1 or newer")
