"""Tests for scripts/manifest.py"""

import hashlib
import os
import tempfile
from pathlib import Path

import pytest
import yaml

# Allow importing from scripts/
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from manifest import (
    _md5,
    build_entry,
    load_manifest,
    mark_analysed,
    mark_error,
    needs_analysis,
    pending_entries,
    save_manifest,
    update_entry_from_file,
    upsert_entry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_pdf(tmp_path):
    """A small fake PDF file for hashing tests."""
    p = tmp_path / "test.pdf"
    p.write_bytes(b"%PDF-1.4 fake content")
    return p


@pytest.fixture()
def manifest_file(tmp_path):
    return tmp_path / "manifest.yaml"


# ---------------------------------------------------------------------------
# _md5
# ---------------------------------------------------------------------------

def test_md5_consistent(tmp_pdf):
    h1 = _md5(tmp_pdf)
    h2 = _md5(tmp_pdf)
    assert h1 == h2


def test_md5_changes_with_content(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"hello")
    h1 = _md5(p)
    p.write_bytes(b"world")
    h2 = _md5(p)
    assert h1 != h2


def test_md5_format(tmp_pdf):
    h = _md5(tmp_pdf)
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# build_entry
# ---------------------------------------------------------------------------

def test_build_entry_structure(tmp_pdf):
    entry = build_entry("https://example.com/test.pdf", tmp_pdf, "example.com")
    assert entry["url"] == "https://example.com/test.pdf"
    assert entry["filename"] == "test.pdf"
    assert entry["site"] == "example.com"
    assert entry["status"] == "pending"
    assert entry["report"] is None
    assert entry["errors"] == []
    assert len(entry["md5"]) == 32


def test_build_entry_with_notes(tmp_pdf):
    entry = build_entry("https://example.com/test.pdf", tmp_pdf, "example.com", notes="Test org scan")
    assert entry["notes"] == "Test org scan"


def test_build_entry_without_notes_has_no_notes_key(tmp_pdf):
    entry = build_entry("https://example.com/test.pdf", tmp_pdf, "example.com")
    assert "notes" not in entry


# ---------------------------------------------------------------------------
# save_manifest / load_manifest
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(manifest_file, tmp_pdf):
    entry = build_entry("https://example.com/a.pdf", tmp_pdf, "example.com")
    save_manifest([entry], manifest_file)
    loaded = load_manifest(manifest_file)
    assert len(loaded) == 1
    assert loaded[0]["url"] == entry["url"]
    assert loaded[0]["md5"] == entry["md5"]


def test_load_missing_manifest_returns_empty_list(tmp_path):
    result = load_manifest(tmp_path / "nonexistent.yaml")
    assert result == []


def test_save_creates_parent_dirs(tmp_path):
    deep_path = tmp_path / "a" / "b" / "c" / "manifest.yaml"
    save_manifest([], deep_path)
    assert deep_path.exists()


# ---------------------------------------------------------------------------
# upsert_entry
# ---------------------------------------------------------------------------

def test_upsert_adds_new_entry(tmp_pdf, manifest_file):
    entries = []
    entries, needs_scan = upsert_entry(entries, "https://example.com/new.pdf", tmp_pdf, "example.com")
    assert len(entries) == 1
    assert needs_scan is True


def test_upsert_skip_unchanged(tmp_pdf, manifest_file):
    entries = []
    entries, _ = upsert_entry(entries, "https://example.com/doc.pdf", tmp_pdf, "example.com")
    # Mark it as analysed
    entries = mark_analysed(entries, "https://example.com/doc.pdf", {"Accessible": True})
    # Upsert again with same file (same MD5)
    entries, needs_scan = upsert_entry(entries, "https://example.com/doc.pdf", tmp_pdf, "example.com")
    assert needs_scan is False


def test_upsert_rescan_on_changed_content(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"original content")
    url = "https://example.com/doc.pdf"
    entries = []
    entries, _ = upsert_entry(entries, url, p, "example.com")
    entries = mark_analysed(entries, url, {"Accessible": True})
    # Simulate file update
    p.write_bytes(b"updated content")
    entries, needs_scan = upsert_entry(entries, url, p, "example.com")
    assert needs_scan is True
    assert entries[0]["status"] == "pending"


def test_upsert_stores_notes_on_new_entry(tmp_pdf):
    entries = []
    entries, _ = upsert_entry(
        entries, "https://example.com/doc.pdf", tmp_pdf, "example.com",
        notes="Govt accessibility audit"
    )
    assert entries[0]["notes"] == "Govt accessibility audit"


def test_upsert_updates_notes_on_existing_unchanged_entry(tmp_pdf):
    entries = []
    entries, _ = upsert_entry(entries, "https://example.com/doc.pdf", tmp_pdf, "example.com")
    entries = mark_analysed(entries, "https://example.com/doc.pdf", {"Accessible": True})
    # Re-upsert with same content but new notes
    entries, _ = upsert_entry(
        entries, "https://example.com/doc.pdf", tmp_pdf, "example.com",
        notes="Updated notes"
    )
    assert entries[0]["notes"] == "Updated notes"


# ---------------------------------------------------------------------------
# mark_analysed / mark_error
# ---------------------------------------------------------------------------

def test_mark_analysed(tmp_pdf):
    entry = build_entry("https://example.com/a.pdf", tmp_pdf, "example.com")
    entries = [entry]
    report = {"Accessible": True, "TaggedTest": "Pass"}
    entries = mark_analysed(entries, "https://example.com/a.pdf", report, ["some log"])
    assert entries[0]["status"] == "analysed"
    assert entries[0]["report"]["Accessible"] is True
    assert entries[0]["errors"] == ["some log"]


def test_mark_error(tmp_pdf):
    entry = build_entry("https://example.com/b.pdf", tmp_pdf, "example.com")
    entries = [entry]
    entries = mark_error(entries, "https://example.com/b.pdf", ["PdfError: broken"])
    assert entries[0]["status"] == "error"
    assert "PdfError: broken" in entries[0]["errors"]


# ---------------------------------------------------------------------------
# pending_entries
# ---------------------------------------------------------------------------

def test_pending_entries_filter(tmp_pdf):
    e1 = build_entry("https://example.com/p.pdf", tmp_pdf, "example.com")
    e2 = build_entry("https://example.com/q.pdf", tmp_pdf, "example.com")
    e2["status"] = "analysed"
    result = pending_entries([e1, e2])
    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/p.pdf"


# ---------------------------------------------------------------------------
# needs_analysis
# ---------------------------------------------------------------------------

def test_needs_analysis_pending(tmp_pdf):
    entry = build_entry("https://example.com/x.pdf", tmp_pdf, "example.com")
    assert needs_analysis(entry, tmp_pdf) is True


def test_needs_analysis_analysed_same_md5(tmp_pdf):
    entry = build_entry("https://example.com/x.pdf", tmp_pdf, "example.com")
    entry["status"] = "analysed"
    assert needs_analysis(entry, tmp_pdf) is False


def test_needs_analysis_analysed_changed_md5(tmp_path):
    p = tmp_path / "f.pdf"
    p.write_bytes(b"v1")
    entry = build_entry("https://example.com/x.pdf", p, "example.com")
    entry["status"] = "analysed"
    p.write_bytes(b"v2")
    assert needs_analysis(entry, p) is True


# ---------------------------------------------------------------------------
# upsert_entry – notes on changed content (line 138)
# ---------------------------------------------------------------------------

def test_upsert_updates_notes_when_content_changes(tmp_path):
    """When a file's content changes, notes should also be updated."""
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"original content")
    url = "https://example.com/doc.pdf"
    entries = []
    entries, _ = upsert_entry(entries, url, p, "example.com")
    entries = mark_analysed(entries, url, {"Accessible": True})
    # Change file content
    p.write_bytes(b"updated content")
    entries, needs_scan = upsert_entry(entries, url, p, "example.com", notes="New notes")
    assert needs_scan is True
    assert entries[0].get("notes") == "New notes"


# ---------------------------------------------------------------------------
# mark_analysed / mark_error – unknown URL returns unchanged list
# ---------------------------------------------------------------------------

def test_mark_analysed_missing_url_returns_unchanged(tmp_pdf):
    """mark_analysed must return the unchanged list when the URL is not found."""
    entry = build_entry("https://example.com/a.pdf", tmp_pdf, "example.com")
    entries = [entry]
    result = mark_analysed(entries, "https://example.com/nonexistent.pdf", {"Accessible": True})
    # The list must be returned unchanged
    assert result is entries
    assert entries[0]["status"] == "pending"


def test_mark_error_missing_url_returns_unchanged(tmp_pdf):
    """mark_error must return the unchanged list when the URL is not found."""
    entry = build_entry("https://example.com/b.pdf", tmp_pdf, "example.com")
    entries = [entry]
    result = mark_error(entries, "https://example.com/nonexistent.pdf", ["some error"])
    assert result is entries
    assert entries[0]["status"] == "pending"
