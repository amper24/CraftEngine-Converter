"""Command-line interface: scan / analyze / convert / validate / report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, driver, validator
from .util import Log


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="converter",
        description="Universal Minecraft mod -> CraftEngine converter",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="detect mod metadata")
    scan_p.add_argument("mod", help="path to mod JAR or directory")

    analyze_p = sub.add_parser("analyze", help="build IR for a mod")
    analyze_p.add_argument("mod", help="path to mod JAR or directory")
    analyze_p.add_argument("--minecraft", default="1.21.4", help="target Minecraft version")
    analyze_p.add_argument(
        "--source",
        default="auto",
        choices=("auto", "mod", "itemsadder"),
        help="input format: a mod jar/dir, or an ItemsAdder contents/ pack",
    )

    convert_p = sub.add_parser("convert", help="convert a mod to CraftEngine")
    convert_p.add_argument("mod", help="path to mod JAR or directory")
    convert_p.add_argument("--target", default="craftengine:26.8")
    convert_p.add_argument("--minecraft", default="1.21.4")
    convert_p.add_argument("--output", required=True, help="output directory")
    convert_p.add_argument(
        "--source",
        default="auto",
        choices=("auto", "mod", "itemsadder"),
        help="input format: a mod jar/dir, or an ItemsAdder contents/ pack",
    )

    validate_p = sub.add_parser("validate", help="validate a generated output")
    validate_p.add_argument("output", help="output directory")

    report_p = sub.add_parser("report", help="generate a report for an output")
    report_p.add_argument("output", help="output directory")

    gui_p = sub.add_parser("gui", help="launch the graphical interface")

    for p in (scan_p, analyze_p, convert_p, validate_p, report_p):
        p.add_argument("--verbose", action="store_true", help="verbose logging")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "scan":
            _print_json(driver.scan(args.mod, verbose=args.verbose))
        elif args.command == "analyze":
            _print_json(driver.analyze(args.mod, args.minecraft, verbose=args.verbose, source=args.source))
        elif args.command == "convert":
            result = driver.convert(
                args.mod,
                args.output,
                target=args.target,
                minecraft_version=args.minecraft,
                verbose=args.verbose,
                source=args.source,
            )
            _print_json(result)
        elif args.command == "validate":
            log = Log(args.verbose)
            result = validator.validate_output(Path(args.output), log)
            _print_json(result.to_dict())
            return 0 if result.valid else 1
        elif args.command == "report":
            log = Log(args.verbose)
            _print_json(validator.generate_report(Path(args.output), log))
        elif args.command == "gui":
            from .gui import run_gui

            return run_gui()
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())