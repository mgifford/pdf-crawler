"""Tests for the PDF analyser: site-filter, non-PDF skip, and size-limit features."""

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


# ---------------------------------------------------------------------------
# Non-PDF file skip
# ---------------------------------------------------------------------------

def _pending_entry_ext(url: str, site: str, ext: str, tmp_path: Path) -> dict:
    """Return a pending manifest entry backed by a tiny file with the given extension."""
    filename = url.split("/")[-1]
    p = tmp_path / site / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"not a pdf")
    return build_entry(url, p, site)


def test_non_pdf_file_is_skipped(tmp_path, capsys):
    """Non-PDF files (xlsx, docx, etc.) must be marked as error with a clear message."""
    from manifest import save_manifest, load_manifest

    for ext, url in [
        (".xlsx", "https://a.com/table.xlsx"),
        (".docx", "https://a.com/report.docx"),
        (".pptx", "https://a.com/slides.pptx"),
    ]:
        entry = _pending_entry_ext(url, "a.com", ext, tmp_path)
        manifest_path = tmp_path / f"manifest_{ext.lstrip('.')}.yaml"
        save_manifest([entry], manifest_path)

        analyser_main(
            manifest_path=str(manifest_path),
            crawled_dir=str(tmp_path),
            keep_files=True,
        )

        entries = load_manifest(manifest_path)
        assert entries[0]["status"] == "error", (
            f"{ext} entry should be marked as error, got {entries[0]['status']!r}"
        )
        assert any("not a pdf" in str(e).lower() for e in entries[0]["errors"]), (
            f"Error message for {ext} should mention 'not a PDF', got {entries[0]['errors']!r}"
        )

    out = capsys.readouterr().out
    assert "SKIP" in out, "Output should contain SKIP for non-PDF files"


def test_non_pdf_file_is_deleted(tmp_path):
    """Non-PDF files should be deleted from disk (unless --keep-files is set)."""
    from manifest import save_manifest, load_manifest

    url = "https://a.com/data.xlsx"
    entry = _pending_entry_ext(url, "a.com", ".xlsx", tmp_path)
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([entry], manifest_path)

    file_path = tmp_path / "a.com" / "data.xlsx"
    assert file_path.exists()

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=False,
    )

    assert not file_path.exists(), "Non-PDF file should be deleted after processing"


# ---------------------------------------------------------------------------
# File-size limit
# ---------------------------------------------------------------------------

def test_oversized_file_is_skipped(tmp_path, capsys):
    """Files exceeding max_file_size_mb must be skipped with an error status."""
    from manifest import save_manifest, load_manifest

    url = "https://a.com/huge.pdf"
    p = tmp_path / "a.com" / "huge.pdf"
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write 2 MB of data so we can test a 1 MB limit.
    p.write_bytes(b"x" * (2 * 1024 * 1024))

    entry = build_entry(url, p, "a.com")
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_file_size_mb=1.0,  # 1 MB limit – the 2 MB file should be skipped.
    )

    entries = load_manifest(manifest_path)
    assert entries[0]["status"] == "error"
    assert any("too large" in str(e).lower() for e in entries[0]["errors"])

    out = capsys.readouterr().out
    assert "SKIP" in out


def test_file_within_size_limit_is_processed(tmp_path):
    """Files smaller than max_file_size_mb must still be processed normally."""
    from manifest import save_manifest, load_manifest

    url = "https://a.com/small.pdf"
    p = tmp_path / "a.com" / "small.pdf"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 tiny fake pdf")

    entry = build_entry(url, p, "a.com")
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_file_size_mb=200.0,  # 200 MB limit – tiny file should pass through.
    )

    entries = load_manifest(manifest_path)
    # The file is not a valid PDF, so it will be an error – but it should NOT
    # be skipped due to size.  The status must be something other than "pending".
    assert entries[0]["status"] != "pending", "Small file should have been processed"


# ---------------------------------------------------------------------------
# File-not-found diagnostics
# ---------------------------------------------------------------------------

def _pending_entry_no_file(url: str, site: str, crawled_at: str) -> dict:
    """Return a pending manifest entry with no backing file on disk."""
    return {
        "url": url,
        "md5": "abc123",
        "filename": url.split("/")[-1],
        "site": site,
        "crawled_at": crawled_at,
        "status": "pending",
        "report": None,
        "errors": [],
    }


