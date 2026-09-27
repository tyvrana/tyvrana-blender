"""Scene time as native data, without playback UI control."""

import bpy  # type: ignore[import-not-found]

from . import motion_channels as channels
from . import rig
from .motion_models import TimelineArguments, TimelineInspectArguments, TimelineState


def inspect(args: TimelineInspectArguments) -> TimelineState:
    rig.idle()
    s = bpy.context.scene
    return TimelineState(
        frame_start=s.frame_start,
        frame_end=s.frame_end,
        frame=s.frame_current,
        subframe=s.frame_subframe,
        fps=s.render.fps,
        fps_base=s.render.fps_base,
        effective_fps=s.render.fps / s.render.fps_base,
        use_preview_range=s.use_preview_range,
        preview_start=s.frame_preview_start,
        preview_end=s.frame_preview_end,
    )


def configure(args: TimelineArguments) -> TimelineState:
    previous = inspect(TimelineInspectArguments())
    values = previous.model_dump() | args.model_dump(exclude_unset=True)
    if (
        values["frame_start"] > values["frame_end"]
        or values["preview_start"] > values["preview_end"]
    ):
        channels.fail("Timeline/preview start must not exceed end")
    s = bpy.context.scene
    channels.editable(s)
    for public, native in [
        ("frame_start", "frame_start"),
        ("frame_end", "frame_end"),
        ("preview_start", "frame_preview_start"),
        ("preview_end", "frame_preview_end"),
    ]:
        prop = s.bl_rna.properties[native]
        if not prop.hard_min <= values[public] <= prop.hard_max:
            channels.fail(
                f"{public} must lie in native range {prop.hard_min}..{prop.hard_max}"
            )

    def apply(data: dict[str, object]) -> None:
        def changed(owner: object, name: str, value: object) -> None:
            # Even equal RNA assignments emit native update notifications. In
            # particular, preview-range setters queue another frame evaluation
            # in the interactive event loop, after this operation has returned.
            if getattr(owner, name) != value:
                setattr(owner, name, value)

        # Set in an order that does not trigger Blender's range clamping.
        changed(s, "frame_end", max(s.frame_end, data["frame_end"]))
        changed(s, "frame_start", data["frame_start"])
        changed(s, "frame_end", data["frame_end"])
        changed(s, "use_preview_range", data["use_preview_range"])
        changed(s, "frame_preview_end", max(s.frame_preview_end, data["preview_end"]))
        changed(s, "frame_preview_start", data["preview_start"])
        changed(s, "frame_preview_end", data["preview_end"])
        changed(s.render, "fps", data["fps"])
        changed(s.render, "fps_base", data["fps_base"])
        s.frame_set(data["frame"], subframe=data["subframe"])
        bpy.context.view_layer.update()

    try:
        apply(values)
    except BaseException:
        apply(previous.model_dump())
        raise
    return inspect(TimelineInspectArguments())
