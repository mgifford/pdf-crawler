"""Tests for the site-filter feature in scripts/pdf_analyser.py"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from manifest import build_entry, mark_analysed, pending_entries
from pdf_analyser import main as analyser_main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pending_entry(url: str, site: str, tmp_path: Path) -> dict:
    """Return a pending manifest entry backed by a real (tiny) file."""
    filename = url.split("/")[-1]
    p = tmp_path / site / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 fake")
    return build_entry(url, p, site)


# ---------------------------------------------------------------------------
# site_filter in main()
# ---------------------------------------------------------------------------

def test_site_filter_skips_other_sites(tmp_path):
    """Entries belonging to a different site must stay 'pending'."""
    entry_a = _pending_entry("https://a.com/doc.pdf", "a.com", tmp_path)
    entry_b = _pending_entry("https://b.com/doc.pdf", "b.com", tmp_path)

    manifest_path = tmp_path / "manifest.yaml"
    from manifest import save_manifest, load_manifest
    save_manifest([entry_a, entry_b], manifest_path)

    # Run the analyser scoped to a.com only
    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        site_filter="a.com",
    )

    entries = load_manifest(manifest_path)
    by_url = {e["url"]: e for e in entries}

    # a.com entry should have been processed (status != pending)
    assert by_url["https://a.com/doc.pdf"]["status"] != "pending"
    # b.com entry must remain pending because it was out of scope
    assert by_url["https://b.com/doc.pdf"]["status"] == "pending"


def test_no_site_filter_processes_all(tmp_path):
    """Without a site filter every pending entry is processed."""
    entry_a = _pending_entry("https://a.com/x.pdf", "a.com", tmp_path)
    entry_b = _pending_entry("https://b.com/x.pdf", "b.com", tmp_path)

    manifest_path = tmp_path / "manifest.yaml"
    from manifest import save_manifest, load_manifest
    save_manifest([entry_a, entry_b], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        site_filter=None,
    )

    entries = load_manifest(manifest_path)
    for e in entries:
        assert e["status"] != "pending", f"{e['url']} is still pending"


def test_site_filter_no_matching_entries(tmp_path, capsys):
    """When the filter matches nothing, the analyser exits early gracefully."""
    entry = _pending_entry("https://a.com/doc.pdf", "a.com", tmp_path)

    manifest_path = tmp_path / "manifest.yaml"
    from manifest import save_manifest, load_manifest
    save_manifest([entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        site_filter="b.com",  # no entries for this site
    )

    out = capsys.readouterr().out
    assert "nothing to do" in out.lower()

    # Original entry must remain untouched
    entries = load_manifest(manifest_path)
    assert entries[0]["status"] == "pending"