def test_file_not_found_produces_error_status(tmp_path, capsys):
    """A pending entry whose file is missing must be marked as error with diagnostics."""
    from manifest import save_manifest, load_manifest

    url = "https://a.com/missing.pdf"
    entry = _pending_entry_no_file(url, "a.com", "2024-01-01T00:00:00+00:00")
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
    )

    entries = load_manifest(manifest_path)
    assert entries[0]["status"] == "error"
    # Error message should include the local path
    assert any("missing.pdf" in str(e) for e in entries[0]["errors"])

    out = capsys.readouterr().out
    assert "SKIP (file not found)" in out
    # crawled_at date should appear in the output
    assert "2024-01-01" in out


def test_file_not_found_summary_shows_count(tmp_path, capsys):
    """The final summary must report a non-zero 'File not found' count."""
    from manifest import save_manifest

    entries = [
        _pending_entry_no_file("https://a.com/doc1.pdf", "a.com", "2024-01-01T00:00:00+00:00"),
        _pending_entry_no_file("https://a.com/doc2.pdf", "a.com", "2024-01-02T00:00:00+00:00"),
    ]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
    )

    out = capsys.readouterr().out
    assert "File not found: 2" in out


def test_max_age_days_marks_old_entry_as_stale(tmp_path, capsys):
    """Entries older than max_age_days whose file is missing should be marked stale."""
    from manifest import save_manifest, load_manifest
    from datetime import datetime, timezone, timedelta

    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    url = "https://a.com/old.pdf"
    entry = _pending_entry_no_file(url, "a.com", old_date)
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_age_days=7,
    )

    entries = load_manifest(manifest_path)
    assert entries[0]["status"] == "error"
    # Error message should mention "stale"
    assert any("stale" in str(e).lower() for e in entries[0]["errors"])

    out = capsys.readouterr().out
    assert "stale" in out.lower()


def test_max_age_days_recent_entry_keeps_normal_message(tmp_path, capsys):
    """Recent missing entries (within max_age_days) should use the normal message."""
    from manifest import save_manifest, load_manifest
    from datetime import datetime, timezone, timedelta

    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    url = "https://a.com/recent.pdf"
    entry = _pending_entry_no_file(url, "a.com", recent_date)
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_age_days=7,
    )

    entries = load_manifest(manifest_path)
    assert entries[0]["status"] == "error"

    out = capsys.readouterr().out
    # Should NOT use the stale label for a recent entry
    assert "SKIP (file not found)" in out
    assert "SKIP (stale" not in out


# ---------------------------------------------------------------------------
# max_files limit
# ---------------------------------------------------------------------------

def test_max_files_limits_analysis_count(tmp_path, capsys):
    """With max_files=1, only one PDF should be analysed; the rest remain pending."""
    from manifest import save_manifest, load_manifest

    entries = [
        _pending_entry("https://a.com/doc1.pdf", "a.com", tmp_path),
        _pending_entry("https://a.com/doc2.pdf", "a.com", tmp_path),
        _pending_entry("https://a.com/doc3.pdf", "a.com", tmp_path),
    ]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_files=1,
    )

    result = load_manifest(manifest_path)
    processed = [e for e in result if e["status"] != "pending"]
    still_pending = [e for e in result if e["status"] == "pending"]

    # Exactly one file should have been analysed
    assert len(processed) == 1, f"Expected 1 processed, got {len(processed)}"
    # The remaining two should still be pending
    assert len(still_pending) == 2, f"Expected 2 pending, got {len(still_pending)}"

    out = capsys.readouterr().out
    assert "STOP" in out
    assert "max-files" in out.lower() or "--max-files" in out


def test_max_files_zero_analyses_nothing(tmp_path, capsys):
    """With max_files=0, no PDFs should be analysed even if files are present."""
    from manifest import save_manifest, load_manifest

    entries = [
        _pending_entry("https://a.com/doc1.pdf", "a.com", tmp_path),
        _pending_entry("https://a.com/doc2.pdf", "a.com", tmp_path),
    ]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_files=0,
    )

    result = load_manifest(manifest_path)
    # All entries should remain pending
    for e in result:
        assert e["status"] == "pending", f"{e['url']} should remain pending with max_files=0"

    out = capsys.readouterr().out
    assert "STOP" in out


