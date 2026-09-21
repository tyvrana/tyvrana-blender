"""Packaged native and typed MCP mixed closed-chain qualification."""

import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_closed_chain(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/closed_chain_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "closed-chain-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "CLOSED_CHAIN_NATIVE_PASSED 7" in log


async def test_closed_chain_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    import json
    from typing import Any

    from tests.closed_chain_fixture import commands, sample_args
    from tyvrana_blender.operations import REGISTRY

    from .conftest import running_blender
    from .test_e2e import core_client

    calls: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        assert port not in {8765, 8766, 8767}
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):

            async def tool(name: str, arguments: dict[str, Any]) -> Any:
                result = await client.call_tool(name, arguments)
                calls.append(
                    dict(
                        tool=name,
                        operation=arguments.get("operation"),
                        request_bytes=len(
                            json.dumps(
                                dict(name=name, arguments=arguments),
                                separators=(",", ":"),
                            ).encode()
                        ),
                        response_bytes=len(
                            result.model_dump_json(by_alias=True).encode()
                        ),
                        failed=result.is_error,
                    )
                )
                (tmp_path / "closed-chain-mcp-calls.json").write_text(
                    json.dumps(calls, indent=2)
                )
                assert not result.is_error, result.content
                return result.structured_content

            adapters = await tool(
                "tyvrana_list_adapters", dict(application="blender", wait_seconds=12)
            )
            assert len(adapters["adapters"]) == 1, adapters
            adapter_id = adapters["adapters"][0]["instance_id"]
            await tool(
                "tyvrana_list_operations",
                dict(
                    adapter_id=adapter_id,
                    names=[
                        "blender.coupling.configure",
                        "blender.coupling.solve",
                        "blender.motion.sample",
                    ],
                    schemas="arguments",
                ),
            )

            async def call(op: str, /, **args: Any) -> Any:
                REGISTRY["blender." + op].parse(args)
                result = await tool(
                    "tyvrana_execute_operation",
                    dict(
                        adapter_id=adapter_id, operation="blender." + op, arguments=args
                    ),
                )
                assert result["result"].get("error") is None, result
                return result["result"]

            for op, args in commands(unrelated=320):
                await call(op, **args)
            await call("timeline.configure", frame=7, subframe=0.25)

            async def snapshot() -> Any:
                return [
                    await call("timeline.inspect"),
                    await call(
                        "armature.inspect",
                        object_name="Rig",
                        bone_names=["Crank", "Rod"],
                    ),
                    await call(
                        "object_set.inspect",
                        names=["Rig", "Slider", "Probe", "Rail"],
                        fields=["transforms"],
                    ),
                ]

            before = await snapshot()
            solution = await call("coupling.solve", name="Linkage")
            assert (
                solution["status"] == "SOLVED"
                and solution["restored"]
                and not solution["applied"]
            ), solution
            inspected = await call("coupling.inspect", names=["Linkage"])
            assert len(inspected["mechanisms"]) == 1
            scene = await call("scene.inspect", limit=1)
            assert scene["object_count"] == 324
            sampled = await call("motion.sample", **sample_args())
            mechanism = sampled["mechanism"]
            assert (
                mechanism["solved_samples"] == 33 and mechanism["failed_samples"] == 0
            ), mechanism
            assert (
                mechanism["branch_discontinuities"] == 0
                and mechanism["maximum_closure_residual"] < 0.00002
            )
            assert sampled["restored"] and sampled["scoped_object_count"] == 4
            assert sampled["violation_count"] == 0 and not sampled["details"], sampled
            assert sampled["contacts"][0]["counts"]["PERMITTED_CONTACT"] == 33, sampled
            assert await snapshot() == before
            await call("action.assign", name="Drive", detach=True)
            await call("coupling.remove", names=["Linkage"])
            await call("action.remove", name="Drive")
            # This host contains only the synthetic fixture. Canonical file reset
            # removes the constrained carriers and all remaining native resources.
            await call("file.new", discard_current=True)
            assert (await call("scene.inspect", limit=1))["object_count"] == 0
            evidence = dict(
                calls=len(calls),
                request_bytes=sum(c["request_bytes"] for c in calls),
                response_bytes=sum(c["response_bytes"] for c in calls),
                failures=sum(c["failed"] for c in calls),
                retries=0,
                total_objects=324,
                scoped_objects=4,
                samples=33,
                manual_intermediate_xyz=False,
                per_frame_loop=False,
                branch_continuity=True,
                restoration=True,
                contact_aggregation=True,
                cleanup=True,
                mechanism=mechanism,
                solution=solution,
                contacts=sampled["contacts"],
                rigid_length=[
                    m
                    for m in sampled["metrics"]
                    if m["name"].startswith("measurement.RodLength")
                ],
                operations=calls,
            )
            (tmp_path / "closed-chain-mcp-metrics.json").write_text(
                json.dumps(evidence, indent=2)
            )
            print("CLOSED_CHAIN_MCP_METRICS", json.dumps(evidence))
