"""Read staged format identity without importing unactivated application code."""

import ast
from pathlib import Path

from .deployment import update_directory


def staged_format(extension: Path, version: tuple[int, ...]) -> str | None:
    candidate = update_directory(extension) / "candidate" / "attestation.py"
    if not candidate.is_file():
        return None
    try:
        tree = ast.parse(candidate.read_text())
    except (OSError, SyntaxError, UnicodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "FORMAT"
            for target in node.targets
        ):
            continue
        # Accept only the canonical expression, with a literal digest suffix.
        expected = ast.parse(
            '"blender-rna-" + ".".join(map(str, bpy.app.version)) + ""',
            mode="eval",
        ).body
        value = node.value
        if not isinstance(value, ast.BinOp) or not isinstance(expected, ast.BinOp):
            return None
        if ast.dump(value.left) != ast.dump(expected.left):
            return None
        if not isinstance(value.op, ast.Add) or not isinstance(
            value.right, ast.Constant
        ):
            return None
        suffix = value.right.value
        if (
            not isinstance(suffix, str)
            or len(suffix) != 17
            or suffix[0] != "-"
            or any(c not in "0123456789abcdef" for c in suffix[1:])
        ):
            return None
        return "blender-rna-" + ".".join(map(str, version)) + suffix
    return None
