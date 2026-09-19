"""Real MCP shot switching without action detachment or manual key reconstruction."""

import json
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import running_blender
from .test_e2e import core_client, discover


async def test_animated_controls_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(operation: str, **arguments: Any) -> Any:
                request = dict(
                    adapter_id=adapter.instance_id,
                    operation="blender." + operation,
                    arguments=arguments,
                )
                started = time.perf_counter()
                response = await client.call_tool("tyvrana_execute_operation", request)
                rows.append(
                    dict(
                        operation=operation,
                        request_bytes=len(json.dumps(request).encode()),
                        response_bytes=len(response.model_dump_json().encode()),
                        seconds=time.perf_counter() - started,
                        failed=response.is_error,
                    )
                )
                assert not response.is_error, response
                assert response.structured_content is not None
                return response.structured_content["result"]

            bones = []
            for prefix in ("F", "I", "D"):
                bones.extend(
                    [
                        dict(name=prefix + "0", head=[0, 0, 0], tail=[0, 1, 0]),
                        dict(
                            name=prefix + "1",
                            head=[0, 1, 0],
                            tail=[0, 2, 0.1],
                            parent=prefix + "0",
                            connected=True,
                        ),
                    ]
                )
            await call("armature.create", name="Rig", bones=bones)
            for name, location in [
                ("Goal", [0, 1.7, 0.5]),
                ("Pole", [0, 0, 2]),
                ("ParentA", [1, 0, 0]),
                ("ParentB", [0, 3, 0]),
                ("Control", [2, 1, 0]),
            ]:
                await call(
                    "object.create_primitive",
                    name=name,
                    primitive="cube",
                    location=location,
                )
            await call(
                "control_rig.configure",
                object_name="Rig",
                name="Chain",
                fk=["F0", "F1"],
                ik=["I0", "I1"],
                deform=["D0", "D1"],
                target=dict(object_name="Goal"),
                pole=dict(object_name="Pole"),
            )
            await call(
                "armature.pose",
                object_name="Rig",
                bones=[
                    dict(name="F0", rotation=[0.2, 0, 0]),
                    dict(name="F1", rotation=[0.7, 0, 0]),
                ],
            )
            await call(
                "constraint.configure",
                constraints=[
                    dict(
                        name="Space",
                        owner=dict(object_name="Control"),
                        settings=dict(
                            kind="child_of", target=dict(object_name="ParentA")
                        ),
                    )
                ],
            )
            await call(
                "action.edit",
                name="Shot",
                create=True,
                channels=[
                    dict(
                        target=dict(
                            kind="transform",
                            object_name="Rig",
                            bone="F0",
                            property="rotation",
                            axis="x",
                        ),
                        keys=[dict(frame=1, value=0.2), dict(frame=20, value=0.5)],
                    )
                ],
            )
            await call("action.assign", name="Shot")
            setup_calls = len(rows)
            for frame, mode in [(5, "IK"), (9, "FK")]:
                await call("timeline.configure", frame=frame)
                result = await call(
                    "control_rig.switch",
                    object_name="Rig",
                    name="Chain",
                    mode=mode,
                    keying=dict(action_name="Shot", anchor_frame=frame - 1),
                )
                assert result["valid"] and result["maximum_match_error"] < 0.001
            await call(
                "constraint.switch_space",
                owner=dict(object_name="Control"),
                constraint="Space",
                target=dict(object_name="ParentB"),
                keying=dict(action_name="Shot", anchor_frame=8),
            )
            await call("timeline.configure", frame=12)
            await call(
                "constraint.switch_space",
                owner=dict(object_name="Control"),
                constraint="Space",
                target=dict(object_name="ParentA"),
                keying=dict(action_name="Shot", anchor_frame=11),
            )
            for frame, mode in [(3, "FK"), (6, "IK"), (10, "FK")]:
                await call("timeline.configure", frame=frame)
                result = await call(
                    "control_rig.inspect", object_name="Rig", name="Chain"
                )
                assert result["valid"] and result["mode"] == mode
            action = await call("action.inspect", name="Shot", limit=1, key_limit=16)
            assert action["valid"] and action["slot_count"] == 4
            keys = action["channels"][0]["keys"]
            assert {(k["frame"], k["value"]) for k in keys}.issuperset(
                {(1, 0.20000000298023224), (20, 0.5)}
            )
            metrics = dict(
                calls=len(rows),
                setup_calls=setup_calls,
                request_bytes=sum(r["request_bytes"] for r in rows),
                response_bytes=sum(r["response_bytes"] for r in rows),
                seconds=sum(r["seconds"] for r in rows),
                failures=sum(bool(r["failed"]) for r in rows),
                schema_bytes=sum(
                    len(REGISTRY["blender." + op].contract.model_dump_json().encode())
                    for op in {r["operation"] for r in rows}
                ),
                operations=rows,
            )
            (tmp_path / "animated-controls-metrics.json").write_text(
                json.dumps(metrics, indent=2)
            )
            print("ANIMATED_CONTROLS_METRICS", json.dumps(metrics))
