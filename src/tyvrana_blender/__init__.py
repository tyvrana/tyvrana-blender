"""Stable Blender extension entry points; importing starts no services."""


def register() -> None:
    import bpy  # type: ignore[import-not-found]

    from .compatibility import require_blender

    require_blender(bpy.app.version)
    from .lifecycle import enable

    enable()


def unregister() -> None:
    from .lifecycle import disable

    disable()
