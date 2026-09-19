"""Independent structural fixtures; clients describe controls, never topology."""

from typing import Any

from .surface_fixtures import fixtures, network, opening


def varying_member(name: str = "VaryingMember") -> dict[str, Any]:
    return {
        "name": name,
        "sections": [
            {"id": "base", "center": [0, 0, 0], "radii": [0.34, 0.31, 0.24, 0.23]},
            {
                "id": "flare",
                "center": [0.04, 0.35, 0.02],
                "radii": [0.42, 0.32, 0.28, 0.22],
                "twist": 0.1,
            },
            {
                "id": "lower",
                "center": [0.12, 0.8, 0.06],
                "radii": [0.2, 0.18, 0.17, 0.13],
                "twist": 0.22,
            },
            {
                "id": "middle",
                "center": [0.22, 1.45, 0.16],
                "radii": [0.17, 0.16, 0.12, 0.13],
                "twist": 0.38,
            },
            {
                "id": "upper",
                "center": [0.24, 2.15, 0.25],
                "radii": [0.22, 0.17, 0.11, 0.14],
                "twist": 0.52,
            },
            {
                "id": "shoulder",
                "center": [0.2, 2.7, 0.3],
                "radii": [0.32, 0.25, 0.2, 0.21],
                "twist": 0.65,
            },
            {
                "id": "tip",
                "center": [0.16, 3, 0.33],
                "radii": [0.36, 0.3, 0.24, 0.24],
                "twist": 0.72,
            },
        ],
        "sides": 32,
        "subdivisions": 6,
        "features": [
            {
                "id": "process",
                "position": 0.55,
                "angle": 0,
                "axial_width": 0.19,
                "angular_width": 0.7,
                "height": 0.35,
            },
            {
                "id": "ridge",
                "position": 0.5,
                "angle": 1.4,
                "axial_width": 0.42,
                "angular_width": 0.4,
                "height": 0.045,
            },
            {
                "id": "groove",
                "position": 0.38,
                "angle": -1.5,
                "axial_width": 0.25,
                "angular_width": 0.6,
                "height": -0.025,
            },
        ],
        "ends": {"start_depth": 0.12, "end_depth": -0.1, "rings": 4},
    }


def compact_member(name: str = "CompactMember") -> dict[str, Any]:
    return {
        "name": name,
        "sections": [
            {"id": "lower", "center": [0, 0, 0], "radii": [0.16, 0.14, 0.16, 0.12]},
            {
                "id": "broad",
                "center": [0.02, 0.12, 0.01],
                "radii": [0.24, 0.18, 0.2, 0.15],
                "twist": 0.12,
            },
            {
                "id": "waist",
                "center": [0.03, 0.25, 0.02],
                "radii": [0.2, 0.15, 0.18, 0.16],
                "twist": 0.25,
            },
            {
                "id": "upper",
                "center": [0.01, 0.42, 0.01],
                "radii": [0.17, 0.18, 0.17, 0.14],
                "twist": 0.35,
            },
        ],
        "sides": 24,
        "subdivisions": 4,
        "features": [
            {
                "id": "lug",
                "position": 0.5,
                "angle": 0,
                "axial_width": 0.4,
                "angular_width": 1,
                "height": 0.08,
            }
        ],
        "ends": {"start_depth": 0.035, "end_depth": -0.035, "rings": 3},
    }


def curved_strut() -> dict[str, Any]:
    return {
        "name": "CurvedStrut",
        "sections": [
            {"id": "left", "center": [-2.5, 0, 0], "radii": [0.08, 0.08, 0.06, 0.06]},
            {"id": "rise", "center": [-2, 0, 0.6], "radii": [0.11, 0.09, 0.06, 0.05]},
            {"id": "arch", "center": [-1, 0, 1.1], "radii": [0.12, 0.1, 0.05, 0.05]},
            {"id": "crown", "center": [0, 0, 1.25], "radii": [0.1, 0.1, 0.05, 0.05]},
            {"id": "fall", "center": [1.2, 0, 1], "radii": [0.1, 0.08, 0.05, 0.05]},
            {"id": "right", "center": [2.5, 0, 0], "radii": [0.06, 0.06, 0.04, 0.04]},
        ],
        "x_reference": [0, 1, 0],
        "sides": 24,
        "subdivisions": 6,
        "features": [
            {
                "id": "projection",
                "position": 0.35,
                "angle": 0,
                "axial_width": 0.16,
                "angular_width": 0.8,
                "height": 0.16,
            }
        ],
    }


