"""Single-PDF validation CLI for raw and structured JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_analyser import check_file
from structured_report import build_json_report


def _print_json(data: dict, pretty: bool = False) -> None:
    if pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a single PDF and output either the raw scanner JSON "
            "or a structured accessibility report JSON."
        )
    )
    parser.add_argument("pdf", help="Path to the PDF file to validate")
    parser.add_argument(
        "--mode",
        choices=["raw", "structured"],
        default="structured",
        help="Output mode: raw scanner JSON or structured report JSON",
    )
    parser.add_argument(
        "--compatible",
        action="store_true",
        help="Structured mode only: include compatible/manual/unsupported rules",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )

    args = parser.parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists() or not pdf_path.is_file():
        parser.error(f"File not found: {pdf_path}")

    result = check_file(str(pdf_path))
    result.setdefault("File", pdf_path.name)

    if args.mode == "raw":
        _print_json(result, pretty=args.pretty)
        return 0

    structured = build_json_report(result, compatible=args.compatible, debug=False)
    _print_json(structured, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
