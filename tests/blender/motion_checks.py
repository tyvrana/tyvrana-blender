"""Native motion contracts, coupled mechanics and frame restoration fixtures."""

import importlib
import json
import math
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE  # noqa: E402
from tests.blender.volume_checks import VolumeTests  # noqa: E402

couplings = importlib.import_module(PACKAGE + "couplings")
motion = importlib.import_module(PACKAGE + "motion")


def transform(
    obj: str = "Rig", bone: str | None = None, prop: str = "rotation", axis: str = "x"
) -> dict[str, Any]:
    return dict(kind="transform", object_name=obj, bone=bone, property=prop, axis=axis)


def mapping(
    scale: float = 0.5, minimum: float = -2.0, maximum: float = 2.0
) -> dict[str, Any]:
    return dict(
        kind="linear", scale=scale, clamp=dict(minimum=minimum, maximum=maximum)
    )


class MotionTests(VolumeTests):
    def setUp(self) -> None:
        super().setUp()
        for key in list(bpy.context.scene.keys()):
            if key.startswith(couplings.KEY):
                del bpy.context.scene[key]
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        bpy.context.scene.frame_set(1)

    def chain(self, n: int = 3) -> Any:
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(
                    name=f"J{i}",
                    head=[0, i, 0],
                    tail=[0, i + 1, 0],
                    x_reference=[1, 0, 0],
                    limits=dict(
                        x=dict(minimum=-1.6, maximum=1.6),
                        y=dict(minimum=-0.7, maximum=0.7),
                        z=dict(minimum=-1.2, maximum=1.2),
                    ),
                    **(dict(parent=f"J{i - 1}", connected=True) if i else {}),
                )
                for i in range(n)
            ],
        )
        return bpy.data.objects["Rig"]

    def test_batched_endpoint_measurements_and_failure_restoration(self) -> None:
        self.chain(2)
        a = dict(kind="bone", object="Rig", bone="J0", endpoint="tail")
        b = dict(kind="bone", object="Rig", bone="J1", endpoint="head")
        queries = [
            dict(
                kind="distance",
                name="Closure",
                a=a,
                b=b,
                comparison=dict(target=0, tolerance=0.0001),
            ),
            dict(
                kind="angle",
                name="Angle",
                a=dict(kind="world", point=[1, 0, 0]),
                vertex=dict(kind="world", point=[0, 0, 0]),
                b=dict(kind="world", point=[0, 1, 0]),
                comparison=dict(target=80, tolerance=1),
            ),
        ]
        bpy.context.scene.frame_set(7, subframe=0.25)
        report = self.call("motion.sample", frames=[1, 2, 3], measurements=queries)
        self.assertEqual(report["violation_count"], 3)
        values = {m["name"]: m for m in report["metrics"]}
        self.assertEqual(values["measurement.Closure.value"]["maximum"], 0)
        self.assertAlmostEqual(values["measurement.Angle.value"]["maximum"], 90)
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, 0.25)
        queries[0]["a"] = dict(kind="bone", object="Rig", bone="Missing")
        self.error("motion.sample", frames=[1, 2], measurements=queries)
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, 0.25)

    def coupling(
        self,
        name: str = "Follow",
        source: Any = None,
        target: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self.call(
            "coupling.configure",
            couplings=[
                dict(
                    name=name,
                    source=source or transform(bone="J0"),
                    target=target or transform(bone="J1"),
                    mapping=kwargs.pop("mapping", mapping()),
                )
            ],
            **kwargs,
        )

    def clip(self, channels: list[dict[str, Any]] | None = None) -> Any:
        result = self.call(
            "action.edit",
            name="Clip",
            create=True,
            channels=channels
            or [
                dict(
                    target=transform(bone="J0"),
                    keys=[
                        dict(frame=1, value=0),
                        dict(frame=11, value=1.5),
                        dict(frame=21, value=-1.5),
                    ],
                )
            ],
        )
        self.call("action.assign", name="Clip")
        return result

    def qa(self, **kwargs: Any) -> Any:
        return self.call(
            "motion.sample",
            frames=[1, 6, 11, 16, 21],
            armature_object="Rig",
            bones=["J0", "J1", "J2"],
            couplings=["Follow"],
            **kwargs,
        )

    def metric(self, result: Any, name: str) -> Any:
        return next(m for m in result["metrics"] if m["name"] == name)

    def test_mechanical_positive_inverse_clamp_and_chain_path(self) -> None:
        rig = self.chain()
        self.coupling()
        self.coupling(
            "Inverse",
            transform(bone="J1"),
            transform(bone="J2"),
            mapping=mapping(-2, -1, 1),
        )
        self.clip()
        self.call(
            "timeline.configure",
            frame=7,
            subframe=0.25,
            frame_start=1,
            frame_end=21,
            fps=30,
            fps_base=1.001,
        )
        saved = rig.pose.bones["J0"].rotation_euler[:]
        result = self.qa(detail_frames=[1, 11, 21])
        self.assertTrue(result["restored"])
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, 0.25)
        self.assertEqual(saved, rig.pose.bones["J0"].rotation_euler[:])
        self.assertLess(
            self.metric(result, "coupling.Follow.mapping_error")["maximum"], 1e-6
        )
        self.assertAlmostEqual(
            self.metric(result, "joint.J1.x.evaluated")["maximum"], 0.75, places=5
        )
        self.assertAlmostEqual(
            self.metric(result, "joint.J2.x.evaluated")["minimum"], -1, places=5
        )
        self.assertGreater(
            self.metric(result, "joint.J2.tail_z")["maximum"]
            - self.metric(result, "joint.J2.tail_z")["minimum"],
            0.2,
        )
        print("MOTION_MECHANICAL", json.dumps(result))

    def test_cycle_direct_long_parent_and_key_conflicts(self) -> None:
        self.chain()
        self.coupling()
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="Self",
                    source=transform(bone="J0"),
                    target=transform(bone="J0", axis="z"),
                    mapping=mapping(),
                )
            ],
        )
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="Cycle",
                    source=transform(bone="J1"),
                    target=transform(bone="J0"),
                    mapping=mapping(),
                )
            ],
        )
        self.coupling("Third", transform(bone="J1"), transform(bone="J2"))
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="LongCycle",
                    source=transform(bone="J2"),
                    target=transform(bone="J0"),
                    mapping=mapping(),
                )
            ],
        )
        self.error(
            "action.edit",
            name="Bad",
            create=True,
            channels=[dict(target=transform(bone="J1"), keys=[dict(frame=1, value=0)])],
        )
        self.clip()
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="KeyConflict",
                    source=transform(bone="J2"),
                    target=transform(bone="J0"),
                    mapping=mapping(),
                )
            ],
        )
        self.assertEqual(len(bpy.data.objects["Rig"].animation_data.drivers), 2)
        self.assertNotIn("Bad", bpy.data.actions)

    def test_mapping_replace_remove_and_unrelated_driver_preserved(self) -> None:
        self.chain()
        self.coupling()
        self.clip()
        self.call("timeline.configure", frame=11)
        self.coupling(mapping=mapping(0.25), replace=True)
        result = self.call("coupling.inspect", names=["Follow"])["couplings"][0]
        self.assertTrue(result["valid"], result)
        self.assertAlmostEqual(result["target_value"], 0.375, places=6)
        self.call("object.create_primitive", primitive="cube", name="Unrelated")
        unrelated = bpy.data.objects["Unrelated"].driver_add("location", 0)
        unrelated.driver.expression = "0.25"
        self.call("coupling.remove", names=["Follow"])
        self.assertEqual(len(bpy.data.objects["Rig"].animation_data.drivers), 0)
        self.assertEqual(unrelated.driver.expression, "0.25")

    def test_action_edit_interpolation_extrapolation_ownership(self) -> None:
        self.chain()
        self.coupling()
        self.call("object.create_primitive", primitive="cube", name="Mover")
        self.clip(
            [
                dict(
                    target=transform(bone="J0"),
                    keys=[
                        dict(frame=1, value=0, interpolation="LINEAR"),
                        dict(frame=11, value=1),
                    ],
                ),
                dict(
                    target=transform("Mover", prop="location"),
                    keys=[
                        dict(frame=1, value=0, interpolation="CONSTANT"),
                        dict(frame=11, value=3),
                    ],
                ),
                dict(
                    target=transform("Mover", prop="location", axis="z"),
                    keys=[
                        dict(frame=1, value=0, interpolation="BEZIER"),
                        dict(frame=11, value=2),
                    ],
                    extrapolation="LINEAR",
                ),
            ]
        )
        self.call("timeline.configure", frame=6)
        self.assertAlmostEqual(bpy.data.objects["Mover"].location.x, 0)
        self.assertAlmostEqual(bpy.data.objects["Mover"].location.z, 1, places=5)
        self.call(
            "action.edit",
            name="Clip",
            channels=[
                dict(
                    target=transform(bone="J0"),
                    keys=[dict(frame=11, value=0.5), dict(frame=21, value=1)],
                )
            ],
        )
        result = self.call("action.inspect", name="Clip", key_limit=4)
        self.assertEqual(result["key_count"], 7)
        self.assertEqual(result["slot_count"], 2)
        self.call(
            "action.edit",
            name="Clip",
            channels=[dict(target=transform(bone="J0"), remove_frames=[21])],
        )
        self.error("action.remove", name="Clip")
        self.call("action.assign", name="Clip", detach=True)
        self.call("action.remove", name="Clip")
        self.assertNotIn("Clip", bpy.data.actions)

    def test_scalar_and_constraint_influence(self) -> None:
        self.chain()
        self.call("object.create_primitive", primitive="cube", name="Control")
        self.call(
            "motion.set_properties",
            properties=[dict(object_name="Control", name="Blend", value=0.5)],
        )
        constraint = bpy.data.objects["Rig"].pose.bones["J1"].constraints[0].name
        self.coupling(
            source=dict(kind="property", object_name="Control", property="Blend"),
            target=dict(
                kind="constraint", object_name="Rig", bone="J1", constraint=constraint
            ),
            mapping=mapping(1, 0, 1),
        )
        result = self.call("coupling.inspect")["couplings"][0]
        self.assertTrue(result["valid"], result)
        self.assertAlmostEqual(result["target_value"], 0.5)
        self.call(
            "motion.set_properties",
            properties=[dict(object_name="Control", name="Blend", value=0.25)],
        )
        self.assertAlmostEqual(
            self.call("coupling.inspect")["couplings"][0]["target_value"], 0.25
        )

    def test_invalid_specs_ownership_and_injected_batch_rollback(self) -> None:
        self.chain()
        self.coupling()
        before = bpy.data.objects["Rig"].animation_data.drivers[0].driver.expression
        original = couplings.create_driver

        def fail_second(spec: Any) -> None:
            if spec.name == "Fail":
                raise RuntimeError("injected driver publication")
            original(spec)

        with patch.object(couplings, "create_driver", fail_second):
            self.error(
                "coupling.configure",
                replace=True,
                couplings=[
                    dict(
                        name="Follow",
                        source=transform(bone="J0"),
                        target=transform(bone="J1"),
                        mapping=mapping(0.2),
                    ),
                    dict(
                        name="Fail",
                        source=transform(bone="J0"),
                        target=transform(bone="J2"),
                        mapping=mapping(),
                    ),
                ],
            )
        self.assertEqual(len(bpy.data.objects["Rig"].animation_data.drivers), 1)
        self.assertEqual(
            bpy.data.objects["Rig"].animation_data.drivers[0].driver.expression, before
        )
        curve = bpy.data.objects["Rig"].animation_data.drivers[0]
        curve.driver.expression = "x*0.123"
        self.assertFalse(self.call("coupling.inspect")["couplings"][0]["valid"])
        self.error("coupling.remove", names=["Follow"])
        self.assertEqual(curve.driver.expression, "x*0.123")

    def test_failed_qa_restores_frame_pose_and_unkeyed_values(self) -> None:
        rig = self.chain()
        self.coupling()
        self.clip()
        self.call("timeline.configure", frame=7, subframe=0.5)
        rig.pose.bones["J2"].rotation_euler.z = 0.321
        bpy.context.view_layer.update()
        before = [tuple(p.rotation_euler) for p in rig.pose.bones]
        with patch.object(
            motion, "aggregate", side_effect=RuntimeError("injected aggregate failure")
        ):
            self.error("motion.sample", frames=[1, 11, 21], couplings=["Follow"])
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, 0.5)
        self.assertEqual(before, [tuple(p.rotation_euler) for p in rig.pose.bones])

    def test_moderate_action_and_compact_sampling(self) -> None:
        self.call(
            "object_set.create",
            objects=[dict(key=f"M{i}", name=f"M{i}", kind="empty") for i in range(24)],
        )
        specs = [
            dict(
                target=transform(f"M{i}", prop="location", axis=axis),
                keys=[
                    dict(
                        frame=f,
                        value=math.sin(f * 0.1 + i),
                        interpolation="BEZIER" if axis == "z" else "LINEAR",
                    )
                    for f in range(1, 65)
                ],
            )
            for i in range(12)
            for axis in "xyz"
        ]
        start = time.perf_counter()
        self.call("action.edit", name="Scale", create=True, channels=specs)
        author = time.perf_counter() - start
        self.call("action.assign", name="Scale")
        self.call(
            "coupling.configure",
            couplings=[
                dict(
                    name=f"Link{i}",
                    source=transform(f"M{i}", prop="location"),
                    target=transform(f"M{i + 12}", prop="location"),
                    mapping=dict(kind="linear", scale=0.5),
                )
                for i in range(12)
            ],
        )
        inspection = self.call("action.inspect", name="Scale", limit=12)
        start = time.perf_counter()
        qa = self.call(
            "motion.sample",
            range=dict(start=1, end=100, step=1),
            couplings=[f"Link{i}" for i in range(12)],
            channels=[
                dict(
                    name=f"M{i}_{axis}",
                    channel=transform(f"M{i}", prop="location", axis=axis),
                )
                for i in range(12)
                for axis in "xyz"
            ],
        )
        seconds = time.perf_counter() - start
        self.assertEqual(inspection["key_count"], 2304)
        self.assertEqual(len(qa["metrics"]), 132)
        self.assertLess(
            max(
                m["maximum"]
                for m in qa["metrics"]
                if m["name"].endswith("mapping_error")
            ),
            1e-6,
        )
        self.assertEqual(len(qa["details"]), 0)
        print(
            "MOTION_SCALE",
            json.dumps(
                dict(
                    channels=36,
                    couplings=12,
                    keys=2304,
                    frames=100,
                    author_seconds=author,
                    inspection_bytes=len(json.dumps(inspection).encode()),
                    qa_seconds=seconds,
                    qa_bytes=len(json.dumps(qa).encode()),
                )
            ),
        )

    def flexible(self, *, layers_enabled: bool = False) -> Any:
        obj = self.tube([-2, -1, 0, 1, 2], caps=True)
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(name=n, head=[0, 0, 0], tail=[0, 2, 0], envelope_distance=5)
                for n in ["A", "B"]
            ],
        )
        self.call(
            "armature.bind",
            object_name="Surface",
            armature_object="Rig",
            preserve_volume=False,
            weights=dict(
                method="explicit",
                vertices=[
                    dict(
                        vertex=i,
                        influences=[
                            dict(bone="A", weight=0.5),
                            dict(bone="B", weight=0.5),
                        ],
                    )
                    for i in range(len(obj.data.vertices))
                ],
            ),
        )
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[
                dict(name="A", rotation=[1, 0, 0]),
                dict(name="B", rotation=[-1, 0, 0]),
            ],
        )
        self.call("deformation.capture_target", object_name="Surface", name="Desired")
        self.call(
            "mesh.transform",
            object_name="Desired",
            selector=dict(mode="all", domain="vertex"),
            scale=[1, 1 / math.cos(1), 1 / math.cos(1)],
            pivot="origin",
        )
        before = self.call(
            "deformation.compare", pairs=[dict(object_name="Surface", target="Desired")]
        )["comparisons"][0]["rms"]
        self.call(
            "shape_keys.edit",
            object_name="Surface",
            keys=[
                dict(
                    name="Support",
                    create=True,
                    value=1,
                    correction=dict(mode="captured_target", target="Desired"),
                )
            ],
        )
        self.call(
            "shape_keys.edit",
            object_name="Surface",
            keys=[dict(name="Support", value=0)],
        )
        self.call("armature.pose", object_name="Rig", reset=True, bones=[])
        if layers_enabled:
            self.call("volume.snapshot", objects=[dict(source="Surface", name="Outer")])
            self.call(
                "mesh.transform",
                object_name="Outer",
                selector=dict(mode="all", domain="vertex"),
                scale=[1.1, 1.1, 1.1],
                pivot="origin",
            )
            self.call(
                "surface_deform.bind",
                driver="Surface",
                driven=["Outer"],
                bind_state="current",
            )
            self.call(
                "layer.capture_reference",
                references=[
                    dict(
                        name="Sliding",
                        source="Outer",
                        target="Surface",
                        sample_count=48,
                    )
                ],
            )
            self.call(
                "curve.create",
                curves=[
                    dict(
                        name="Guide",
                        splines=[
                            dict(
                                type="POLY",
                                points=[
                                    dict(co=[0, -2, 0], radius=0.5),
                                    dict(co=[0.2, 0, 0], radius=1),
                                    dict(co=[0, 2, 0], radius=0.5),
                                ],
                            )
                        ],
                        settings=dict(
                            profile=dict(
                                kind="circle", radius=0.1, resolution=8, caps=True
                            )
                        ),
                        bindings=[
                            dict(
                                spline=0,
                                point=0,
                                target=dict(kind="bone", object="Rig", bone="A"),
                            ),
                            dict(
                                spline=0,
                                point=2,
                                target=dict(kind="bone", object="Rig", bone="B"),
                            ),
                        ],
                    )
                ],
            )
            self.call(
                "volume.snapshot",
                objects=[
                    dict(source="Guide", name="Volume"),
                    dict(source="Guide", name="VolumeRest"),
                ],
            )
            frozen = bpy.data.objects["Volume"]
            self.call(
                "shape_keys.edit",
                object_name="Volume",
                keys=[
                    dict(
                        name="Expand",
                        create=True,
                        correction=dict(
                            mode="sparse",
                            deltas=[
                                dict(
                                    vertex=v.index,
                                    delta=[0.3 * v.co.x, 0, 0.3 * v.co.z],
                                )
                                for v in frozen.data.vertices
                            ],
                        ),
                    )
                ],
            )
        self.coupling(
            "Oppose", transform(bone="A"), transform(bone="B"), mapping=mapping(-1)
        )
        self.coupling(
            "Support",
            transform(bone="A"),
            dict(kind="shape", object_name="Surface", key="Support"),
            mapping=dict(
                kind="remap", input_min=0, input_max=1, output_min=0, output_max=1
            ),
        )
        if layers_enabled:
            self.coupling(
                "Expand",
                transform(bone="A"),
                dict(kind="shape", object_name="Volume", key="Expand"),
                mapping=dict(
                    kind="remap", input_min=0, input_max=1, output_min=0, output_max=1
                ),
            )
        self.clip(
            [
                dict(
                    target=transform(bone="A"),
                    keys=[
                        dict(frame=1, value=0),
                        dict(frame=11, value=1),
                        dict(frame=16, value=1.5),
                        dict(frame=21, value=-0.5),
                    ],
                )
            ]
        )
        return before

    def test_pose_dependent_corrective_rest_partial_target_beyond_reverse(self) -> None:
        before = self.flexible()
        self.call("timeline.configure", frame=11)
        after = self.call(
            "deformation.compare", pairs=[dict(object_name="Surface", target="Desired")]
        )["comparisons"][0]["rms"]
        self.assertGreater(before, 0.1)
        self.assertLess(after, 1e-5)
        result = self.call(
            "motion.sample",
            frames=[1, 6, 11, 16, 21],
            couplings=["Support", "Oppose"],
            objects=["Surface"],
            targets=[dict(object_name="Surface", target="Desired")],
            detail_frames=[1, 6, 11, 16, 21],
        )
        self.assertEqual(
            [
                round(row["values"]["coupling.Support.target_value"], 5)
                for row in result["details"]
            ],
            [0, 0.5, 1, 1, 0],
        )
        self.assertLess(
            self.metric(result, "coupling.Support.mapping_error")["maximum"], 1e-5
        )
        self.assertAlmostEqual(
            result["details"][2]["values"]["mesh.Surface.volume_ratio"], 1, places=5
        )
        print(
            "MOTION_CORRECTIVE",
            json.dumps(dict(before_rms=before, after_rms=after, qa=result)),
        )

    def test_multi_system_volume_layers_attachments_and_corrective(self) -> None:
        self.flexible(layers_enabled=True)
        self.call("timeline.configure", frame=6)
        result = self.call(
            "motion.sample",
            frames=[1, 6, 11, 16, 21],
            armature_object="Rig",
            bones=["A", "B"],
            couplings=["Oppose", "Support", "Expand"],
            objects=["Surface"],
            volumes=[
                dict(object_name="Surface"),
                dict(object_name="Guide"),
                dict(object_name="Volume", reference="VolumeRest"),
            ],
            layers=dict(
                queries=[dict(mode="reference", name="Sliding")], worst_limit=0
            ),
            detail_frames=[1, 11],
        )
        self.assertEqual(result["violation_count"], 0)
        self.assertLess(
            self.metric(result, "volume.Guide.attachment_error")["maximum"], 1e-5
        )
        self.assertGreater(self.metric(result, "volume.Volume.ratio")["maximum"], 1.5)
        self.assertEqual(self.metric(result, "layer.0.valid")["minimum"], 1)
        self.assertEqual(bpy.context.scene.frame_current, 6)
        print("MOTION_SYSTEMS", json.dumps(result))

    def test_constraints_requested_vs_evaluated_and_thresholds(self) -> None:
        self.chain()
        self.coupling(mapping=mapping(2, -2, 2))
        self.clip()
        result = self.qa(
            thresholds=[dict(metric="joint.J1.x.requested_excess", maximum=0.01)],
            violation_limit=1,
        )
        self.assertGreater(
            self.metric(result, "joint.J1.x.constraint_delta")["maximum"], 0.3
        )
        self.assertGreater(result["violation_count"], 1)
        self.assertEqual(len(result["violations"]), 1)
        self.assertLess(
            self.metric(result, "joint.J1.x.limit_violation")["maximum"], 1e-5
        )

    def test_object_parent_local_sources_and_scale_channels(self) -> None:
        self.call(
            "object_set.create",
            objects=[
                dict(kind="empty", key="Parent", name="Parent", location=[3, 0, 0]),
                dict(
                    kind="empty",
                    key="Source",
                    name="Source",
                    parent=dict(key="Parent"),
                    location=[2, 0, 0],
                ),
                dict(kind="empty", key="Target", name="Target"),
            ],
        )
        self.coupling(
            source=transform("Source", prop="location"),
            target=transform("Target", prop="scale"),
            mapping=dict(kind="linear", scale=0.5),
        )
        row = self.call("coupling.inspect")["couplings"][0]
        self.assertTrue(row["valid"], row)
        self.assertLess(row["mapping_error"], 1e-6)
        self.assertAlmostEqual(row["target_value"], 1)

    def test_shape_key_actions_and_driven_conflict_after_detach(self) -> None:
        self.call("object.create_primitive", primitive="cube", name="Cube")
        self.call(
            "shape_keys.edit", object_name="Cube", keys=[dict(name="Key", create=True)]
        )
        shape = dict(kind="shape", object_name="Cube", key="Key")
        self.clip(
            [dict(target=shape, keys=[dict(frame=1, value=0), dict(frame=11, value=1)])]
        )
        self.call("timeline.configure", frame=6)
        self.assertAlmostEqual(
            bpy.data.objects["Cube"].data.shape_keys.key_blocks["Key"].value, 0.5
        )
        self.call("action.assign", name="Clip", detach=True)
        self.call("object.create_primitive", primitive="cube", name="Source")
        self.coupling(
            source=transform("Source", prop="location"),
            target=shape,
            mapping=mapping(1, 0, 1),
        )
        self.error("action.assign", name="Clip")
        self.assertTrue(
            self.call("action.inspect", name="Clip")["channels"][0]["driven"]
        )

    def test_action_failure_keeps_original_assignment_and_other_keys(self) -> None:
        self.chain()
        self.clip()
        old = bpy.data.actions["Clip"]
        old_keys = self.call("action.inspect", name="Clip", key_limit=8)
        self.error(
            "action.edit",
            name="Clip",
            channels=[
                dict(target=transform(bone="J0"), keys=[dict(frame=11, value=0.3)]),
                dict(target=transform(bone="J2"), remove_frames=[99]),
            ],
        )
        self.assertEqual(bpy.data.objects["Rig"].animation_data.action, old)
        self.assertEqual(
            self.call("action.inspect", name="Clip", key_limit=8), old_keys
        )
        self.assertEqual(len(bpy.data.actions), 1)

    def test_budget_failure_and_topology_failure_restore(self) -> None:
        self.chain()
        self.coupling()
        self.clip()
        self.call("timeline.configure", frame=6)
        self.error("motion.sample", range=dict(start=1, end=200), couplings=["Follow"])
        obj = self.grid(2, 2)
        original = motion.rig.snapshot
        count = 0

        def changed(*args: Any, **kwargs: Any) -> Any:
            nonlocal count
            count += 1
            row = original(*args, **kwargs)
            return (row[0], row[1][1:], row[2], row[3]) if count > 1 else row

        with patch.object(motion.rig, "snapshot", changed):
            self.error("motion.sample", frames=[1, 11], objects=[obj.name])
        self.assertEqual(bpy.context.scene.frame_current, 6)

    def test_object_rename_native_pointer_persistence_and_invalid_source(self) -> None:
        self.chain()
        self.coupling()
        self.clip()
        bpy.data.objects["Rig"].name = "Renamed"
        self.assertTrue(self.call("coupling.inspect")["couplings"][0]["valid"])
        self.assertEqual(
            self.call("action.inspect", name="Clip")["channels"][0]["target"][
                "object_name"
            ],
            "Renamed",
        )
        bpy.data.objects["Renamed"].pose.bones["J0"].name = "RenamedBone"
        self.assertFalse(self.call("coupling.inspect")["couplings"][0]["valid"])

    def test_linked_shared_and_unsupported_rotation_guards(self) -> None:
        import tempfile

        self.call("object.create_primitive", primitive="cube", name="Local")
        local = bpy.data.objects["Local"]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "library.blend")
            local.name = "LibrarySource"
            bpy.data.libraries.write(path, {local})
            local.name = "Local"
            with bpy.data.libraries.load(path, link=True) as (source, destination):
                destination.objects = source.objects
            linked = destination.objects[0]
            bpy.context.collection.objects.link(linked)
            self.error(
                "action.edit",
                name="Protected",
                create=True,
                channels=[
                    dict(
                        target=transform(linked.name, prop="location"),
                        keys=[dict(frame=1, value=0)],
                    )
                ],
            )
        local.rotation_mode = "QUATERNION"
        self.error(
            "action.edit",
            name="Unsupported",
            create=True,
            channels=[dict(target=transform("Local"), keys=[dict(frame=1, value=0)])],
        )
        self.assertNotIn("Protected", bpy.data.actions)
        self.assertNotIn("Unsupported", bpy.data.actions)

    def test_missing_source_can_be_removed_without_touching_new_same_name(self) -> None:
        self.call(
            "object_set.create",
            objects=[dict(key=n, name=n, kind="empty") for n in ["Source", "Target"]],
        )
        self.coupling(
            source=transform("Source", prop="location"),
            target=transform("Target", prop="location"),
        )
        bpy.data.objects.remove(bpy.data.objects["Source"], do_unlink=True)
        self.assertFalse(self.call("coupling.inspect")["couplings"][0]["valid"])
        self.call(
            "object_set.create", objects=[dict(key="New", name="Source", kind="empty")]
        )
        self.call("coupling.remove", names=["Follow"])
        self.assertIsNone(bpy.data.objects["Target"].animation_data)
        self.assertEqual(self.call("coupling.inspect")["couplings"], [])

    def test_control_batch_budget_and_property_cycle(self) -> None:
        self.call(
            "object_set.create",
            objects=[dict(key=n, name=n, kind="empty") for n in ["S", "T"]],
        )
        self.call(
            "motion.set_properties",
            properties=[
                dict(object_name="S", name=f"C{i}", value=0) for i in range(63)
            ],
        )
        self.error(
            "motion.set_properties",
            properties=[
                dict(object_name="S", name="ExtraA", value=0),
                dict(object_name="S", name="ExtraB", value=0),
            ],
        )
        self.assertNotIn("tyvrana_control_ExtraA", bpy.data.objects["S"])
        self.call(
            "motion.set_properties",
            properties=[dict(object_name="T", name="Scalar", value=0)],
        )
        source = dict(kind="property", object_name="S", property="C0")
        target = dict(kind="property", object_name="T", property="Scalar")
        self.coupling(source=source, target=target, mapping=mapping(1, 0, 1))
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="Cycle", source=target, target=source, mapping=mapping(1, 0, 1)
                )
            ],
        )

    def test_native_timeline_bounds_and_preview_preserve(self) -> None:
        before = self.call("timeline.inspect")
        self.error("timeline.configure", frame_start=-100)
        self.assertEqual(self.call("timeline.inspect"), before)
        result = self.call(
            "timeline.configure",
            preview_start=5,
            preview_end=9,
            use_preview_range=True,
            frame=-2,
        )
        self.assertEqual(result["frame"], -2)
        self.assertEqual(result["preview_start"], 5)
        self.assertEqual(result["frame_start"], before["frame_start"])

    def test_ancestor_driver_cycle_rejected_before_native_creation(self) -> None:
        self.call(
            "object_set.create",
            objects=[
                dict(key="Parent", name="Parent", kind="empty"),
                dict(
                    key="Source", name="Source", kind="empty", parent=dict(key="Parent")
                ),
                dict(key="Target", name="Target", kind="empty"),
            ],
        )
        curve = bpy.data.objects["Parent"].driver_add("location", 0)
        variable = curve.driver.variables.new()
        variable.name = "x"
        variable.type = "TRANSFORMS"
        variable.targets[0].id = bpy.data.objects["Target"]
        variable.targets[0].transform_type = "LOC_X"
        curve.driver.expression = "x"
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="Cycle",
                    source=transform("Source", prop="location"),
                    target=transform("Target", prop="location"),
                    mapping=dict(kind="linear"),
                )
            ],
        )
        target = bpy.data.objects["Target"]
        self.assertTrue(
            target.animation_data is None or not target.animation_data.drivers
        )

    def test_same_object_shape_scalar_drives_transform_with_geometry_qa(self) -> None:
        self.call("object.create_primitive", primitive="cube", name="Cube")
        self.call(
            "shape_keys.edit",
            object_name="Cube",
            keys=[dict(name="Scalar", create=True, value=0.5)],
        )
        self.coupling(
            source=dict(kind="shape", object_name="Cube", key="Scalar"),
            target=transform("Cube", prop="scale"),
            mapping=dict(kind="linear", scale=0.1, offset=1),
        )
        value = self.call("volume.inspect", objects=[dict(object_name="Cube")])[
            "volumes"
        ][0]
        self.assertAlmostEqual(value["evaluated"]["volume"], 8.4, places=5)
        self.assertTrue(self.call("coupling.inspect")["couplings"][0]["valid"])

    def test_removed_coupling_restores_plain_pose_workflow(self) -> None:
        self.chain()
        self.coupling()
        self.call("coupling.remove", names=["Follow"])
        self.assertIsNone(bpy.data.objects["Rig"].animation_data)
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="J0", rotation=[0.5, 0, 0])],
        )


if __name__ == "__main__":
    suite = unittest.TestSuite(
        MotionTests(name) for name in MotionTests.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("MOTION_NATIVE_PASSED")
