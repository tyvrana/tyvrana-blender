"""Stage a built extension without changing the running Blender's loaded files."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tyvrana_blender.deployment import recover, stage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--extension-dir", required=True, type=Path)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    if args.recover:
        print(json.dumps({"recovered": recover(args.extension_dir.resolve())}))
    elif args.archive is not None:
        print(json.dumps(stage(args.archive, args.extension_dir), indent=2))
    else:
        parser.error("Provide an archive or --recover")


if __name__ == "__main__":
    main()
