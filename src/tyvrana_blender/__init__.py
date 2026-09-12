"""Blender extension entry points; importing the package starts no services."""


def register() -> None:
    import bpy  # type: ignore[import-not-found]

    from .compatibility import require_blender

    require_blender(bpy.app.version)
    from .blender import register as enable

    enable()


def unregister() -> None:
    from .blender import unregister as disable

    disable()
