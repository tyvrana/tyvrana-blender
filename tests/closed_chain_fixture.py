"""Typed generic linkage fixtures shared by native and real-MCP qualification."""

from typing import Any


def channel(bone: str) -> dict[str, Any]:
    return dict(
        kind="transform", object_name="Rig", bone=bone, property="rotation", axis="z"
    )


def point(bone: str, endpoint: str) -> dict[str, Any]:
    return dict(kind="bone", object="Rig", bone=bone, endpoint=endpoint)


def definition(*, driven_slider: bool = False) -> dict[str, Any]:
    return dict(
        name="Linkage",
        variables=[
            dict(
                name="Crank",
                channel=channel("Crank"),
                role="solve" if driven_slider else "input",
                minimum=0.05 if driven_slider else -1.3,
                maximum=1.3,
            ),
            dict(name="Rod", channel=channel("Rod"), minimum=-3.14145, maximum=3.14145),
            dict(
                name="Slider",
                channel=dict(
                    kind="transform",
                    object_name="Slider",
                    property="location",
                    axis="x",
                ),
                role="input" if driven_slider else "solve",
                minimum=0.01,
                maximum=3.2,
            ),
        ],
        closures=[
            dict(
                name="EndClosure",
                a=point("Rod", "tail"),
                b=dict(kind="object", object="Slider", point=[0, 0, 0]),
            )
        ],
        tolerance=0.00002,
    )


def envelope() -> dict[str, Any]:
    return dict(
        name="RailSeat",
        source=dict(
            object_name="Probe",
            selector=dict(
                mode="box",
                domain="vertex",
                min=[-1.01, -1.01, -1.01],
                max=[1.01, -0.99, 1.01],
            ),
        ),
        target=dict(object_name="Rail"),
        maximum_gap=0.003,
    )


def commands(
    *, unrelated: int = 0, driven_slider: bool = False, rotated: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for start in range(0, unrelated, 64):
        result.append(
            (
                "object_set.create",
                dict(
                    objects=[
                        dict(
                            kind="empty",
                            key=f"Unrelated{i:03}",
                            name=f"Unrelated{i:03}",
                        )
                        for i in range(start, min(start + 64, unrelated))
                    ]
                ),
            )
        )
    result.extend(
        [
            (
                "armature.create",
                dict(
                    name="Rig",
                    bones=[
                        dict(
                            name="Crank",
                            head=[0, 0, 0],
                            tail=[1, 0, 0],
                            limits=dict(z=dict(minimum=-3.14145, maximum=3.14145)),
                        ),
                        dict(
                            name="Rod",
                            head=[1, 0, 0],
                            tail=[3, 0, 0],
                            parent="Crank",
                            connected=True,
                            limits=dict(z=dict(minimum=-3.14145, maximum=3.14145)),
                        ),
                    ],
                ),
            ),
            (
                "object_set.create",
                dict(
                    objects=[
                        dict(
                            kind="empty",
                            key="Slider",
                            name="Slider",
                            location=[2.5, 0, 0],
                        ),
                        dict(
                            kind="primitive",
                            key="Probe",
                            name="Probe",
                            primitive="cube",
                            parent=dict(key="Slider"),
                            scale=[0.05, 0.05, 0.05],
                        ),
                        dict(
                            kind="primitive",
                            key="Rail",
                            name="Rail",
                            primitive="cube",
                            location=[0, -0.151, 0],
                            scale=[5, 0.1, 0.5],
                        ),
                    ]
                ),
            ),
            (
                "constraint.configure",
                dict(
                    constraints=[
                        dict(
                            name="SlideLimits",
                            owner=dict(object_name="Slider"),
                            settings=dict(
                                kind="limit_location",
                                x=dict(minimum=0.01, maximum=3.2),
                                y=dict(minimum=0, maximum=0),
                                z=dict(minimum=0, maximum=0),
                            ),
                        )
                    ]
                ),
            ),
        ]
    )
    result.extend(
        [
            (
                "coupling.configure",
                dict(mechanisms=[definition(driven_slider=driven_slider)]),
            ),
            (
                "action.edit",
                dict(
                    name="Drive",
                    create=True,
                    channels=[
                        dict(
                            target=dict(
                                kind="transform",
                                object_name="Slider",
                                property="location",
                                axis="x",
                            )
                            if driven_slider
                            else channel("Crank"),
                            keys=[
                                dict(frame=1, value=2.3 if driven_slider else -0.9),
                                dict(frame=33, value=2.8 if driven_slider else 0.9),
                            ],
                        )
                    ],
                ),
            ),
            ("action.assign", dict(name="Drive")),
        ]
    )
    return result


def sample_args(samples: int = 33, *, contact: bool = True) -> dict[str, Any]:
    return dict(
        range=dict(start=1, end=33, step=1 if samples == 33 else 2),
        mechanism="Linkage",
        armature_object="Rig",
        bones=["Crank", "Rod"],
        measurements=[
            dict(
                kind="distance",
                name="RodLength",
                a=point("Rod", "head"),
                b=point("Rod", "tail"),
                comparison=dict(target=2, tolerance=0.00002),
            )
        ],
        contacts=[envelope()] if contact else [],
        contact_max_tests=2000000,
    )
