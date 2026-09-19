"""Dense deforming templates, transient contact and fractional-time MCP QA."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_instance_geometry(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/geometry_elements_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "elements-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "GEOMETRY_ELEMENTS_NATIVE_PASSED" in log


async def test_adaptive_dense_templates(
    profile: dict[str, str], tmp_path: Path
) -> None:
    metrics = []
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
                metrics.append(
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
                        name="Tilt",
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
                            delta=[0, 0, 0.2],
                        ),
                    )
                ],
            )
            await call(
                "mesh.create",
                name="Template",
                hidden=True,
                vertices=[[-0.012, 0, 0], [0.012, 0, 0], [0.012, 0, 1], [-0.012, 0, 1]],
                faces=[[0, 1, 2, 3]],
            )
            await call(
                "growth.create",
                name="Field",
                surface="Support",
                families=[
                    dict(
                        name="Strip",
                        length=0.5,
                        shape=[[0, 0, 0], [0, 0, 1]],
                        template=dict(object_name="Template", mode="deform"),
                    )
                ],
                regions=[
                    dict(
                        name="Panel",
                        family="Strip",
                        guides=0,
                        rows=[
                            dict(name=f"Row{i}", path=[[0.1, v], [0.9, v]], count=80)
                            for i, v in enumerate([0.45, 0.55])
                        ],
                    )
                ],
            )
            await call(
                "object.create_primitive",
                primitive="cube",
                name="Probe",
                location=[-5, 0, 0.3],
                scale=[0.015, 0.4, 0.4],
            )
            await call(
                "action.edit",
                name="Motion",
                create=True,
                channels=[
                    dict(
                        target=dict(
                            kind="transform",
                            object_name="Probe",
                            property="location",
                            axis="x",
                        ),
                        keys=[dict(frame=1, value=-5), dict(frame=3, value=5)],
                    ),
                    dict(
                        target=dict(kind="shape", object_name="Support", key="Tilt"),
                        keys=[
                            dict(frame=1, value=0),
                            dict(frame=2, value=0.8),
                            dict(frame=3, value=0),
                        ],
                    ),
                ],
            )
            await call("action.assign", name="Motion")
            await call("timeline.configure", frame=7, subframe=0.25)
            query = [
                dict(object_name="Field", self_intersection=False, obstacles=["Probe"])
            ]
            endpoints = await call(
                "geometry.inspect", instances=query, frames=[1, 3], worst_limit=2
            )
            assert all(
                s["instances"][0]["contact_element_pairs"] == 0
                for s in endpoints["samples"]
            )
            adaptive = await call(
                "geometry.inspect",
                instances=query,
                worst_limit=2,
                adaptive=dict(
                    start=1,
                    end=3,
                    initial_samples=2,
                    max_samples=13,
                    minimum_step=0.025,
                ),
            )
            assert any(
                s["instances"][0]["contact_element_pairs"] > 0
                for s in adaptive["samples"]
            )
            assert any(s["frame"] % 1 for s in adaptive["samples"])
            assert adaptive["coverage"]["sample_count"] <= 13
            assert adaptive["restored"] and adaptive["minimum_distance"] == 0
            timeline = await call("timeline.inspect")
            assert timeline["frame"] == 7 and timeline["subframe"] == 0.25
            summary = dict(
                calls=len(metrics),
                argument_bytes=sum(r["argument_bytes"] for r in metrics),
                result_bytes=sum(r["result_bytes"] for r in metrics),
                seconds=sum(r["seconds"] for r in metrics),
                schema_bytes=len(
                    REGISTRY["blender.geometry.inspect"]
                    .contract.model_dump_json()
                    .encode()
                ),
                roots=160,
                failures=0,
                operations=metrics,
                coverage=adaptive["coverage"],
                processing_seconds=adaptive["processing_seconds"],
                triangle_tests=adaptive["triangle_tests"],
                transformed_vertices=adaptive["evaluated_vertex_samples"],
                scope=(
                    "Operation arguments/results; excludes RPC envelopes and discovery"
                ),
            )
            (tmp_path / "adaptive-instance-metrics.json").write_text(
                json.dumps(summary, indent=2)
            )
            print("ADAPTIVE_INSTANCE_METRICS", json.dumps(summary))
