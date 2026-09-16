"""Packaged headless native paths, attachments and persistence."""

import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT


def test_native_curves(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/curve_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "curve-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "CURVE_NATIVE_PASSED" in log


async def test_paths_persist_and_reevaluate_across_hosts(
    profile: dict[str, str],
    tmp_path: Path,
) -> None:
    import json
    import math
    from typing import Any

    import pytest

    from .conftest import running_blender
    from .test_e2e import core_client, discover, operation

    def path(name: str, coords: Any, **kwargs: Any) -> Any:
        return dict(
            name=name, splines=[dict(points=[dict(co=p) for p in coords])], **kwargs
        )

    names = ["Hose", "Span", "SurfaceGuide", "Section", "Profiled"]
    saved: dict[str, Any] = {}
    pids = []
    filepath = str(tmp_path / "paths.blend")
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        for host_index in range(2):
            directory = tmp_path / f"host-{host_index}"
            directory.mkdir()
            env = dict(
                profile, TYVRANA_TEST_CONTROL=str(directory), TMPDIR=str(directory)
            )
            async with running_blender(env, directory, ui=False) as process:
                pids.append(process.pid)
                adapter = await discover(client)
                assert adapter is not None and adapter.operation_count == 146
                print("CURVE_MCP_BUILD", adapter.model_dump_json())

                async def call(
                    name: str,
                    /,
                    identifier: str = adapter.instance_id,
                    **arguments: Any,
                ) -> Any:
                    return await operation(
                        client, identifier, "blender." + name, arguments
                    )

                lifecycle = await call("extension.inspect")
                assert lifecycle["implementation_build"] == lifecycle["build"]
                print("CURVE_IMPLEMENTATION", json.dumps(lifecycle))

                async def inspect(name: str) -> Any:
                    row = (await call("curve.inspect", names=[name], samples=5))[
                        "curves"
                    ][0]
                    assert row["valid"], row
                    assert all(b["error"] < 1e-4 for b in row["bindings"])
                    return row

                if host_index == 0:
                    schema = await client.call_tool(
                        "tyvrana_list_operations",
                        dict(
                            adapter_id=adapter.instance_id,
                            names=[
                                "blender.curve." + n
                                for n in ["create", "configure", "inspect", "remove"]
                            ],
                            include_schemas=True,
                        ),
                    )
                    assert not schema.is_error
                    await call(
                        "object_set.create",
                        objects=[
                            dict(kind="empty", key="root", name="Assembly"),
                            dict(
                                kind="empty",
                                key="start",
                                name="Start",
                                parent=dict(key="root"),
                            ),
                            dict(
                                kind="empty",
                                key="end",
                                name="End",
                                location=[3, 2, 0],
                                parent=dict(key="root"),
                            ),
                        ],
                    )
                    await call(
                        "material.create_principled",
                        name="Rubber",
                        base_color=[0.05, 0.07, 0.1],
                        roughness=0.7,
                    )
                    await call(
                        "curve.create",
                        curves=[
                            path(
                                "Hose",
                                [[0, 0, 0], [1, 0, 1], [2, 2, 1], [3, 2, 0]],
                                settings=dict(
                                    profile=dict(
                                        kind="circle", radius=0.12, resolution=12
                                    ),
                                    material="Rubber",
                                ),
                                bindings=[
                                    dict(
                                        spline=0,
                                        point=0,
                                        target=dict(kind="object", object="Start"),
                                    ),
                                    dict(
                                        spline=0,
                                        point=3,
                                        target=dict(kind="object", object="End"),
                                    ),
                                ],
                            )
                        ],
                    )
                    before = await inspect("Hose")
                    await call(
                        "object.set_transform",
                        name="Assembly",
                        location=[1, 2, 3],
                        rotation=[0.2, 0, 0.3],
                    )
                    await call(
                        "object_set.configure",
                        objects=[dict(name="Start", rename="Socket")],
                    )
                    moved = await inspect("Hose")
                    assert moved["splines"][0]["start"] != before["splines"][0]["start"]
                    assert moved["bindings"][0]["target"]["object"] == "Socket"
                    await call(
                        "curve.configure",
                        curves=[
                            dict(
                                name="Hose",
                                ranges=[
                                    dict(
                                        spline=0,
                                        start=1,
                                        points=[
                                            dict(co=[1, 0, 1.5], radius=0.7, tilt=0.3)
                                        ],
                                    )
                                ],
                            )
                        ],
                    )
                    await call(
                        "armature.create",
                        name="Structure",
                        bones=[
                            dict(name="A", head=[0, 0, 0], tail=[0, 1, 0]),
                            dict(
                                name="B",
                                head=[0, 1, 0],
                                tail=[0, 2, 0],
                                parent="A",
                                connected=True,
                            ),
                        ],
                    )
                    await call(
                        "mesh.create",
                        name="Surface",
                        vertices=[[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0]],
                        faces=[[0, 1, 2]],
                    )
                    await call(
                        "armature.bind",
                        object_name="Surface",
                        armature_object="Structure",
                        weights=dict(
                            method="explicit",
                            vertices=[
                                dict(vertex=i, influences=[dict(bone="A", weight=1.0)])
                                for i in range(3)
                            ],
                        ),
                    )
                    await call(
                        "curve.create",
                        curves=[
                            path(
                                "Span",
                                [[0, 0, 0], [1, 1, 0], [0, 2, 0]],
                                bindings=[
                                    dict(
                                        spline=0,
                                        point=0,
                                        target=dict(
                                            kind="bone", object="Structure", bone="A"
                                        ),
                                    ),
                                    dict(
                                        spline=0,
                                        point=2,
                                        target=dict(
                                            kind="bone", object="Structure", bone="B"
                                        ),
                                        offset=[0, 1, 0],
                                    ),
                                ],
                            ),
                            path(
                                "SurfaceGuide",
                                [[1, 1, 0], [1, 1, 0.5], [1.2, 1, 1]],
                                bindings=[
                                    dict(
                                        spline=0,
                                        follow="spline",
                                        target=dict(
                                            kind="surface",
                                            object="Surface",
                                            face=0,
                                            barycentric=[0, 0.5, 0.5],
                                        ),
                                    )
                                ],
                            ),
                        ],
                    )
                    await call(
                        "armature.pose",
                        object_name="Structure",
                        bones=[
                            dict(name="A", rotation=[0.5, 0, 0]),
                            dict(name="B", rotation=[0, 0, 0.3]),
                        ],
                    )
                    surface = await inspect("SurfaceGuide")
                    assert surface["bindings"][0]["evaluated_world"] == pytest.approx(
                        [1, math.cos(0.5), math.sin(0.5)], abs=1e-5
                    )
                    end = surface["splines"][0]["end"]
                    assert end == pytest.approx(
                        [
                            1.2,
                            math.cos(0.5) - math.sin(0.5),
                            math.sin(0.5) + math.cos(0.5),
                        ],
                        abs=1e-5,
                    )
                    await call(
                        "curve.create",
                        curves=[
                            dict(
                                name="Section",
                                splines=[
                                    dict(
                                        type="POLY",
                                        cyclic=True,
                                        points=[
                                            dict(co=p)
                                            for p in [
                                                [-0.1, -0.2, 0],
                                                [0.1, -0.2, 0],
                                                [0.1, 0.2, 0],
                                                [-0.1, 0.2, 0],
                                            ]
                                        ],
                                    )
                                ],
                            )
                        ],
                    )
                    await call(
                        "curve.create",
                        curves=[path("Profiled", [[0, 0, 0], [0, 1, 1], [0, 3, 1]])],
                        defaults=dict(profile=dict(kind="object", object="Section")),
                    )
                    guides = [
                        path(
                            f"Guide{i:02}",
                            [[i * 0.1, 0, 0], [i * 0.1, 1, 0.2], [i * 0.1, 2, 0]],
                        )
                        for i in range(50)
                    ]
                    batch = await call(
                        "curve.create",
                        curves=guides,
                        defaults=dict(
                            resolution=6,
                            profile=dict(kind="circle", radius=0.01, resolution=4),
                        ),
                        sample_limit=0,
                    )
                    assert batch["curve_count"] == 50 and batch["curves"] == []
                    default = await call("curve.inspect", prefix="Guide")
                    filtered = await call("curve.inspect", names=["Guide25"])
                    detail = await call(
                        "curve.inspect", names=["Guide25"], samples=16, point_limit=64
                    )
                    assert (
                        len(default["curves"]) == 8
                        and default["page"]["matched_count"] == 50
                    )
                    assert all(
                        not s["points"] for c in default["curves"] for s in c["splines"]
                    )
                    print(
                        "CURVE_RESULT_BYTES",
                        json.dumps(
                            {
                                k: len(json.dumps(v, separators=(",", ":")).encode())
                                for k, v in dict(
                                    default=default, filtered=filtered, detail=detail
                                ).items()
                            }
                        ),
                    )
                    failure = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id=adapter.instance_id,
                            operation="blender.curve.configure",
                            arguments=dict(
                                curves=[
                                    dict(
                                        name="Hose",
                                        bindings=[
                                            dict(
                                                spline=0,
                                                target=dict(
                                                    kind="object", object="Missing"
                                                ),
                                            )
                                        ],
                                    )
                                ]
                            ),
                        ),
                    )
                    assert failure.is_error
                    for name in names + ["Guide25"]:
                        saved[name] = await inspect(name)
                    await call("file.save", filepath=filepath)
                else:
                    await call("file.open", filepath=filepath, discard_current=True)
                    for name, expected in saved.items():
                        assert await inspect(name) == expected
                    guides = await call("curve.inspect", prefix="Guide", limit=1)
                    assert guides["page"]["matched_count"] == 50
                    await call(
                        "armature.pose",
                        object_name="Structure",
                        reset=True,
                        sample_limit=0,
                    )
                    neutral = await inspect("SurfaceGuide")
                    assert neutral["bindings"][0]["evaluated_world"] == pytest.approx(
                        [1, 1, 0], abs=1e-5
                    )
                    await call(
                        "curve.configure",
                        curves=[
                            dict(
                                name="Section",
                                ranges=[
                                    dict(
                                        spline=0,
                                        start=0,
                                        points=[dict(co=[-0.2, -0.3, 0])],
                                    )
                                ],
                            )
                        ],
                    )
                    assert (await inspect("Profiled"))["minimum"] != saved["Profiled"][
                        "minimum"
                    ]
                    await call(
                        "curve.remove",
                        names=["Hose", "Span", "SurfaceGuide", "Profiled"],
                    )
                    assert (await inspect("Section"))["point_count"] == 4
        assert pids[0] != pids[1]
    print("CURVE_PERSISTED_HOSTS", pids)