def master_spec() -> dict[str, Any]:
    plate = network(
        "PerforatedPlate",
        {
            "a": [-0.25, -0.3, 0],
            "b": [0.3, -0.25, 0.03],
            "c": [0.2, 0.3, 0.02],
            "d": [-0.3, 0.25, 0],
        },
        {"plate": ["a", "b", "c", "d"]},
    )
    plate["thickness"] = 0.025
    plate["openings"] = [opening("port", "plate", [0.5, 0.5], [0.16, 0.18])]
    shell = fixtures()[1]
    block = compact_member("FacetedFitting")
    block.update(sides=4, subdivisions=3, smooth=False)
    for section in block["sections"]:
        section["twist"] = 0.785398
    return {
        "name": "StructuralModule",
        "templates": [
            {"id": "housing", "kind": "surface", "spec": shell},
            {"id": "member", "kind": "loft", "spec": varying_member()},
            {"id": "segment", "kind": "loft", "spec": compact_member()},
            {"id": "strut", "kind": "loft", "spec": curved_strut()},
            {"id": "plate", "kind": "surface", "spec": plate},
            {"id": "block", "kind": "loft", "spec": block},
        ],
        "families": [
            {"id": "housing", "template": "housing", "path": [[0, 0, 0.8]]},
            {
                "id": "membersL",
                "template": "member",
                "count": 2,
                "path": [[-2.5, -2.6, 0.25], [-2.5, 0, 0.25]],
            },
            {"id": "membersR", "mirror_of": "membersL", "count": 2},
            {
                "id": "segments",
                "template": "segment",
                "count": 16,
                "path": [[0, -2.5, 1.7], [0, 0.1, 1.78], [0, 2.3, 1.65]],
                "align_path": True,
                "scale_start": [0.8, 0.8, 0.8],
                "scale_end": [1.2, 1.15, 1.1],
                "rotation_end": [0.1, 0.2, 0.15],
                "feature_scale": [0.5, 1.4],
                "overrides": [
                    {
                        "index": 0,
                        "shape": {"features": [{"id": "lug", "height": 0.13}]},
                    },
                    {"index": 7, "scale": [1.3, 0.8, 0.7]},
                    {
                        "index": 15,
                        "shape": {
                            "ends": {
                                "start_depth": 0.04,
                                "end_depth": -0.07,
                                "rings": 3,
                            }
                        },
                    },
                ],
            },
            {
                "id": "cage",
                "template": "strut",
                "count": 8,
                "path": [[0, -2.2, 0.35], [0, 2.2, 0.35]],
                "scale_end": [1, 0.85, 0.94],
                "feature_scale": [0.6, 1.2],
            },
            {
                "id": "fittings",
                "template": "segment",
                "count": 4,
                "path": [[-2.5, -2.3, 0.42], [-2.5, 2.1, 0.42]],
                "scale_start": [0.8, 0.7, 1.3],
                "scale_end": [1.2, 0.9, 0.8],
                "rotation_end": [0.1, 0, 0.3],
                "feature_scale": [0.7, 1.4],
            },
            {
                "id": "blocks",
                "template": "block",
                "count": 4,
                "path": [[2.5, -2.3, 0.42], [2.5, 2.1, 0.42]],
                "scale_start": [1, 0.7, 0.7],
                "scale_end": [1.3, 1, 0.7],
            },
            {
                "id": "plates",
                "template": "plate",
                "count": 4,
                "path": [[2.35, -1.8, 0.6], [2.35, 1.8, 0.6]],
                "rotation_end": [0, 0.2, 0.1],
                "scale_end": [1.2, 1, 1],
            },
        ],
    }
