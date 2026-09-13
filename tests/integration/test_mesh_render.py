import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tyvrana_protocol import JsonValue

from tyvrana_blender.mesh_models import MeshEditResult, MeshSummary
from tyvrana_blender.models import SceneSummary

from ..png import mean_pixel_difference, red_bounds
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
@pytest.mark.parametrize(
    "edit", ["extrude", "bevel", "inset_extrude", "regional_transform", "shading"]
)
async def test_modeling_changes_real_render_over_mcp(
    profile: dict[str, str], tmp_path: Path, ui: bool, edit: str
) -> None:
    profile["TYVRANA_TEST_MESH"] = "1"
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop = asyncio.get_running_loop()
        async with core_client(tmp_path) as (client, port):
            profile["TYVRANA_TEST_PORT"] = str(port)
            async with running_blender(profile, tmp_path, ui=ui):
                registered = await discover(client)
                assert registered is not None
                identifier = registered.instance_id

                async def mesh(name: str, **arguments: JsonValue) -> JsonValue:
                    return await operation(
                        client,
                        identifier,
                        "blender.mesh." + name,
                        {"object_name": "Surface", **arguments},
                    )

                scene = SceneSummary.model_validate(
                    await operation(client, identifier, "blender.scene.inspect", {})
                )
                resources = {
                    kind: await operation(
                        client, identifier, "blender." + kind + ".inspect", {}
                    )
                    for kind in ["camera", "light", "material", "image"]
                }
                original = MeshSummary.model_validate(await mesh("inspect"))
                assert (
                    original.vertex_count,
                    original.edge_count,
                    original.face_count,
                ) == (8, 12, 6)
                assert original.bounds_min == [-1, -1, 0] and original.bounds_max == [
                    1,
                    1,
                    1,
                ]
                before = await render(client, identifier)
                vertices = await mesh(
                    "query", selector={"domain": "vertex", "mode": "all"}
                )
                top: JsonValue = {
                    "domain": "face",
                    "mode": "normal",
                    "direction": [0, 0, 1],
                    "min_dot": 0.99,
                }
                faces = await mesh("query", selector=top)
                assert isinstance(faces, dict) and faces["matched_count"] == 1
                if edit == "extrude":
                    result = await mesh("extrude_faces", selector=top, offset=[0, 0, 1])
                    expected = (12, 20, 10)
                elif edit == "bevel":
                    result = await mesh(
                        "bevel_edges",
                        selector={"domain": "edge", "mode": "all"},
                        width=0.15,
                        segments=4,
                    )
                    expected = (152, 300, 150)
                elif edit == "inset_extrude":
                    inset = MeshEditResult.model_validate(
                        await mesh("inset_faces", selector=top, thickness=0.25)
                    )
                    assert (
                        inset.region_faces is not None and inset.region_faces.total == 1
                    )
                    inner: JsonValue = {
                        "domain": "face",
                        "mode": "indices",
                        "indices": list(inset.region_faces.indices),
                    }
                    await mesh("query", selector=inner)
                    result = await mesh(
                        "extrude_faces", selector=inner, offset=[0, 0, 0.8]
                    )
                    expected = (16, 28, 14)
                elif edit == "shading":
                    result = await mesh(
                        "set_shading",
                        selector={"domain": "face", "mode": "all"},
                        smooth=True,
                    )
                    expected = (8, 12, 6)
                    assert MeshEditResult.model_validate(result).changed_faces == 6
                else:
                    result = await mesh(
                        "transform",
                        selector={
                            "domain": "vertex",
                            "mode": "box",
                            "min": [-1, -1, 1],
                            "max": [1, 1, 1],
                        },
                        translation=[0.65, 0, 0],
                    )
                    expected = (8, 12, 6)
                changed = MeshEditResult.model_validate(result)
                current = MeshSummary.model_validate(await mesh("inspect"))
                assert current == changed.mesh
                assert (
                    current.vertex_count,
                    current.edge_count,
                    current.face_count,
                ) == expected
                assert current.manifold_summary.non_manifold_edge_count == 0
                after = await render(client, identifier)
                difference = await loop.run_in_executor(
                    executor, mean_pixel_difference, before, after
                )
                before_box = await loop.run_in_executor(executor, red_bounds, before)
                after_box = await loop.run_in_executor(executor, red_bounds, after)
                assert difference > 0.5
                if edit in {"extrude", "inset_extrude"}:
                    assert after_box[1] < before_box[1] - 15
                    assert (
                        current.bounds_max is not None and current.bounds_max[2] > 1.7
                    )
                elif edit in {"bevel", "shading"}:
                    assert current.bounds_min == original.bounds_min
                    assert current.bounds_max == original.bounds_max
                    if edit == "shading":
                        # Color thresholds change with lighting interpolation;
                        # compare actual geometry rather than a red pixel box.
                        assert (
                            await mesh(
                                "query", selector={"domain": "vertex", "mode": "all"}
                            )
                            == vertices
                        )
                        await mesh(
                            "set_shading",
                            selector={"domain": "face", "mode": "all"},
                            smooth=False,
                        )
                        restored = await render(client, identifier)
                        # PNG metadata includes render timing; compare pixels.
                        assert (
                            await loop.run_in_executor(
                                executor, mean_pixel_difference, before, restored
                            )
                            == 0
                        )
                else:
                    assert after_box[2] > before_box[2] + 10
                    assert (
                        current.bounds_max is not None and current.bounds_max[0] > 1.6
                    )
                print(
                    "MESH_RENDER",
                    edit,
                    ui,
                    "counts",
                    expected,
                    "difference",
                    difference,
                    "silhouette",
                    before_box,
                    after_box,
                )
                (tmp_path / "before.png").write_bytes(before)
                (tmp_path / "after.png").write_bytes(after)
                final_scene = SceneSummary.model_validate(
                    await operation(client, identifier, "blender.scene.inspect", {})
                )
                assert final_scene.object_count == scene.object_count
                for previous, following in zip(
                    scene.objects, final_scene.objects, strict=True
                ):
                    assert previous.model_dump(
                        exclude={"dimensions"}
                    ) == following.model_dump(exclude={"dimensions"})
                for kind, previous_resource in resources.items():
                    assert (
                        await operation(
                            client, identifier, "blender." + kind + ".inspect", {}
                        )
                        == previous_resource
                    )
            await discover(client, empty=True)
