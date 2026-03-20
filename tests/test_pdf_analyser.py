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
# total_timeout limit
# ---------------------------------------------------------------------------

def test_total_timeout_zero_analyses_nothing(tmp_path, capsys):
    """With total_timeout=0, no PDFs should be analysed (budget exhausted immediately)."""
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
        total_timeout=0,
    )

    result = load_manifest(manifest_path)
    for e in result:
        assert e["status"] == "pending", (
            f"{e['url']} should remain pending with total_timeout=0"
        )

    out = capsys.readouterr().out
    assert "STOP" in out
    assert "time budget" in out.lower()


def test_total_timeout_none_is_unlimited(tmp_path):
    """When total_timeout is None (default), all files should be analysed."""
    from manifest import save_manifest, load_manifest

    entries = [
        _pending_entry(f"https://a.com/t{i}.pdf", "a.com", tmp_path)
        for i in range(3)
    ]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        total_timeout=None,
    )

    result = load_manifest(manifest_path)
    for e in result:
        assert e["status"] != "pending", f"{e['url']} should have been analysed"


def test_total_timeout_message_printed(tmp_path, capsys):
    """total_timeout budget message must appear in the output at the start of analysis."""
    from manifest import save_manifest

    entries = [_pending_entry("https://a.com/x.pdf", "a.com", tmp_path)]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        total_timeout=9999,
    )

    out = capsys.readouterr().out
    assert "9999s" in out


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


# ---------------------------------------------------------------------------
# Process-based per-file timeout
# ---------------------------------------------------------------------------

def test_process_timeout_raises_timeout_error(tmp_path):
    """_analyse_with_process_timeout must raise TimeoutError when child exceeds limit."""
    import time
    import multiprocessing
    from pdf_analyser import _analyse_with_process_timeout

    # Write a real (but trivial) file so the worker can be started; the worker
    # will never finish because we patch check_file to sleep forever inside it.
    p = tmp_path / "sleep.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    # Monkey-patch _run_check_file_worker to sleep indefinitely in the child.
    import pdf_analyser as _mod

    original = _mod._run_check_file_worker

    def _sleeping_worker(filename, site, queue, run_verapdf_check=False):
        time.sleep(3600)  # sleep much longer than the timeout

    _mod._run_check_file_worker = _sleeping_worker
    try:
        with pytest.raises(TimeoutError, match="per-file limit"):
            _analyse_with_process_timeout(str(p), "a.com", timeout=2)
    finally:
        _mod._run_check_file_worker = original


def test_process_timeout_returns_result_on_success(tmp_path):
    """_analyse_with_process_timeout must return the dict on success."""
    import pdf_analyser as _mod
    from pdf_analyser import _analyse_with_process_timeout

    p = tmp_path / "ok.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    original = _mod._run_check_file_worker

    def _fast_worker(filename, site, queue, run_verapdf_check=False):
        queue.put((True, {"TaggedTest": "Pass", "_log": ""}))

    _mod._run_check_file_worker = _fast_worker
    try:
        result = _analyse_with_process_timeout(str(p), "a.com", timeout=10)
        assert result["TaggedTest"] == "Pass"
    finally:
        _mod._run_check_file_worker = original


def test_process_timeout_handles_worker_exception(tmp_path):
    """_analyse_with_process_timeout must raise RuntimeError when worker puts an error."""
    import pdf_analyser as _mod
    from pdf_analyser import _analyse_with_process_timeout

    p = tmp_path / "err.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    original = _mod._run_check_file_worker

    def _error_worker(filename, site, queue, run_verapdf_check=False):
        queue.put((False, "something went wrong"))

    _mod._run_check_file_worker = _error_worker
    try:
        with pytest.raises(RuntimeError, match="something went wrong"):
            _analyse_with_process_timeout(str(p), "a.com", timeout=10)
    finally:
        _mod._run_check_file_worker = original


def test_per_file_timeout_marks_entry_as_error(tmp_path, capsys):
    """main() must mark a timed-out file as error and continue to the next file."""
    import time
    from manifest import save_manifest, load_manifest
    import pdf_analyser as _mod

    p1 = tmp_path / "a.com" / "slow.pdf"
    p1.parent.mkdir(parents=True, exist_ok=True)
    p1.write_bytes(b"%PDF-1.4 fake")

    p2 = tmp_path / "a.com" / "fast.pdf"
    p2.write_bytes(b"%PDF-1.4 fake")

    from manifest import build_entry
    entries = [
        build_entry("https://a.com/slow.pdf", p1, "a.com"),
        build_entry("https://a.com/fast.pdf", p2, "a.com"),
    ]
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(entries, manifest_path)

    original = _mod._run_check_file_worker

    call_count = [0]

    def _selective_worker(filename, site, queue, run_verapdf_check=False):
        call_count[0] += 1
        if "slow" in filename:
            time.sleep(3600)  # will be killed by timeout
        else:
            queue.put((True, {"TaggedTest": "Pass", "_log": ""}))

    _mod._run_check_file_worker = _selective_worker
    try:
        analyser_main(
            manifest_path=str(manifest_path),
            crawled_dir=str(tmp_path),
            keep_files=True,
            per_file_timeout=2,
        )
    finally:
        _mod._run_check_file_worker = original

    result = load_manifest(manifest_path)
    by_url = {e["url"]: e for e in result}

    # The slow file should be marked as error (timed out)
    assert by_url["https://a.com/slow.pdf"]["status"] == "error"
    assert any("exceeded" in str(e).lower() or "timeout" in str(e).lower()
               for e in by_url["https://a.com/slow.pdf"]["errors"])

    # The fast file should still be processed (analyser must continue after a timeout)
    assert by_url["https://a.com/fast.pdf"]["status"] != "pending"

    out = capsys.readouterr().out
    assert "TIMEOUT" in out



