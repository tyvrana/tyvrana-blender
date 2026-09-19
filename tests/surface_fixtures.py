"""Sparse mechanical fixtures with shared boundaries, openings and local features."""

from typing import Any


def network(
    name: str, coordinates: dict[str, list[float]], patches: dict[str, list[str]]
) -> dict[str, Any]:
    curves: dict[tuple[str, str], str] = {}
    result: dict[str, Any] = {
        "name": name,
        "nodes": [{"id": n, "point": p} for n, p in coordinates.items()],
        "curves": [],
        "patches": [],
        "openings": [],
        "features": [],
        "thickness": 0.035,
    }
    for name, corners in patches.items():
        boundaries = []
        for a, b in zip(corners, corners[1:] + corners[:1], strict=True):
            key = (min(a, b), max(a, b))
            if key not in curves:
                handle = a + "-" + b
                curves[key] = handle
                result["curves"].append(
                    {"id": handle, "start": a, "end": b, "samples": 16}
                )
            boundaries.append(curves[key])
        result["patches"].append(
            {"id": name, "boundaries": boundaries, "resolution": 18}
        )
    return result


def opening(
    name: str, patch: str, center: list[float], radii: list[float]
) -> dict[str, Any]:
    return {
        "kind": "ellipse",
        "id": name,
        "patch": patch,
        "center": center,
        "radii": radii,
    }


def bulge(
    name: str, patch: str, center: list[float], radii: list[float], height: float
) -> dict[str, Any]:
    return {
        "kind": "bulge",
        "id": name,
        "patch": patch,
        "center": center,
        "radii": radii,
        "height": height,
    }


def fixtures() -> list[dict[str, Any]]:
    shell = network(
        "PerforatedShell",
        {
            "a": [-2, -1.5, 0],
            "b": [2.4, -1.3, 0.15],
            "c": [1.8, 1.6, 0.2],
            "d": [-1.8, 1.3, 0.05],
        },
        {"plate": ["a", "b", "c", "d"]},
    )
    shell["curves"][0]["through"] = [[0, -1.7, 0.2]]
    shell["curves"][2]["through"] = [[0, 1.8, 0.5]]
    shell["patches"][0]["thickness"] = [0.025, 0.05, 0.065, 0.035]
    shell["openings"] = [
        opening("portA", "plate", [0.25, 0.45], [0.09, 0.13]),
        opening("portB", "plate", [0.7, 0.65], [0.13, 0.08]),
    ]
    shell["features"] = [
        bulge("dome", "plate", [0.42, 0.25], [0.28, 0.18], 0.22),
        bulge("recess", "plate", [0.48, 0.7], [0.18, 0.22], -0.18),
        {
            "kind": "ridge",
            "id": "crest",
            "patch": "plate",
            "path": [[0.15, 0.83], [0.45, 0.9], [0.8, 0.85]],
            "width": 0.06,
            "height": 0.12,
        },
    ]
    support = network(
        "BranchedSupport",
        {
            "a": [-1, -0.7, 0],
            "b": [1, -0.7, 0],
            "c": [1, 0.7, 0.1],
            "d": [-1, 0.7, 0.1],
            "e": [-0.7, -2.8, 0.8],
            "f": [0.7, -2.8, 0.8],
            "g": [2.8, -0.5, 0.5],
            "h": [2.6, 0.55, 0.7],
            "i": [-2.8, -0.5, -0.5],
            "j": [-2.5, 0.5, -0.4],
        },
        {
            "hub": ["a", "b", "c", "d"],
            "south": ["e", "f", "b", "a"],
            "east": ["b", "g", "h", "c"],
            "west": ["i", "a", "d", "j"],
        },
    )
    support["openings"] = [opening("branchPort", "south", [0.5, 0.38], [0.19, 0.14])]
    support["features"] = [
        bulge("hubRecess", "hub", [0.5, 0.5], [0.38, 0.38], -0.2),
        {
            "kind": "ridge",
            "id": "braceCrest",
            "patch": "east",
            "path": [[0.25, 0.5], [0.75, 0.5]],
            "width": 0.13,
            "height": 0.1,
        },
    ]
    socket = network(
        "SocketMount",
        {
            "a": [-2, -1.6, 0],
            "b": [2, -1.6, 0],
            "c": [2, 1.6, 0.2],
            "d": [-2, 1.6, 0.2],
            "e": [4, -0.7, 0.6],
            "f": [4, 0.7, 0.7],
        },
        {"body": ["a", "b", "c", "d"], "taper": ["b", "e", "f", "c"]},
    )
    socket["features"] = [
        bulge("socket", "body", [0.43, 0.5], [0.29, 0.34], -0.6),
        {
            "kind": "ring",
            "id": "rim",
            "patch": "body",
            "center": [0.43, 0.5],
            "radii": [0.32, 0.37],
            "width": 0.055,
            "height": 0.14,
        },
        {
            "kind": "ridge",
            "id": "rail",
            "patch": "taper",
            "path": [[0.15, 0.5], [0.85, 0.5]],
            "width": 0.12,
            "height": 0.17,
        },
    ]
    socket["openings"] = [opening("drain", "body", [0.43, 0.5], [0.065, 0.08])]
    housing = network(
        "SectionHousing",
        {
            "a": [-1.5, -2, 0],
            "b": [1.5, -2, 0],
            "c": [1.7, 0, 0.15],
            "d": [-1.4, 0, 0.1],
            "e": [1.3, 2, 0],
            "f": [-1.6, 2, 0.1],
            "g": [2.4, -2, -0.25],
            "h": [2.7, 0, -0.2],
        },
        {
            "front": ["a", "b", "c", "d"],
            "rear": ["d", "c", "e", "f"],
            "flange": ["b", "g", "h", "c"],
        },
    )
    for c in housing["curves"]:
        if c["id"] in ("a-b", "c-d", "e-f"):
            c["through"] = [
                [
                    0,
                    {"a-b": -2, "c-d": 0, "e-f": 2}[c["id"]],
                    {"a-b": 0.8, "c-d": 1.1, "e-f": 0.6}[c["id"]],
                ]
            ]
    housing["patches"][0]["thickness"] = [0.035, 0.035, 0.07, 0.07]
    housing["patches"][1]["thickness"] = [0.07, 0.07, 0.05, 0.05]
    housing["openings"] = [
        opening("access", "front", [0.48, 0.5], [0.26, 0.24]),
        {
            "kind": "polygon",
            "id": "vent",
            "patch": "rear",
            "points": [[0.3, 0.35], [0.65, 0.38], [0.6, 0.65], [0.35, 0.62]],
            "subdivisions": 4,
        },
    ]
    return [shell, support, socket, housing]
