"""Engine comparison report tests."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_report import main as generate_main
from manifest import save_manifest


def test_generate_report_includes_engine_comparison(tmp_path):
    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    entries = [
        {
            "url": "https://example.com/a.pdf",
            "md5": "a" * 32,
            "file_hash": "b" * 64,
            "filename": "a.pdf",
            "site": "example.com",
            "crawled_at": "2026-01-01T00:00:00+00:00",
            "status": "analysed",
            "report": {"Accessible": True},
            "errors": [],
            "analyses": {
                "original": {
                    "status": "analysed",
                    "report": {
                        "Accessible": True,
                        "TaggedTest": "Pass",
                        "TitleTest": "Pass",
                        "LanguageTest": "Pass",
                        "BookmarksTest": "Pass",
                    },
                    "errors": [],
                    "analysed_at": "2026-01-01T00:00:00+00:00",
                },
                "bloom": {
                    "status": "analysed",
                    "report": {
                        "Accessible": False,
                        "TaggedTest": "Fail",
                        "TitleTest": "Pass",
                        "LanguageTest": "Pass",
                        "BookmarksTest": "Pass",
                    },
                    "errors": [],
                    "analysed_at": "2026-01-01T00:01:00+00:00",
                },
            },
        }
    ]

    save_manifest(entries, manifest_path)
    generate_main(manifest_path=str(manifest_path), report_dir=str(report_dir))

    report_json = (report_dir / "report.json").read_text(encoding="utf-8")
    assert '"comparison"' in report_json
    assert '"comparable_files": 1' in report_json
    assert '"accessible_disagreements": 1' in report_json
    # Flattened per-engine entries should include both engines.
    assert '"engine": "bloom"' in report_json
    assert '"engine": "original"' in report_json


def test_generate_report_can_filter_to_single_engine(tmp_path):
    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    entries = [
        {
            "url": "https://example.com/a.pdf",
            "md5": "a" * 32,
            "file_hash": "b" * 64,
            "filename": "a.pdf",
            "site": "example.com",
            "crawled_at": "2026-01-01T00:00:00+00:00",
            "status": "analysed",
            "report": {"Accessible": True},
            "errors": [],
            "analyses": {
                "original": {
                    "status": "analysed",
                    "report": {
                        "Accessible": True,
                        "TaggedTest": "Pass",
                        "TitleTest": "Pass",
                        "LanguageTest": "Pass",
                        "BookmarksTest": "Pass",
                    },
                    "errors": [],
                    "analysed_at": "2026-01-01T00:00:00+00:00",
                },
                "bloom": {
                    "status": "analysed",
                    "report": {
                        "Accessible": False,
                        "TaggedTest": "Fail",
                        "TitleTest": "Pass",
                        "LanguageTest": "Pass",
                        "BookmarksTest": "Pass",
                    },
                    "errors": [],
                    "analysed_at": "2026-01-01T00:01:00+00:00",
                },
            },
        }
    ]

    save_manifest(entries, manifest_path)
    generate_main(
        manifest_path=str(manifest_path),
        report_dir=str(report_dir),
        engine_filter="bloom",
    )

    report_json = (report_dir / "report.json").read_text(encoding="utf-8")
    assert '"engine": "bloom"' in report_json
    assert '"engine": "original"' not in report_json
