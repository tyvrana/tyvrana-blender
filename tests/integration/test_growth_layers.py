"""Ordered broad surfaces, moving body contact, native persistence and MCP QA."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation, wait_for_project


def test_native_layer_correction(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/growth_layers_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "layers-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "GROWTH_LAYERS_NATIVE_PASSED" in log


async def test_layer_correction_motion_and_persistence(
    profile: dict[str, str], tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(name: str, /, **args: Any) -> Any:
                started = time.perf_counter()
                result = await operation(
                    client, adapter.instance_id, "blender." + name, args
                )
                calls.append(
                    dict(
                        operation=name,
                        argument_bytes=len(json.dumps(args).encode()),
                        result_bytes=len(json.dumps(result).encode()),
                        seconds=time.perf_counter() - started,
                    )
                )
                return result

            await call(
                "mesh.create",
                name="Support",
                vertices=[[-2, -2, 0], [2, -2, 0], [2, 2, 0], [-2, 2, 0]],
                faces=[[0, 1, 2, 3]],
                corner_uvs=[[0, 0], [1, 0], [1, 1], [0, 1]],
            )
            await call(
                "shape_keys.edit",
                object_name="Support",
                keys=[
                    dict(
                        name="Fold",
                        create=True,
                        value=0,
                        correction=dict(
                            mode="region",
                            selector=dict(
                                mode="box",
                                domain="vertex",
                                min=[0, -3, -1],
                                max=[3, 3, 1],
                            ),
                            delta=[0, 0, 0.8],
                        ),
                    )
                ],
            )
            await call(
                "mesh.create",
                name="Strip",
                hidden=True,
                vertices=[
                    [-0.005, 0, 0],
                    [0.005, 0, 0],
                    [-0.24, 0, 0.5],
                    [0.24, 0, 0.5],
                    [-0.24, 0, 1],
                    [0.24, 0, 1],
                ],
                faces=[[0, 1, 3, 2], [2, 3, 5, 4]],
            )
            await call(
                "mesh.create",
                name="Body",
                vertices=[
                    [-1.5, 0.03, 0.9],
                    [1.5, 0.03, 0.9],
                    [1.5, 0.03, 1.5],
                    [-1.5, 0.03, 1.5],
                ],
                faces=[[0, 1, 2, 3]],
            )
            await call(
                "growth.create",
                name="Field",
                surface="Support",
                families=[
                    dict(
                        name="Strip",
                        length=0.8,
                        shape=[[0, 0, 0], [0, 0, 1]],
                        template=dict(object_name="Strip", mode="deform"),
                    )
                ],
                regions=[
                    dict(
                        name="Panel",
                        family="Strip",
                        guides=0,
                        rows=[
                            dict(
                                name="Lower",
                                path=[[0.25, 0.5], [0.65, 0.5]],
                                count=4,
                                layer=0,
                                order=0,
                                overlap="over_previous",
                            ),
                            dict(
                                name="Upper",
                                path=[[0.30, 0.5], [0.70, 0.5]],
                                count=4,
                                layer=1,
                                order=1,
                                overlap="over_previous",
                            ),
                        ],
                    )
                ],
            )
            await call(
                "action.edit",
                name="FoldMotion",
                create=True,
                channels=[
                    dict(
                        target=dict(kind="shape", object_name="Support", key="Fold"),
                        keys=[
                            dict(frame=1, value=0),
                            dict(frame=2, value=1),
                            dict(frame=3, value=0),
                        ],
                    )
                ],
            )
            await call("action.assign", name="FoldMotion")
            query = dict(
                instances=[dict(object_name="Field", obstacles=["Body"])],
                containment=False,
                worst_limit=2,
            )
            before = await call("geometry.inspect", **query, frames=[1, 2, 3])
            assert any(
                s["instances"][0]["contact_element_pairs"] for s in before["samples"]
            )
            settings = dict(
                object_name="Field",
                frame_start=1,
                frame_end=3,
                samples=33,
                direction=[0, 1, 0],
                maximum_lift=0.6,
                steps=48,
                colliders=["Body"],
                max_seconds=20,
            )
            corrected = await call("growth.layers.correct", **settings)
            assert corrected["valid"], corrected
            assert corrected["maximum_root_error"] == 0
            after = await call(
                "geometry.inspect",
                **query,
                adaptive=dict(
                    start=1,
                    end=3,
                    initial_samples=3,
                    max_samples=33,
                    minimum_step=0.03125,
                ),
            )
            (tmp_path / "layers-adaptive.json").write_text(json.dumps(after, indent=2))
            print(
                "LAYER_CONFLICT_TIMES",
                [
                    (s["frame"], s["instances"][0]["contacts"])
                    for s in after["samples"]
                    if s["instances"][0]["contact_element_pairs"]
                ],
            )
            assert all(
                s["instances"][0]["contact_element_pairs"] == 0
                for s in after["samples"]
            ), after
            assert after["minimum_distance"] > 0.001
            between = await call(
                "geometry.inspect", **query, frames=[1.07, 1.31, 1.73, 2.11, 2.47, 2.83]
            )
            assert all(
                s["instances"][0]["contact_element_pairs"] == 0
                for s in between["samples"]
            )

            scene = tmp_path / "layered.blend"
            await call("file.save", filepath=str(scene), overwrite=True)
            await wait_for_project(client, adapter.instance_id, str(scene))
            await call("file.open", filepath=str(scene), discard_current=True)
            await wait_for_project(client, adapter.instance_id, str(scene))
            saved = await call("growth.layers.inspect", object_name="Field")
            assert saved["valid"] and saved["cache_sha256"] == corrected["cache_sha256"]
            repeat = await call("growth.layers.correct", **settings, replace=True)
            assert repeat["cache_sha256"] == corrected["cache_sha256"]
            impossible = await call(
                "growth.layers.correct",
                **(settings | dict(maximum_lift=0.00001, replace=True)),
            )
            assert not impossible["valid"] and impossible["conflicts"]
            retained = await call("growth.layers.inspect", object_name="Field")
            assert (
                retained["valid"]
                and retained["cache_sha256"] == corrected["cache_sha256"]
            )
            await call(
                "action.edit",
                name="FoldMotion",
                channels=[
                    dict(
                        target=dict(kind="shape", object_name="Support", key="Fold"),
                        keys=[dict(frame=2, value=0.5)],
                    )
                ],
            )
            stale = await call("growth.layers.inspect", object_name="Field")
            assert not stale["valid"]
            await call("growth.layers.clear", object_name="Field")
            summary = dict(
                calls=len(calls),
                argument_bytes=sum(r["argument_bytes"] for r in calls),
                result_bytes=sum(r["result_bytes"] for r in calls),
                seconds=sum(r["seconds"] for r in calls),
                operations=calls,
                correction=corrected,
                coverage=after["coverage"],
                minimum_distance=after["minimum_distance"],
                schema_bytes=sum(
                    len(
                        REGISTRY["blender.growth.layers." + n]
                        .contract.model_dump_json()
                        .encode()
                    )
                    for n in ["correct", "inspect", "clear"]
                ),
                failures=0,
                infeasible_results=1,
                scope=(
                    "Operation arguments/results; excludes RPC envelopes, "
                    "discovery and project registration waits"
                ),
            )
            (tmp_path / "growth-layers-metrics.json").write_text(
                json.dumps(summary, indent=2)
            )
            print("GROWTH_LAYERS_METRICS", json.dumps(summary))