# ---------------------------------------------------------------------------
# veraPDF integration
# ---------------------------------------------------------------------------

def test_run_verapdf_returns_none_when_not_on_path(tmp_path, monkeypatch):
    """run_verapdf() returns None silently when veraPDF is not installed."""
    import shutil
    import pdf_analyser as _mod

    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    p = tmp_path / "test.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    assert _mod.run_verapdf(str(p)) is None


def test_run_verapdf_compliant(tmp_path, monkeypatch):
    """run_verapdf() parses a compliant MRR report correctly."""
    import shutil
    import subprocess
    import pdf_analyser as _mod

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<report><jobs><job>"
        '<validationReport profileName="PDF/UA-1" isCompliant="true">'
        '<details failedChecks="0" passedChecks="120"/>'
        "</validationReport>"
        "</job></jobs></report>"
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/verapdf")

    class _Done:
        stdout = xml
        stderr = ""
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())

    p = tmp_path / "ok.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    result = _mod.run_verapdf(str(p))

    assert result is not None
    assert result["compliant"] is True
    assert result["profile"] == "PDF/UA-1"
    assert result["failed_checks"] == 0
    assert result["passed_checks"] == 120
    assert result["failed_rules"] == []
    assert result["error"] is None


def test_run_verapdf_non_compliant(tmp_path, monkeypatch):
    """run_verapdf() extracts failed rules from a non-compliant MRR report."""
    import shutil
    import subprocess
    import pdf_analyser as _mod

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<report><jobs><job>"
        '<validationReport profileName="PDF/UA-1" isCompliant="false">'
        '<details failedChecks="3" passedChecks="117">'
        '<rule clause="7.1" testNumber="1" status="FAILED" failedChecks="2"/>'
        '<rule clause="7.2" testNumber="3" status="FAILED" failedChecks="1"/>'
        '<rule clause="7.3" testNumber="1" status="PASSED" failedChecks="0"/>'
        "</details>"
        "</validationReport>"
        "</job></jobs></report>"
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/verapdf")

    class _Done:
        stdout = xml
        stderr = ""
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())

    p = tmp_path / "fail.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    result = _mod.run_verapdf(str(p))

    assert result is not None
    assert result["compliant"] is False
    assert result["failed_checks"] == 3
    assert result["passed_checks"] == 117
    assert "7.1-1" in result["failed_rules"]
    assert "7.2-3" in result["failed_rules"]
    assert "7.3-1" not in result["failed_rules"]
    assert result["error"] is None


def test_run_verapdf_timeout(tmp_path, monkeypatch):
    """run_verapdf() handles subprocess TimeoutExpired gracefully."""
    import shutil
    import subprocess
    import pdf_analyser as _mod

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/verapdf")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("verapdf", 120)),
    )

    p = tmp_path / "slow.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    result = _mod.run_verapdf(str(p))

    assert result is not None
    assert result["error"] is not None
    assert "timed out" in result["error"].lower()


def test_run_verapdf_empty_output(tmp_path, monkeypatch):
    """run_verapdf() reports an error when veraPDF produces no stdout."""
    import shutil
    import subprocess
    import pdf_analyser as _mod

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/verapdf")

    class _Empty:
        stdout = ""
        stderr = "veraPDF internal error"
        returncode = 2

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Empty())

    p = tmp_path / "empty.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    result = _mod.run_verapdf(str(p))

    assert result is not None
    assert result["error"] is not None


def test_run_verapdf_exception_message(tmp_path, monkeypatch):
    """run_verapdf() surfaces <exceptionMessage> from the XML report."""
    import shutil
    import subprocess
    import pdf_analyser as _mod

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<report><jobs><job>"
        "<exceptionMessage>PDF header not found</exceptionMessage>"
        "</job></jobs></report>"
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/verapdf")

    class _Done:
        stdout = xml
        stderr = ""
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())

    p = tmp_path / "broken.pdf"
    p.write_bytes(b"not a pdf")
    result = _mod.run_verapdf(str(p))

    assert result is not None
    assert result["error"] is not None
    assert "PDF header" in result["error"]


def test_check_file_includes_verapdf_when_flag_set(tmp_path, monkeypatch):
    """check_file(run_verapdf_check=True) adds 'veraPDF' key to the result."""
    import shutil
    import pdf_analyser as _mod

    # Simulate veraPDF absent – run_verapdf() returns None.
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    p = tmp_path / "test.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    result = _mod.check_file(str(p), run_verapdf_check=True)
    assert "veraPDF" in result
    assert result["veraPDF"] is None  # None because veraPDF not on PATH


