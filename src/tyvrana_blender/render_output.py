"""Native render format configuration and bounded OpenEXR header inspection."""

import struct
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from .errors import OperationError
from .models import RenderArguments, RenderColorMetadata

FORMATS = {"png": "PNG", "exr": "OPEN_EXR", "exr_multilayer": "OPEN_EXR_MULTILAYER"}
PASS_NAMES = {
    "z": "Depth",
    "normal": "Normal",
    "position": "Position",
    "vector": "Vector",
    "uv": "UV",
    "mist": "Mist",
    "object_index": "IndexOB",
    "material_index": "IndexMA",
    "shadow": "Shadow",
    "ambient_occlusion": "AO",
    "emit": "Emit",
    "environment": "Env",
    "diffuse_direct": "DiffDir",
    "diffuse_indirect": "DiffInd",
    "diffuse_color": "DiffCol",
    "glossy_direct": "GlossDir",
    "glossy_indirect": "GlossInd",
    "glossy_color": "GlossCol",
    "transmission_direct": "TransDir",
    "transmission_indirect": "TransInd",
    "transmission_color": "TransCol",
}


def media_type(arguments: RenderArguments, *, sequence: bool = True) -> str:
    if sequence and arguments.frames:
        return "application/zip"
    return "image/png" if arguments.format == "png" else "image/x-exr"


def suffix(arguments: RenderArguments) -> str:
    return (
        ".zip" if arguments.frames else ".png" if arguments.format == "png" else ".exr"
    )


def color_metadata(scene: Any, args: RenderArguments) -> RenderColorMetadata:
    return RenderColorMetadata(
        display_device=scene.display_settings.display_device,
        view_transform=scene.view_settings.view_transform,
        look=scene.view_settings.look,
        exposure=scene.view_settings.exposure,
        gamma=scene.view_settings.gamma,
        output_encoding="display_referred" if args.format == "png" else "scene_linear",
    )


@contextmanager
def output_settings(scene: Any, layer: Any, args: RenderArguments) -> Iterator[None]:
    image = scene.render.image_settings
    attributes = ["media_type", "file_format", "color_mode", "color_depth", "exr_codec"]
    saved = [(image, n, getattr(image, n)) for n in attributes]
    saved += [
        (scene.view_settings, n, getattr(scene.view_settings, n))
        for n in ("view_transform", "look", "exposure", "gamma")
    ]
    passes = [
        p.identifier
        for p in layer.bl_rna.properties
        if p.identifier.startswith("use_pass_") and p.type == "BOOLEAN"
    ]
    saved += [(layer, n, getattr(layer, n)) for n in passes]
    aovs = [(a.name, a.type) for a in layer.aovs]
    try:
        image.media_type = (
            "MULTI_LAYER_IMAGE" if args.format == "exr_multilayer" else "IMAGE"
        )
        image.file_format = FORMATS[args.format]
        image.color_mode = args.color_mode
        image.color_depth = str(args.bit_depth)
        if args.format != "png":
            image.exr_codec = "ZIP"
        for prop in passes:
            setattr(layer, prop, prop == "use_pass_combined" or prop[9:] in args.passes)
        for item in list(layer.aovs):
            layer.aovs.remove(item)
        for spec in args.aovs:
            item = layer.aovs.add()
            item.name, item.type = spec.name, spec.type
            if not item.is_valid:
                raise OperationError(
                    "render_aov_invalid",
                    "AOV name conflicts with a native pass; choose another name",
                )
        if args.color:
            for prop in ("view_transform", "look", "exposure", "gamma"):
                value = getattr(args.color, prop)
                if value is not None:
                    try:
                        setattr(scene.view_settings, prop, value)
                    except (TypeError, ValueError) as exc:
                        raise OperationError(
                            "render_color_invalid", str(exc)[:500]
                        ) from exc
        yield
    finally:
        for item in list(layer.aovs):
            layer.aovs.remove(item)
        for name, kind in aovs:
            item = layer.aovs.add()
            item.name, item.type = name, kind
        # Format/view transform precede their dependent settings.
        for owner, prop, value in saved:
            setattr(owner, prop, value)


def cstring(stream: BinaryIO) -> bytes:
    value = bytearray()
    for _ in range(256):
        byte = stream.read(1)
        if byte == b"\0":
            return bytes(value)
        if not byte:
            break
        value.extend(byte)
    raise ValueError("Invalid bounded EXR header string")


def exr_header(path: Path) -> tuple[int, int, dict[str, int]]:
    """Inspect bounded flat EXR part headers; never decode or allocate pixel buffers."""
    from io import BytesIO

    channels: dict[str, int] = {}
    dimensions = None
    with path.open("rb") as stream:
        if stream.read(4) != b"\x76\x2f\x31\x01":
            raise ValueError("Invalid EXR signature")
        flags = struct.unpack("<I", stream.read(4))[0]
        if flags & 0x800 or flags & 0xFF != 2:
            raise ValueError("Expected a flat EXR with file format version 2")
        multipart = bool(flags & 0x1000)
        for _ in range(65):
            attributes: dict[bytes, tuple[bytes, bytes]] = {}
            while stream.tell() < 1048576:
                name = cstring(stream)
                if not name:
                    break
                kind = cstring(stream)
                length = struct.unpack("<I", stream.read(4))[0]
                if stream.tell() + length > 1048576:
                    raise ValueError("EXR headers exceed 1 MiB")
                payload = stream.read(length)
                if len(payload) != length or name in attributes:
                    raise ValueError("Truncated or duplicate EXR attribute")
                attributes[name] = kind, payload
            else:
                raise ValueError("EXR headers exceed 1 MiB")
            if not attributes:
                if multipart:
                    break
                raise ValueError("Empty EXR header")
            if attributes.get(b"type", (b"", b"scanlineimage"))[1] not in (
                b"scanlineimage",
                b"tiledimage",
            ):
                raise ValueError("Expected flat EXR image parts")
            window = attributes.get(b"dataWindow")
            channel_list = attributes.get(b"channels")
            if window is None or window[0] != b"box2i" or channel_list is None:
                raise ValueError("EXR part lacks dimensions/channels")
            x0, y0, x1, y1 = struct.unpack("<4i", window[1])
            current = (x1 - x0 + 1, y1 - y0 + 1)
            if min(current) < 1 or dimensions not in (None, current):
                raise ValueError("EXR part dimensions are invalid or inconsistent")
            dimensions = current
            if channel_list[0] != b"chlist":
                raise ValueError("Invalid EXR channel list")
            data = BytesIO(channel_list[1])
            while channel := cstring(data):
                info = data.read(16)
                key = channel.decode("utf-8")
                if len(info) != 16 or len(channels) >= 256 or key in channels:
                    raise ValueError("Invalid, duplicate or more than 256 EXR channels")
                pixel_type = struct.unpack("<I", info[:4])[0]
                if pixel_type not in (0, 1, 2):
                    raise ValueError("Invalid EXR channel pixel type")
                channels[key] = pixel_type
            if not multipart:
                break
        else:
            raise ValueError("EXR has more than 64 parts")
    if dimensions is None or not channels:
        raise ValueError("EXR lacks dimensions/channels")
    return *dimensions, channels
