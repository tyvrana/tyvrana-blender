"""Blender extension entry points; importing the package starts no services."""


def register() -> None:
    from .blender import register as enable

    enable()


def unregister() -> None:
    from .blender import unregister as disable

    disable()
