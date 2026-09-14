"""Blender extension entry points; importing the package starts no services."""

import sys
from types import ModuleType


def _clear_cached_modules() -> None:
    """Reload local code after Blender unregisters the extension for script reload."""
    prefix = __name__ + "."
    backend = sys.modules.get(prefix + "blender")
    if backend is not None:
        backend.unregister()
    for name in tuple(sys.modules):
        if name.startswith(prefix):
            del sys.modules[name]
    package = sys.modules[__name__]
    for name, value in tuple(vars(package).items()):
        if isinstance(value, ModuleType) and value.__name__.startswith(prefix):
            delattr(package, name)


# Blender reloads the package entry point, but not its imported submodules.
# Clear only our code; shared dependency wheels remain owned by Blender.
_clear_cached_modules()


def register() -> None:
    import bpy  # type: ignore[import-not-found]

    from .compatibility import require_blender

    require_blender(bpy.app.version)
    from .blender import register as enable

    enable()


def unregister() -> None:
    from .blender import unregister as disable

    disable()
