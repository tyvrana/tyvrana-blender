"""Packaged native structural and mechanical fixtures."""

import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_loft_motion(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/loft_motion_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "loft-motion.log").write_text(log)
    assert result.returncode == 0, log
    assert "LOFT_MOTION_NATIVE_PASSED" in log


async def test_structural_mcp_benchmark(
    profile: dict[str, str], tmp_path: Path
) -> None:
    import json
    import math
    import time
    from typing import Any

    from tyvrana_blender.operations import REGISTRY

    from .conftest import running_blender
    from .test_e2e import core_client, discover

    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            rows = []

            async def call(name: str, /, **args: Any) -> Any:
                request = dict(
                    adapter_id=adapter.instance_id,
                    operation="blender." + name,
                    arguments=args,
                )
                start = time.perf_counter()
                response = await client.call_tool("tyvrana_execute_operation", request)
                rows.append(
                    dict(
                        request_bytes=len(json.dumps(request).encode()),
                        response_bytes=len(response.model_dump_json().encode()),
                        seconds=time.perf_counter() - start,
                    )
                )
                assert not response.is_error, response.content
                return response.structured_content["result"]

            components = []
            for i in range(12):
                sections = [
                    dict(
                        center=[i * 2, j * 0.5, 0],
                        radii=[
                            0.2 + 0.1 * j,
                            0.15 + 0.03 * j,
                            0.12 + 0.04 * j,
                            0.08 + 0.03 * j,
                        ],
                    )
                    for j in range(5)
                ]
                components.append(
                    dict(
                        name=f"Component{i}",
                        sections=sections,
                        interpolation="linear",
                        sides=16,
                        subdivisions=4,
                        caps=False,
                    )
                )
                vertices = []
                for k in range(17):
                    t = k / 4
                    radii = [
                        0.2 + 0.1 * t,
                        0.15 + 0.03 * t,
                        0.12 + 0.04 * t,
                        0.08 + 0.03 * t,
                    ]
                    for side in range(16):
                        u, v = (
                            math.cos(side * math.pi / 8),
                            math.sin(side * math.pi / 8),
                        )
                        vertices.append(
                            [
                                i * 2 + u * radii[0 if u >= 0 else 1],
                                t * 0.5,
                                v * radii[2 if v >= 0 else 3],
                            ]
                        )
                faces = [
                    [
                        r * 16 + s,
                        (r + 1) * 16 + s,
                        (r + 1) * 16 + (s + 1) % 16,
                        r * 16 + (s + 1) % 16,
                    ]
                    for r in range(16)
                    for s in range(16)
                ]
                await call(
                    "mesh.create", name=f"Raw{i}", vertices=vertices, faces=faces
                )
            before = list(rows)
            rows.clear()
            created = await call("loft.create", components=components)
            after = list(rows)
            rows.clear()
            assert len(created["components"]) == 12
            for i, c in enumerate(created["components"]):
                assert (
                    c["vertex_count"] == 272 and c["face_count"] == 256 and c["valid"]
                )
                assert abs(c["bounds_max"][0] - (i * 2 + 0.6)) < 1e-5
            check = await call(
                "geometry.inspect",
                pairs=[dict(left="Raw0", right="Component0")],
                max_triangle_tests=200000,
            )
            assert check["minimum_distance"] < 1e-7
            for name in ["Control", "Response"]:
                await call("object.create_primitive", primitive="cube", name=name)
            await call(
                "motion.set_properties",
                properties=[
                    dict(
                        object_name="Control", name=n, value=v, minimum=-10, maximum=10
                    )
                    for n, v in [("a", 0.4), ("b", 0.6)]
                ],
            )
            rows.clear()
            source = dict(kind="property", object_name="Control", property="a")
            await call(
                "coupling.configure",
                couplings=[
                    dict(
                        name="Combined",
                        source=source,
                        additional_sources=[
                            dict(channel=source | dict(property="b"), weight=0.5)
                        ],
                        target=dict(
                            kind="transform",
                            object_name="Response",
                            property="location",
                            axis="x",
                        ),
                        mapping=dict(
                            kind="piecewise",
                            knots=[
                                dict(input=0, output=0),
                                dict(input=1, output=2),
                                dict(input=2, output=3),
                            ],
                        ),
                    )
                ],
            )
            inspected = await call("coupling.inspect", names=["Combined"])
            assert inspected["couplings"][0]["valid"]
            assert abs(inspected["couplings"][0]["evaluated_target_value"] - 1.4) < 1e-5
            sample = await call(
                "motion.sample", frames=[1, 5, 10], couplings=["Combined"]
            )
            assert sample["restored"]

            def total(data: list[dict[str, Any]]) -> dict[str, Any]:
                return dict(
                    calls=len(data),
                    operations=len(data),
                    request_bytes=sum(r["request_bytes"] for r in data),
                    response_bytes=sum(r["response_bytes"] for r in data),
                    elapsed_seconds=sum(r["seconds"] for r in data),
                    failures=0,
                    retries=0,
                    renders=0,
                    polls=0,
                )

            result = dict(
                loft=dict(
                    before=total(before),
                    after=total(after),
                    native_seconds=created["processing_seconds"],
                    schema_bytes=len(
                        REGISTRY["blender.loft.create"]
                        .contract.model_dump_json()
                        .encode()
                    ),
                ),
                coupling=dict(
                    after=total(rows),
                    before="No equivalent typed multi-source/piecewise predecessor",
                    schema_bytes=len(
                        REGISTRY["blender.coupling.configure"]
                        .contract.model_dump_json()
                        .encode()
                    ),
                ),
            )
            (tmp_path / "structural-metrics.json").write_text(json.dumps(result))
            print("STRUCTURAL_MCP_METRICS", json.dumps(result))