async def test_batched_guide_interaction(
    profile: dict[str, str], tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    import json
    import time
    from typing import Any

    from .conftest import running_blender
    from .test_e2e import core_client, discover, operation

    metrics: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            original = client.call_tool
            phase = "single"

            async def observed(name: str, arguments: Any = None, **kwargs: Any) -> Any:
                started = time.perf_counter()
                result = await original(name, arguments, **kwargs)
                metrics.append(
                    dict(
                        phase=phase,
                        request_bytes=len(
                            json.dumps(arguments, separators=(",", ":")).encode()
                        ),
                        response_bytes=len(result.model_dump_json().encode()),
                        seconds=time.perf_counter() - started,
                        error=result.is_error,
                    )
                )
                return result

            monkeypatch.setattr(client, "call_tool", observed)
            defaults: Any = dict(
                spline_type="BEZIER",
                resolution=6,
                profile=dict(kind="circle", radius=0.01, resolution=4),
            )
            curves: Any = [
                dict(
                    name=f"G{i:02}",
                    splines=[
                        dict(
                            points=[
                                dict(co=[i * 0.1, j, 0.1 * (j % 2)], radius=0.8)
                                for j in range(5)
                            ]
                        )
                    ],
                )
                for i in range(50)
            ]
            for spec in curves:
                await operation(
                    client,
                    adapter.instance_id,
                    "blender.curve.create",
                    dict(curves=[spec], defaults=defaults, sample_limit=0),
                )
            phase = "cleanup"
            await operation(
                client,
                adapter.instance_id,
                "blender.curve.remove",
                dict(names=[c["name"] for c in curves]),
            )
            phase = "batch"
            await operation(
                client,
                adapter.instance_id,
                "blender.curve.create",
                dict(curves=curves, defaults=defaults, sample_limit=0),
            )
            phase = "verify"
            value: Any = await operation(
                client,
                adapter.instance_id,
                "blender.curve.inspect",
                dict(prefix="G", limit=1),
            )
            assert value["page"]["matched_count"] == 50
    (tmp_path / "guide-metrics.json").write_text(json.dumps(metrics, indent=2))
    print("CURVE_BATCH_METRICS", json.dumps(metrics))
