"""Typed continuous fields/ordered roots, compact revision and native persistence."""

import copy
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation, wait_for_project


def test_native_growth_domains(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/growth_domain_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=90,
    )
    log = result.stdout + result.stderr
    (tmp_path / "domains-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "GROWTH_DOMAIN_NATIVE_PASSED" in log


async def test_ordered_field_revision_and_persistence(
    profile: dict[str, str], tmp_path: Path
) -> None:
    calls = []
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
                "mesh.create",
                name="Strip",
                hidden=True,
                vertices=[[-0.03, 0, 0], [0.03, 0, 0], [0.03, 0, 1], [-0.03, 0, 1]],
                faces=[[0, 1, 2, 3]],
            )
            region = dict(
                name="Panel",
                family="Strip",
                guides=0,
                field=dict(
                    controls=[
                        dict(uv=[0.1, 0.1], direction=[1, 0], length_scale=0.75),
                        dict(uv=[0.4, 0.9], direction=[0.2, 1], length_scale=1.5),
                    ]
                ),
                rows=[
                    dict(
                        name=f"Row{i}",
                        path=[[0.1, v], [0.4, v]],
                        count=80,
                        mirror="u",
                        order=i,
                        layer=i,
                        overlap="over_previous" if i else "none",
                    )
                    for i, v in enumerate([0.2, 0.4, 0.6])
                ],
            )
            created = await call(
                "growth.create",
                name="Field",
                surface="Support",
                families=[
                    dict(name="Strip", length=0.25, template=dict(object_name="Strip"))
                ],
                regions=[region],
            )
            assert created["summary"]["instance_count"] == 480
            before = await call(
                "growth.inspect",
                object_name="Field",
                guide_limit=8,
                include_rows=True,
                field_samples=[dict(region="Panel", uv=[0.2, 0.4])],
            )
            assert len(before["rows"]) == 6 and before["summary"]["valid"]
            revised = copy.deepcopy(region)
            revised["field"]["controls"][0]["length_scale"] = 1.1
            await call("growth.configure", object_name="Field", regions=[revised])
            after = await call(
                "growth.inspect", object_name="Field", guide_limit=8, include_rows=True
            )
            assert before["guides"] == after["guides"]
            assert after["summary"]["qa"]["maximum_root_error"] < 1e-4
            destination = tmp_path / "ordered.blend"
            await call("file.save", filepath=str(destination))
            await wait_for_project(client, adapter.instance_id, str(destination))
            await call("file.open", filepath=str(destination), discard_current=True)
            reopened = await call(
                "growth.inspect", object_name="Field", guide_limit=8, include_rows=True
            )
            assert reopened["guides"] == after["guides"]
            assert reopened["rows"] == after["rows"]
            assert reopened["summary"]["system_id"] == after["summary"]["system_id"]
            summary = dict(
                calls=len(calls),
                argument_bytes=sum(x["argument_bytes"] for x in calls),
                result_bytes=sum(x["result_bytes"] for x in calls),
                seconds=sum(x["seconds"] for x in calls),
                schema_bytes=sum(
                    len(REGISTRY["blender." + name].contract.model_dump_json().encode())
                    for name in {x["operation"] for x in calls}
                ),
                output_roots=480,
                failures=0,
                operations=calls,
                scope=(
                    "Operation arguments/results; excludes RPC envelopes, "
                    "discovery and save wait"
                ),
            )
            (tmp_path / "domains-metrics.json").write_text(
                json.dumps(summary, indent=2)
            )
            print("GROWTH_DOMAINS_METRICS", json.dumps(summary))
