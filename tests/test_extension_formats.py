"""Staged format inspection never executes candidate code."""

from pathlib import Path

from tyvrana_blender.extension_formats import staged_format


def test_staged_format_requires_canonical_literal(tmp_path: Path) -> None:
    extension = tmp_path / "tyvrana_blender"
    candidate = tmp_path / ".tyvrana_blender-update/candidate"
    candidate.mkdir(parents=True)
    target = candidate / "attestation.py"
    assert staged_format(extension, (5, 2, 1)) is None
    for suffix in ["7fd96589ef405168", "46e0c05d01837c2a"]:
        target.write_text(
            'raise RuntimeError("never execute")\n'
            'FORMAT = "blender-rna-" + ".".join(map(str, bpy.app.version)) + "-'
            + suffix
            + '"\n'
        )
        assert staged_format(extension, (5, 2, 1)) == "blender-rna-5.2.1-" + suffix
    for value in [
        '"invented"',
        "get_format()",
        '"blender-rna-" + __import__("os").getcwd() + "-7fd96589ef405168"',
    ]:
        target.write_text("FORMAT = " + value)
        assert staged_format(extension, (5, 2, 1)) is None
    target.write_text("broken syntax !")
    assert staged_format(extension, (5, 2, 1)) is None