def test_max_files_file_not_found_does_not_count(tmp_path):
    """File-not-found entries must NOT count against the max_files limit."""
    from manifest import save_manifest, load_manifest

    # One real file + two missing entries
    real_entry = _pending_entry("https://a.com/real.pdf", "a.com", tmp_path)
    missing1 = _pending_entry_no_file("https://a.com/gone1.pdf", "a.com", "2024-01-01T00:00:00+00:00")
    missing2 = _pending_entry_no_file("https://a.com/gone2.pdf", "a.com", "2024-01-01T00:00:00+00:00")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([missing1, missing2, real_entry], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_files=1,
    )

    result = load_manifest(manifest_path)
    by_url = {e["url"]: e for e in result}

    # The real file should be analysed (limit is 1, file-not-found don't count)
    assert by_url["https://a.com/real.pdf"]["status"] != "pending", \
        "Real file should have been analysed despite max_files=1 and two preceding misses"

    # Both missing entries should be marked as error
    assert by_url["https://a.com/gone1.pdf"]["status"] == "error"
    assert by_url["https://a.com/gone2.pdf"]["status"] == "error"


def test_max_files_none_is_unlimited(tmp_path):
    """When max_files is None (default), all files should be analysed."""
    from manifest import save_manifest, load_manifest

    entries = [
        _pending_entry(f"https://a.com/doc{i}.pdf", "a.com", tmp_path)
        for i in range(5)
    ]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        max_files=None,
    )

    result = load_manifest(manifest_path)
    for e in result:
        assert e["status"] != "pending", f"{e['url']} should have been analysed"


# ---------------------------------------------------------------------------
# Words and Images fields
# ---------------------------------------------------------------------------

def test_check_file_words_and_images_present_in_result(tmp_path):
    """check_file() result dict must always contain 'Words' and 'Images' keys."""
    import pikepdf
    from pdf_analyser import check_file

    # Create a minimal valid PDF using pikepdf
    p = tmp_path / "minimal.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.save(str(p))

    result = check_file(str(p))
    assert "Words" in result, "'Words' key must be present in check_file() result"
    assert "Images" in result, "'Images' key must be present in check_file() result"


def test_check_file_images_zero_for_text_only_pdf(tmp_path):
    """check_file() must return Images=0 for a PDF with no image XObjects."""
    import pikepdf
    from pdf_analyser import check_file

    p = tmp_path / "no_images.pdf"
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)
    pdf.save(str(p))

    result = check_file(str(p))
    assert result["Images"] == 0


def test_count_images_counts_image_xobjects(tmp_path):
    """_count_images() must count /Image XObjects on a page."""
    import pikepdf
    from pdf_analyser import _count_images

    p = tmp_path / "with_image.pdf"
    pdf = pikepdf.Pdf.new()

    # Create a tiny 1x1 white JPEG-like image stream (raw bytes sufficient for structure)
    image_stream = pikepdf.Stream(
        pdf,
        b"\xff\xd8\xff\xd9",  # minimal JPEG-like bytes
        Width=1,
        Height=1,
        ColorSpace=pikepdf.Name("/DeviceGray"),
        BitsPerComponent=8,
        Filter=pikepdf.Name("/DCTDecode"),
        Subtype=pikepdf.Name("/Image"),
        Type=pikepdf.Name("/XObject"),
    )
    xobjects = pikepdf.Dictionary(Im0=image_stream)
    resources = pikepdf.Dictionary(XObject=xobjects)
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 612, 792],
        Resources=resources,
    ))
    pdf.pages.append(page)
    pdf.save(str(p))

    opened = pikepdf.Pdf.open(str(p))
    assert _count_images(opened) == 1


def test_count_words_returns_none_on_broken_file(tmp_path):
    """_count_words() must return None (not raise) for unreadable files."""
    from pdf_analyser import _count_words

    p = tmp_path / "broken.pdf"
    p.write_bytes(b"not a pdf at all")

    result = _count_words(str(p))
    assert result is None


def test_count_words_returns_int_for_empty_pdf(tmp_path):
    """_count_words() must return 0 for a valid PDF with no text content."""
    import pikepdf
    from pdf_analyser import _count_words

    p = tmp_path / "empty.pdf"
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)
    pdf.save(str(p))

    result = _count_words(str(p))
    # pdfminer returns whitespace/form-feed for empty pages; no non-whitespace tokens → 0
    assert result == 0
