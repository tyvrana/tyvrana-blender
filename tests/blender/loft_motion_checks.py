"""Native section loft ownership and multi-source piecewise mechanics."""

import importlib
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

loft = importlib.import_module(PACKAGE + "loft")


def component(name: str = "Beam", offset: float = 0) -> dict[str, Any]:
    return dict(
        name=name,
        sides=16,
        subdivisions=4,
        sections=[
            dict(center=[offset, 0, 0], radii=[0.2, 0.1, 0.15, 0.1]),
            dict(center=[offset, 0.8, 0.2], radii=[0.4, 0.3, 0.2, 0.1], twist=0.2),
            dict(center=[offset, 1.6, 0], radii=[0.1, 0.12, 0.08, 0.05]),
        ],
    )


class LoftMotionTests(TopologyTests):
    def test_loft_edit_ownership_topology_and_rollback(self) -> None:
        a, b = component(), component("Other", 2)
        made = self.call("loft.create", components=[a, b])
        obj = bpy.data.objects["Beam"]
        identity = obj.as_pointer()
        mesh = obj.data.as_pointer()
        obj.vertex_groups.new(name="Attachment").add([0, 1], 1, "REPLACE")
        obj.modifiers.new("Subdivision", "SUBSURF").levels = 1
        a["sections"][1]["radii"] = [0.5, 0.3, 0.2, 0.1]
        self.call("loft.configure", components=[a])
        self.assertEqual((obj.as_pointer(), obj.data.as_pointer()), (identity, mesh))
        inspected = self.call("loft.inspect", names=["Beam"], include_sections=True)[
            "components"
        ][0]
        self.assertEqual(
            inspected["component_id"], made["components"][0]["component_id"]
        )
        self.assertTrue(inspected["valid"])
        self.assertEqual(len(obj.vertex_groups), 1)
        qa = self.call("geometry.inspect", objects=[dict(object_name="Beam")])[
            "samples"
        ][0]["objects"][0]
        self.assertTrue(qa["closed_consistent"])
        self.assertGreater(qa["signed_volume"], 0)
        before = loft.signature(obj.data)
        oldraw = str(obj[loft.KEY])
        a["sections"][1]["radii"][0] = 0.7
        with patch.object(loft, "summary", side_effect=RuntimeError("injected")):
            self.error("loft.configure", components=[a, b])
        self.assertEqual(loft.signature(obj.data), before)
        self.assertEqual(obj[loft.KEY], oldraw)
        bpy.data.objects["Other"].data.vertices[0].co.x += 1
        self.error("loft.configure", components=[a, b])
        self.assertEqual(loft.signature(obj.data), before)
        self.assertFalse(
            self.call("loft.inspect", names=["Other"])["components"][0]["valid"]
        )
        broken = component("Rollback")
        broken["sections"][1]["center"] = [0, 0, 0]
        self.error("loft.create", components=[component("First"), broken])
        self.assertNotIn("First", bpy.data.objects)

    def test_multi_source_piecewise_drivers_and_cycles(self) -> None:
        for name in ["Inputs", "Driven"]:
            self.call("object.create_primitive", primitive="cube", name=name)
        self.call(
            "motion.set_properties",
            properties=[
                dict(object_name="Inputs", name=n, value=v, minimum=-10, maximum=10)
                for n, v in [("a", 0.4), ("b", 0.6)]
            ],
        )

        def source(n: str) -> dict[str, Any]:
            return dict(kind="property", object_name="Inputs", property=n)

        mapping = dict(
            kind="piecewise",
            knots=[
                dict(input=0, output=0),
                dict(input=1, output=2),
                dict(input=2, output=3),
            ],
        )
        coupling = dict(
            name="Response",
            source=source("a"),
            additional_sources=[dict(channel=source("b"), weight=1)],
            target=dict(
                kind="transform", object_name="Driven", property="location", axis="x"
            ),
            mapping=mapping,
        )
        self.call("coupling.configure", couplings=[coupling])
        row = self.call("coupling.inspect", names=["Response"])["couplings"][0]
        self.assertTrue(row["valid"], row)
        self.assertAlmostEqual(row["evaluated_target_value"], 2, places=5)
        self.assertLess(row["mapping_error"], 1e-6)
        self.call(
            "motion.set_properties",
            properties=[
                dict(object_name="Inputs", name="a", value=1, minimum=-10, maximum=10)
            ],
        )
        row = self.call("coupling.inspect", names=["Response"])["couplings"][0]
        self.assertAlmostEqual(row["evaluated_target_value"], 2.6, places=5)
        driver = bpy.data.objects["Driven"].animation_data.drivers[0].driver
        self.assertTrue(driver.is_simple_expression)
        target = dict(
            kind="transform", object_name="Driven", property="location", axis="x"
        )
        self.error(
            "coupling.configure",
            couplings=[
                dict(
                    name="Cycle",
                    source=target,
                    target=source("a"),
                    mapping=dict(kind="linear", clamp=dict(minimum=-10, maximum=10)),
                )
            ],
        )
        bpy.data.objects["Inputs"].name = "Renamed"
        self.assertTrue(
            self.call("coupling.inspect", names=["Response"])["couplings"][0]["valid"]
        )
        self.call("coupling.remove", names=["Response"])
        self.assertIsNone(bpy.data.objects["Driven"].animation_data)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [
                LoftMotionTests("test_loft_edit_ownership_topology_and_rollback"),
                LoftMotionTests("test_multi_source_piecewise_drivers_and_cycles"),
            ]
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("LOFT_MOTION_NATIVE_PASSED")
