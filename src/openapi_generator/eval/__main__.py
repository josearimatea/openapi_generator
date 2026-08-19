"""
Command-line entry point for the comparison.

    python -m openapi_generator.eval <generated.yaml> <reference.yaml> [--json]
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from openapi_generator.eval.comparison import compare, format_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m openapi_generator.eval",
        description="Compare a generated OpenAPI document against a reference one.",
    )
    parser.add_argument("generated", type=Path, help="generated OpenAPI YAML")
    parser.add_argument("reference", type=Path, help="reference OpenAPI YAML")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    args = parser.parse_args(argv)

    for path in (args.generated, args.reference):
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2

    result = compare(
        yaml.safe_load(args.generated.read_text(encoding="utf-8")) or {},
        yaml.safe_load(args.reference.read_text(encoding="utf-8")) or {},
    )
    print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False)
          if args.json else format_report(result))

    # Non-zero when the contract itself is incomplete, so CI can gate on it.
    contract = result.contract
    return 1 if (contract.absent or contract.differing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