def test_check_file_no_verapdf_key_without_flag(tmp_path):
    """check_file() without run_verapdf_check must NOT include 'veraPDF' key."""
    import pdf_analyser as _mod

    p = tmp_path / "test.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    result = _mod.check_file(str(p))
    assert "veraPDF" not in result


def test_main_verapdf_flag_off_by_default(tmp_path):
    """main() without run_verapdf must not add 'veraPDF' key to the manifest."""
    from manifest import save_manifest, load_manifest, build_entry
    import pdf_analyser as _mod

    p = tmp_path / "a.com" / "doc.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"%PDF-1.4 fake")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([build_entry("https://a.com/doc.pdf", p, "a.com")], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
    )

    entries = load_manifest(manifest_path)
    report = entries[0].get("report", {})
    assert "veraPDF" not in report


def test_main_verapdf_flag_stores_result(tmp_path, monkeypatch):
    """main(run_verapdf=True) stores veraPDF results in the manifest."""
    import shutil
    import subprocess
    from manifest import save_manifest, load_manifest, build_entry
    import pdf_analyser as _mod

    # Provide a minimal compliant MRR response.
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<report><jobs><job>"
        '<validationReport profileName="PDF/UA-1" isCompliant="true">'
        '<details failedChecks="0" passedChecks="10"/>'
        "</validationReport>"
        "</job></jobs></report>"
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/verapdf")

    class _Done:
        stdout = xml
        stderr = ""
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())

    p = tmp_path / "a.com" / "doc.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"%PDF-1.4 fake")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([build_entry("https://a.com/doc.pdf", p, "a.com")], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        run_verapdf=True,
    )

    entries = load_manifest(manifest_path)
    report = entries[0].get("report", {})
    assert "veraPDF" in report
    vp = report["veraPDF"]
    assert vp is not None
    assert vp["compliant"] is True
    assert vp["failed_checks"] == 0


def test_main_verapdf_prints_status(tmp_path, monkeypatch, capsys):
    """main(run_verapdf=True) prints availability status before analysis."""
    import shutil
    from manifest import save_manifest, build_entry
    import pdf_analyser as _mod

    # veraPDF absent.
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    p = tmp_path / "a.com" / "x.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"%PDF-1.4 fake")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([build_entry("https://a.com/x.pdf", p, "a.com")], manifest_path)

    analyser_main(
        manifest_path=str(manifest_path),
        crawled_dir=str(tmp_path),
        keep_files=True,
        run_verapdf=True,
    )

    out = capsys.readouterr().out
    assert "verapdf" in out.lower()


def test_main_passes_verapdf_flag_to_process_timeout(tmp_path):
    """main(run_verapdf=True) must pass run_verapdf_check=True to _analyse_with_process_timeout."""
    from manifest import save_manifest, build_entry
    import pdf_analyser as _mod

    p = tmp_path / "a.com" / "doc.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"%PDF-1.4 fake")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([build_entry("https://a.com/doc.pdf", p, "a.com")], manifest_path)

    captured = {}
    original = _mod._analyse_with_process_timeout

    def _capturing(*args, **kwargs):
        # args: (filename, site, per_file_timeout, run_verapdf_check)
        captured["run_verapdf_check"] = args[3] if len(args) > 3 else kwargs.get("run_verapdf_check", False)
        return {"TaggedTest": None, "_log": ""}

    _mod._analyse_with_process_timeout = _capturing
    try:
        analyser_main(
            manifest_path=str(manifest_path),
            crawled_dir=str(tmp_path),
            keep_files=True,
            run_verapdf=True,
        )
    finally:
        _mod._analyse_with_process_timeout = original

    assert captured.get("run_verapdf_check") is True, (
        "run_verapdf_check should be True when main() is called with run_verapdf=True"
    )


def test_main_passes_false_verapdf_flag_by_default(tmp_path):
    """main() must pass run_verapdf_check=False to _analyse_with_process_timeout by default."""
    from manifest import save_manifest, build_entry
    import pdf_analyser as _mod

    p = tmp_path / "a.com" / "doc.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"%PDF-1.4 fake")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([build_entry("https://a.com/doc.pdf", p, "a.com")], manifest_path)

    captured = {}
    original = _mod._analyse_with_process_timeout

    def _capturing(*args, **kwargs):
        captured["run_verapdf_check"] = args[3] if len(args) > 3 else kwargs.get("run_verapdf_check", False)
        return {"TaggedTest": None, "_log": ""}

    _mod._analyse_with_process_timeout = _capturing
    try:
        analyser_main(
            manifest_path=str(manifest_path),
            crawled_dir=str(tmp_path),
            keep_files=True,
            # run_verapdf defaults to False
        )
    finally:
        _mod._analyse_with_process_timeout = original

    assert captured.get("run_verapdf_check") is False, (
        "run_verapdf_check should be False when main() is called without run_verapdf"
    )
