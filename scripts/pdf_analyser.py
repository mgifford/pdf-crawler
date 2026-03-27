"""
PDF accessibility analyser.

Checks each PDF in the YAML manifest that has status == "pending" and writes
the results back into the manifest.  After analysis, local PDF files are
deleted to keep the repository lean.

Usage:
    python pdf_analyser.py [--manifest reports/manifest.yaml] [--keep-files]

The accessibility checks mirror those of simplA11yPDFCrawler's pdfCheck.py:

    TaggedTest      – is the document tagged?
    EmptyTextTest   – does the document contain text (not just images)?
    ProtectedTest   – is the document protected against assistive technologies?
    TitleTest       – does the document have a title with DisplayDocTitle set?
    LanguageTest    – does the document have a valid default language?
    BookmarksTest   – for documents > 20 pages, are there bookmarks?

References
----------
- https://github.com/accessibility-luxembourg/simplA11yPDFCrawler
- Matterhorn Protocol: https://www.pdfa.org/resource/the-matterhorn-protocol/
"""

from __future__ import annotations

import multiprocessing
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz
import pikepdf
from pikepdf import Pdf, String
from pikepdf.models.metadata import decode_pdf_date
import dateparser
from bitstring import BitArray
from langcodes import Language, tag_parser
from pdfminer.high_level import extract_text as _pdfminer_extract_text

import sys

sys.path.insert(0, str(Path(__file__).parent))
from manifest import (
    load_manifest,
    save_manifest,
    mark_analysed,
    mark_error,
    pending_entries,
)

# EU Web Accessibility Directive implementation deadline (Directive 2016/2102/EU).
# Public sector PDFs published before this date are considered exempt from
# the simplified monitoring requirements under Commission Decision 2018/1524.
DEADLINE_DATE_STR = "2018-09-23T00:00:00+02:00"

# Path where main() writes the stale entry count after each run, so that the
# calling workflow can check it without parsing stdout.
STALE_COUNT_FILE = "/tmp/pdf_analyser_stale.txt"

# ---------------------------------------------------------------------------
# Optional veraPDF validation
# ---------------------------------------------------------------------------

def run_verapdf(filepath: str) -> Optional[Dict[str, Any]]:
    """Run veraPDF (PDF/UA-1) against *filepath* and return a summary dict.

    Returns ``None`` when veraPDF is not installed (i.e. ``verapdf`` is not on
    PATH), so the rest of the analysis can proceed without error.

    veraPDF must be installed separately; see https://verapdf.org/.
    The PDF/UA-1 profile (ISO 14289-1) is used because it is the primary
    accessibility conformance standard targeted by this project.

    Returns:
        A dict with keys:

        ``compliant``      (bool | None) – True if the file passes PDF/UA-1.
        ``profile``        (str  | None) – Profile name reported by veraPDF.
        ``failed_checks``  (int  | None) – Number of failed checks.
        ``passed_checks``  (int  | None) – Number of passed checks.
        ``failed_rules``   (list[str])   – Clause references of failed rules.
        ``error``          (str  | None) – Error message if veraPDF itself failed.

        or ``None`` if veraPDF is not available.

    References
    ----------
    - https://docs.verapdf.org/cli/
    - ISO 14289-1 (PDF/UA-1)
    """
    if not shutil.which("verapdf"):
        return None

    result: Dict[str, Any] = {
        "compliant": None,
        "profile": None,
        "failed_checks": None,
        "passed_checks": None,
        "failed_rules": [],
        "error": None,
    }

    try:
        proc = subprocess.run(
            ["verapdf", "--flavour", "ua1", "--format", "mrr", filepath],
            capture_output=True,
            text=True,
            timeout=120,
        )
        raw = proc.stdout.strip()
        if not raw:
            result["error"] = proc.stderr.strip() or "veraPDF produced no output"
            return result

        root = ET.fromstring(raw)

        # Locate the <validationReport> element (namespace-agnostic).
        val_report = None
        for elem in root.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local == "validationReport":
                val_report = elem
                break

        if val_report is None:
            # veraPDF emits <exceptionMessage> for unreadable files.
            for elem in root.iter():
                local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local == "exceptionMessage":
                    result["error"] = elem.text or "veraPDF exception (no message)"
                    return result
            result["error"] = "No validationReport element in veraPDF output"
            return result

        result["profile"] = val_report.get("profileName")
        compliant_str = (val_report.get("isCompliant") or "").lower()
        if compliant_str in ("true", "false"):
            result["compliant"] = compliant_str == "true"

        # Locate the <details> element inside validationReport.
        details = None
        for elem in val_report.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local == "details":
                details = elem
                break

        if details is not None:
            for attr, key in (
                ("failedChecks", "failed_checks"),
                ("passedChecks", "passed_checks"),
            ):
                val = details.get(attr)
                if val is not None:
                    try:
                        result[key] = int(val)
                    except ValueError:
                        pass

            failed_rules: List[str] = []
            for elem in details.iter():
                local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local == "rule" and elem.get("status") == "FAILED":
                    clause = elem.get("clause", "")
                    test_num = elem.get("testNumber", "")
                    ref = (
                        f"{clause}-{test_num}"
                        if clause and test_num
                        else (clause or test_num)
                    )
                    if ref:
                        failed_rules.append(ref)
            result["failed_rules"] = failed_rules

    except subprocess.TimeoutExpired:
        result["error"] = "veraPDF timed out (120s limit)"
    except ET.ParseError as exc:
        result["error"] = f"veraPDF XML parse error: {exc}"
    except Exception as exc:
        result["error"] = f"veraPDF error: {exc}"

    return result


# ---------------------------------------------------------------------------
# Per-file analysis with a hard process-level timeout
# ---------------------------------------------------------------------------

def _run_check_file_worker(
    filename: str,
    site: str,
    queue: "multiprocessing.Queue",
    run_verapdf_check: bool = False,
) -> None:
    """Worker function run inside a child process.

    Calls check_file() and places ``(True, result)`` on *queue* on success, or
    ``(False, error_message)`` on failure.  Using a subprocess instead of
    SIGALRM means the timeout is enforced at the OS level via SIGKILL, which
    reliably terminates any blocking C-extension call (e.g. pdfminer/pikepdf).
    """
    try:
        result = check_file(filename, site=site, run_verapdf_check=run_verapdf_check)
        queue.put((True, result))
    except Exception as exc:  # pragma: no cover
        queue.put((False, str(exc)))


def _analyse_with_process_timeout(
    filename: str,
    site: str,
    timeout: int,
    run_verapdf_check: bool = False,
) -> dict:
    """Run check_file() in a child process with a hard wall-clock *timeout*.

    Unlike SIGALRM (which cannot interrupt blocking C-extension calls),
    killing the child process with SIGKILL is guaranteed to terminate it
    regardless of what native library it is blocked in.

    Args:
        filename: Path to the PDF file to analyse.
        site: Site/domain string passed to check_file().
        timeout: Maximum seconds to wait for the child process.
        run_verapdf_check: When True, veraPDF is invoked inside the child
            process if it is available on PATH.

    Returns:
        The analysis result dict returned by check_file().

    Raises:
        TimeoutError: If the child is still alive after *timeout* seconds.
        RuntimeError: If the child crashes or produces no result.
    """
    ctx = multiprocessing.get_context("fork")
    queue: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(
        target=_run_check_file_worker,
        args=(filename, site, queue, run_verapdf_check),
        daemon=True,
    )
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        if proc.is_alive():
            proc.kill()
            proc.join()
        raise TimeoutError(f"Analysis exceeded {timeout}s per-file limit")

    try:
        ok, payload = queue.get_nowait()
    except Exception:
        raise RuntimeError(
            f"Analysis subprocess exited (code {proc.exitcode}) without producing a result"
        )

    if ok:
        return payload
    raise RuntimeError(payload)


# ---------------------------------------------------------------------------
# Date helpers (ported from simplA11yPDFCrawler)
# ---------------------------------------------------------------------------

def _extract_date(s) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, String):
        s = str(s)
    try:
        return dateparser.parse(
            s, settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True}
        )
    except ValueError:
        return None


def _extract_pdf_date(s) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, String):
        s = str(s)
    s = s.strip()
    if not s or s.startswith("CPY Document"):
        return None

    # Fix malformed timezone offsets (e.g. +01, +1'0', +01'00')
    m = re.search(r"\+([\d\':\s]+)$", s)
    if m is not None:
        tz = m.group(0)
        orig_tz = tz
        if len(tz) == 3:
            tz += "00"
        if "'" in tz:
            n = re.search(r"\+\s?(\d+)\'\s?(\d+)\'?", tz)
            tz = "+%02d%02d" % (int(n.group(1)), int(n.group(2))) if n else "+0000"
        s = s.replace(orig_tz, tz)

    try:
        pdf_date = decode_pdf_date(s)
        if not (
            pdf_date.tzinfo is not None
            and pdf_date.tzinfo.utcoffset(pdf_date) is not None
        ):
            pdf_date = pdf_date.replace(tzinfo=pytz.utc)
        return pdf_date
    except (ValueError, AttributeError):
        return _extract_date(s)


# ---------------------------------------------------------------------------
# Content analysis helpers
# ---------------------------------------------------------------------------

def _init_analysis() -> Dict[str, Any]:
    return {"numTxt": 0, "fontNames": set()}


def _merge_analyses(a: Dict, b: Dict) -> Dict:
    return {
        "numTxt": a["numTxt"] + b["numTxt"],
        "fontNames": a["fontNames"] | b["fontNames"],
    }


def _analyse_content(content, is_xobject: bool = False) -> Dict[str, Any]:
    res = _init_analysis()
    resources = content.get("/Resources")
    if resources is None:
        return res

    # Recurse into Form XObjects
    xobject = resources.get("/XObject")
    if xobject is not None:
        for key in xobject:
            obj = xobject[key]
            if str(obj.get("/Subtype")) == "/Form" and obj.get("/Ref") is None:
                res = _merge_analyses(res, _analyse_content(obj, True))

    font_dict = resources.get("/Font")
    if font_dict is not None:
        for key in font_dict:
            font = font_dict[key]
            font_desc = font.get("/FontDescriptor")
            if font_desc is not None:
                font_name = str(font_desc.get("/FontName", ""))
            else:
                font_name = str(font.get("/BaseFont", ""))
            res["fontNames"].add(font_name)

        for _operands, _operator in pikepdf.parse_content_stream(content, "Tf"):
            res["numTxt"] += 1

    return res


def _count_images(pdf: Pdf) -> int:
    """Count image XObjects referenced across all pages (including nested Form XObjects)."""

    def _count_in_resources(resources) -> int:
        count = 0
        if resources is None:
            return count
        xobject = resources.get("/XObject")
        if xobject is None:
            return count
        for key in xobject:
            try:
                obj = xobject[key]
                subtype = str(obj.get("/Subtype", ""))
                if subtype == "/Image":
                    count += 1
                elif subtype == "/Form" and obj.get("/Ref") is None:
                    count += _count_in_resources(obj.get("/Resources"))
            except Exception:
                pass
        return count

    total = 0
    for page in pdf.pages:
        total += _count_in_resources(page.get("/Resources"))
    return total


def _count_words(filename: str) -> Optional[int]:
    """Extract text from *filename* using pdfminer.six and return the word count.

    Uses ``re.findall(r'\\S+', text)`` so that any whitespace variant (spaces,
    tabs, newlines, form-feed characters) acts as a word delimiter, and
    consecutive whitespace sequences are not double-counted.
    """
    try:
        text = _pdfminer_extract_text(filename)
        return len(re.findall(r"\S+", text)) if text else 0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core check function
# ---------------------------------------------------------------------------

def check_file(
    filename: str,
    site: str = None,
    run_verapdf_check: bool = False,
) -> Dict[str, Any]:
    """Run all accessibility checks on *filename* and return a result dict.

    Args:
        filename: Path to the PDF file.
        site: Optional site/domain string (currently unused in checks).
        run_verapdf_check: When True and veraPDF is on PATH, run veraPDF
            (PDF/UA-1) against the file and store the result under the
            ``veraPDF`` key in the returned dict.
    """
    result: Dict[str, Any] = {
        "Accessible": True,
        "TotallyInaccessible": False,
        "BrokenFile": False,
        "TaggedTest": None,
        "EmptyTextTest": None,
        "ProtectedTest": None,
        "TitleTest": None,
        "LanguageTest": None,
        "BookmarksTest": None,
        "Exempt": False,
        "Date": None,
        "hasTitle": None,
        "hasDisplayDocTitle": None,
        "hasLang": None,
        "InvalidLang": None,
        "Form": None,
        "xfa": None,
        "hasBookmarks": None,
        "hasXmp": None,
        "PDFVersion": None,
        "Creator": None,
        "Producer": None,
        "Pages": None,
        "Words": None,
        "Images": None,
        "_log": "",
    }

    try:
        pdf = Pdf.open(filename)
        result["PDFVersion"] = pdf.pdf_version
        result["Pages"] = len(pdf.pages)
        result["Images"] = _count_images(pdf)

        meta = pdf.open_metadata()
        if meta is None:
            result["hasXmp"] = False
            result["Accessible"] = False
            result["_log"] += "xmp "
        else:
            result["hasXmp"] = True
            result["Creator"] = meta.get("xmp:CreatorTool")
            result["Producer"] = meta.get("pdf:Producer")

            xmp_modify = _extract_date(meta.get("xmp:ModifyDate"))
            dc_modified = _extract_date(meta.get("dc:Modified"))
            mod_date = _extract_pdf_date(pdf.docinfo.get("/ModDate"))
            create_date = _extract_date(meta.get("xmp:CreateDate"))
            creation_date = _extract_pdf_date(pdf.docinfo.get("/CreationDate"))

            date = xmp_modify or dc_modified or mod_date or create_date or creation_date
            if date is not None:
                deadline = datetime.strptime(DEADLINE_DATE_STR, "%Y-%m-%dT%H:%M:%S%z")
                result["Date"] = str(date)
                if date < deadline:
                    result["Exempt"] = True
            else:
                result["_log"] += "no date found, "

            # Title check
            title = meta.get("dc:title") or pdf.docinfo.get("/Title")
            viewer_prefs = pdf.Root.get("/ViewerPreferences")
            if title is not None and len(str(title)) != 0:
                result["hasTitle"] = True
                if viewer_prefs is not None:
                    disp = viewer_prefs.get("/DisplayDocTitle")
                    if disp is not None:
                        if disp is False:
                            result["TitleTest"] = "Fail"
                            result["hasDisplayDocTitle"] = False
                            result["Accessible"] = False
                            result["_log"] += "title, "
                        else:
                            result["TitleTest"] = "Pass"
                            result["hasDisplayDocTitle"] = True
                    else:
                        result["TitleTest"] = "Fail"
                        result["hasDisplayDocTitle"] = False
                        result["Accessible"] = False
                        result["_log"] += "title, "
                else:
                    result["TitleTest"] = "Fail"
                    result["hasDisplayDocTitle"] = False
                    result["Accessible"] = False
                    result["_log"] += "title, "
            else:
                result["TitleTest"] = "Fail"
                result["hasTitle"] = False
                result["Accessible"] = False
                result["_log"] += "title, "

        # Tagged check
        struct_tree = pdf.Root.get("/StructTreeRoot")
        if struct_tree is not None:
            mark_info = pdf.Root.get("/MarkInfo")
            if mark_info is not None:
                marked = mark_info.get("/Marked")
                if marked is not None:
                    if marked is False:
                        result["TaggedTest"] = "Fail"
                        result["Accessible"] = False
                        result["_log"] += "tagged, "
                    else:
                        result["TaggedTest"] = "Pass"
                else:
                    result["TaggedTest"] = "Fail"
                    result["Accessible"] = False
                    result["_log"] += "tagged, "
            else:
                result["TaggedTest"] = "Fail"
                result["Accessible"] = False
                result["_log"] += "tagged, "
        else:
            result["TaggedTest"] = "Fail"
            result["Accessible"] = False
            result["_log"] += "tagged, "

        # Protection check
        result["ProtectedTest"] = "Pass"
        if pdf.is_encrypted:
            if pdf.encryption.P is None or pdf.allow is None:
                result["Accessible"] = False
                result["ProtectedTest"] = "Fail"
            else:
                bits = BitArray(intbe=pdf.encryption.P, length=16)
                bit10 = bits[16 - 10]
                bit5 = bits[16 - 5]
                if (not bit10) and bit5:
                    result["ProtectedTest"] = "Pass"
                    result["_log"] += (
                        f"P[10]={bit10} P[5]={bit5} R={pdf.encryption.R}, "
                    )
                else:
                    result["ProtectedTest"] = (
                        "Pass" if pdf.allow.accessibility else "Fail"
                    )
            if result["ProtectedTest"] == "Fail":
                result["Accessible"] = False

        # Language check
        lang = pdf.Root.get("/Lang")
        if lang is not None and len(str(lang)) != 0:
            result["hasLang"] = True
            try:
                if not Language.get(str(lang)).is_valid():
                    result["InvalidLang"] = True
                    result["LanguageTest"] = "Fail"
                    result["_log"] += f"Default language is not valid: {lang}, "
                    result["Accessible"] = False
                else:
                    result["LanguageTest"] = "Pass"
            except tag_parser.LanguageTagError:
                result["InvalidLang"] = True
                result["LanguageTest"] = "Fail"
                result["_log"] += f"Default language is not valid: {lang}, "
                result["Accessible"] = False
        else:
            result["LanguageTest"] = "Fail"
            result["hasLang"] = False
            result["Accessible"] = False
            result["_log"] += "lang, "

        # Form / XFA check
        acro = pdf.Root.get("/AcroForm")
        if acro is not None:
            try:
                xfa = acro.get("/XFA")
                if xfa is not None:
                    try:
                        for n in range(len(xfa) - 1):
                            if xfa[n] == "config":
                                xml_bytes = xfa[n + 1].read_bytes().decode()
                                doc = ET.fromstring(xml_bytes)
                                for elem in doc.iter():
                                    if re.match(r".*dynamicRender", elem.tag):
                                        if elem.text == "required":
                                            result["xfa"] = True
                                            result["_log"] += "xfa, "
                                break
                    except TypeError:
                        result["_log"] += "malformed xfa, "
            except ValueError:
                result["_log"] += "malformed xfa, "

            try:
                fields = acro.get("/Fields")
                if fields is not None and len(fields) != 0:
                    result["Form"] = True
                    result["Exempt"] = False
            except ValueError:
                result["_log"] += "malformed Form fields, "

        # Bookmarks check
        outline = pdf.open_outline()
        result["hasBookmarks"] = len(outline.root) > 0
        result["BookmarksTest"] = "Pass"
        if not result["hasBookmarks"] and len(pdf.pages) > 20:
            result["BookmarksTest"] = "Fail"
            result["Accessible"] = False
            result["_log"] += "no bookmarks and more than 20 pages, "

        # Empty text check
        combined = _init_analysis()
        for page in pdf.pages:
            combined = _merge_analyses(combined, _analyse_content(page))

        result["EmptyTextTest"] = (
            "Fail"
            if (len(combined["fontNames"]) == 0 or combined["numTxt"] == 0)
            else "Pass"
        )

        result["Words"] = _count_words(filename)

    except pikepdf.PasswordError as err:
        result["BrokenFile"] = True
        result["Accessible"] = None
        result["_log"] += f"Password protected: {err}"
    except pikepdf.PdfError as err:
        result["BrokenFile"] = True
        result["Accessible"] = None
        result["_log"] += f"PdfError: {err}"
    except ValueError as err:
        result["BrokenFile"] = True
        result["Accessible"] = None
        result["_log"] += f"ValueError: {err}"

    # Derived flags
    if result["TaggedTest"] == "Fail" and result["EmptyTextTest"] == "Fail":
        result["TotallyInaccessible"] = True
    if result["ProtectedTest"] == "Fail":
        result["TotallyInaccessible"] = True

    # Optional veraPDF (PDF/UA-1) validation.
    if run_verapdf_check:
        result["veraPDF"] = run_verapdf(filename)

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_crawled_at(crawled_at: Optional[str]) -> Optional[datetime]:
    """Parse a crawled_at ISO-8601 string into an aware datetime, or return None."""
    if not crawled_at:
        return None
    try:
        dt = datetime.fromisoformat(crawled_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def main(
    manifest_path: str = "reports/manifest.yaml",
    crawled_dir: str = "crawled_files",
    keep_files: bool = False,
    site_filter: Optional[str] = None,
    max_file_size_mb: float = 200.0,
    per_file_timeout: int = 120,
    max_age_days: Optional[int] = None,
    max_files: Optional[int] = None,
    total_timeout: Optional[int] = None,
    run_verapdf: bool = False,
) -> int:
    """Analyse pending PDFs and update the manifest.

    Args:
        manifest_path: Path to the YAML manifest.
        crawled_dir: Root directory where crawled files are stored.
        keep_files: When True, local files are not deleted after analysis.
        site_filter: Only analyse entries for this site/domain.
        max_file_size_mb: Skip files larger than this threshold (MB).
        per_file_timeout: Maximum seconds to spend analysing a single file.
            Uses SIGALRM on POSIX systems; ignored on Windows.
        max_age_days: If set, pending entries older than this many days whose
            local file is not found are marked as stale errors and skipped.
            This prevents stale manifest entries from previous runs from
            generating spurious "file not found" noise.
        max_files: If set, stop after analysing this many PDF files (entries
            that are skipped as file-not-found, non-PDF, or oversized do not
            count toward this limit).  Useful for bounding the run time when
            a site has a very large number of pending entries.
        total_timeout: If set, stop analysis after this many seconds of total
            wall-clock time. Remaining entries stay pending and will be
            processed on the next run. Useful for staying within CI time
            limits so that report generation can still complete in the same
            job.
        run_verapdf: When True, run veraPDF (PDF/UA-1) against each PDF if
            ``verapdf`` is available on PATH and store the result in the
            manifest under the ``veraPDF`` key.

    Returns:
        The number of stale manifest entries found (pending entries whose
        local file is missing and whose ``crawled_at`` is older than
        ``max_age_days``).  Returns 0 when ``max_age_days`` is not set or
        no stale entries exist.
    """
    print(f"pikepdf version: {pikepdf.__version__}")

    entries = load_manifest(manifest_path)
    pending = pending_entries(entries)

    if site_filter:
        pending = [e for e in pending if e.get("site") == site_filter]
        print(f"Filtering to site '{site_filter}': {len(pending)} pending entry/entries.")

    if not pending:
        print("No pending entries in manifest – nothing to do.")
        return 0

    print(f"Analysing {len(pending)} pending file(s)…")
    if max_files is not None:
        print(f"  File analysis limit: at most {max_files} PDF file(s) will be analysed this run.")
    if total_timeout is not None:
        print(f"  Total time budget: at most {total_timeout}s of wall-clock time will be used this run.")
    if max_age_days is not None:
        print(
            f"  Stale-entry threshold: entries older than {max_age_days} day(s) "
            "without a local file will be marked as stale errors and skipped."
        )
    if run_verapdf:
        if shutil.which("verapdf"):
            print("  veraPDF: enabled (found on PATH) – PDF/UA-1 validation will run per file.")
        else:
            print("  veraPDF: requested but not found on PATH – veraPDF checks will be skipped.")

    accessible_count = 0
    issues_count = 0
    broken_count = 0
    error_count = 0
    file_not_found_count = 0
    stale_count = 0
    skipped_count = 0
    files_analysed_count = 0

    now_utc = datetime.now(timezone.utc)
    t_run_start = time.monotonic()

    for entry in pending:
        # Check the total wall-clock budget before starting each new file.
        if total_timeout is not None:
            elapsed_total = time.monotonic() - t_run_start
            if elapsed_total >= total_timeout:
                print(
                    f"  STOP: total time budget of {total_timeout}s exceeded "
                    f"({elapsed_total:.0f}s elapsed). "
                    "Remaining pending entries will be processed in the next run."
                )
                break

        url = entry["url"]
        site = entry.get("site", "")
        filename = entry.get("filename", "")
        crawled_at = entry.get("crawled_at", "")
        local_path = Path(crawled_dir) / site / filename

        if not local_path.exists():
            crawled_dt = _parse_crawled_at(crawled_at)
            age_str = ""
            if crawled_dt:
                age = now_utc - crawled_dt
                age_days = age.total_seconds() / 86400
                if age_days < 1:
                    age_str = f" (crawled {age_days:.1f} day(s) ago at {crawled_at})"
                else:
                    age_str = f" (crawled {int(age_days)} day(s) ago at {crawled_at})"
            else:
                age_str = f" (crawled_at: {crawled_at!r})" if crawled_at else ""

            # Decide whether this is a stale entry (older than max_age_days).
            is_stale = (
                max_age_days is not None
                and crawled_dt is not None
                and (now_utc - crawled_dt).total_seconds() / 86400 > max_age_days
            )

            if is_stale:
                print(
                    f"  SKIP (stale – file not found): {local_path}{age_str}\n"
                    f"    → This pending entry is older than {max_age_days} day(s) and "
                    "its file is no longer on disk.\n"
                    f"    → It is likely a leftover from a previous crawl run. "
                    "Marking as stale error."
                )
                error_msg = (
                    f"Stale manifest entry: file not found after {max_age_days}+ days "
                    f"(crawled_at: {crawled_at}). "
                    "The file was probably downloaded in a previous run whose "
                    "crawled_files directory is no longer available."
                )
            else:
                print(
                    f"  SKIP (file not found): {local_path}{age_str}\n"
                    f"    → The file is listed as pending in the manifest but is not "
                    "present on disk.\n"
                    f"    → This may indicate a failed download, an incomplete artifact "
                    "transfer, or a stale manifest entry from a prior crawl run."
                )
                error_msg = (
                    f"File not found: {local_path}{age_str}. "
                    "Possible causes: failed download, incomplete artifact transfer, "
                    "or stale manifest entry from a previous run."
                )

            entries = mark_error(entries, url, [error_msg])
            save_manifest(entries, manifest_path)
            file_not_found_count += 1
            if is_stale:
                stale_count += 1
            continue

        # Skip non-PDF files – pikepdf can only analyse PDF documents.
        if local_path.suffix.lower() != ".pdf":
            ext = local_path.suffix or "(no extension)"
            print(f"  SKIP (not a PDF – {ext}): {url}")
            entries = mark_error(entries, url, [f"Not a PDF file: {ext}"])
            save_manifest(entries, manifest_path)
            if not keep_files:
                try:
                    local_path.unlink()
                    print(f"    → Deleted local file: {local_path}")
                except OSError as exc:
                    print(f"    → Could not delete {local_path}: {exc}")
            skipped_count += 1
            continue

        # Skip files that exceed the size limit.
        file_size_bytes = local_path.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        if file_size_mb > max_file_size_mb:
            print(
                f"  SKIP (file too large – {file_size_mb:.1f} MB > {max_file_size_mb:.0f} MB limit): {url}"
            )
            entries = mark_error(
                entries,
                url,
                [f"File too large to analyse: {file_size_mb:.1f} MB (limit: {max_file_size_mb:.0f} MB)"],
            )
            save_manifest(entries, manifest_path)
            if not keep_files:
                try:
                    local_path.unlink()
                    print(f"    → Deleted local file: {local_path}")
                except OSError as exc:
                    print(f"    → Could not delete {local_path}: {exc}")
            skipped_count += 1
            continue

        # Enforce the per-run file analysis limit.
        if max_files is not None and files_analysed_count >= max_files:
            print(
                f"  STOP: reached the --max-files limit of {max_files} "
                f"analysed file(s). Remaining pending entries will be "
                "processed in the next run."
            )
            break

        print(f"  Checking: {url}")
        print(f"    File: {local_path}  [{file_size_mb:.2f} MB]")
        t_start = time.monotonic()

        try:
            report = _analyse_with_process_timeout(
                str(local_path), site, per_file_timeout, run_verapdf
            )

            elapsed = time.monotonic() - t_start
            log_msg = report.pop("_log", "")
            errors = [log_msg] if log_msg else []
            entries = mark_analysed(entries, url, report, errors)

            # Print per-check results for transparency
            checks = {
                "Tagged":    report.get("TaggedTest"),
                "EmptyText": report.get("EmptyTextTest"),
                "Protected": report.get("ProtectedTest"),
                "Title":     report.get("TitleTest"),
                "Language":  report.get("LanguageTest"),
                "Bookmarks": report.get("BookmarksTest"),
            }
            check_str = " | ".join(
                f"{name}: {val if val is not None else '—'}"
                for name, val in checks.items()
            )
            print(f"    Checks: {check_str}")
            if run_verapdf and "veraPDF" in report:
                vp = report["veraPDF"]
                if vp is None:
                    vp_str = "veraPDF: not available (not on PATH)"
                elif vp.get("error"):
                    vp_str = f"veraPDF: error – {vp['error']}"
                else:
                    label = (
                        "Pass" if vp.get("compliant") is True
                        else "Fail" if vp.get("compliant") is False
                        else "—"
                    )
                    failed = vp.get("failed_checks", "?")
                    passed = vp.get("passed_checks", "?")
                    vp_str = (
                        f"veraPDF (PDF/UA-1): {label} "
                        f"(failed: {failed}, passed: {passed})"
                    )
                print(f"    {vp_str}")
            print(f"    Elapsed: {elapsed:.1f}s")

            if report.get("BrokenFile"):
                status = "broken file"
                broken_count += 1
            elif report.get("Accessible"):
                status = "accessible"
                accessible_count += 1
            else:
                status = "issues found"
                if log_msg:
                    # log_msg entries are comma-separated (e.g. "title, tagged, ")
                    status += f" ({log_msg.strip().rstrip(',')})"
                issues_count += 1
            print(f"    → {status}")
        except TimeoutError as exc:
            elapsed = time.monotonic() - t_start
            entries = mark_error(entries, url, [str(exc)])
            print(f"    → TIMEOUT after {elapsed:.1f}s: {exc}")
            error_count += 1
        except Exception as exc:
            elapsed = time.monotonic() - t_start
            entries = mark_error(entries, url, [str(exc)])
            print(f"    → ERROR after {elapsed:.1f}s: {exc}")
            error_count += 1

        save_manifest(entries, manifest_path)
        files_analysed_count += 1

        if not keep_files:
            try:
                local_path.unlink()
                print(f"    → Deleted local file: {local_path}")
            except OSError as exc:
                print(f"    → Could not delete {local_path}: {exc}")

    print(
        f"Analysis complete. "
        f"Accessible: {accessible_count}, "
        f"Issues found: {issues_count}, "
        f"Broken: {broken_count}, "
        f"Errors: {error_count}, "
        f"File not found: {file_not_found_count} (stale: {stale_count}), "
        f"Skipped (non-PDF or too large): {skipped_count}."
    )
    if file_not_found_count > 0:
        entry_word = "entry" if file_not_found_count == 1 else "entries"
        print(
            f"\n  ⚠ {file_not_found_count} pending manifest {entry_word} had no "
            "corresponding file on disk.\n"
            "  This typically means those PDFs were recorded in the manifest during "
            "a previous crawl run\n"
            "  but their files are no longer available (e.g. the GitHub Actions "
            "artifact has expired,\n"
            "  the crawl was interrupted, or the download failed).\n"
            "  To suppress these warnings for old entries, re-run with "
            "--max-age-days N."
        )
    if stale_count > 0:
        stale_word = "entry" if stale_count == 1 else "entries"
        print(
            f"\n  ♻ {stale_count} stale manifest {stale_word} found (pending files "
            f"older than {max_age_days} day(s) with no local copy).\n"
            "  These entries are from a previous crawl whose downloaded files are no "
            "longer available.\n"
            "  Re-crawl the site to refresh them."
        )

    # Write stale count to a file so the calling workflow can check it.
    try:
        import pathlib
        pathlib.Path(STALE_COUNT_FILE).write_text(str(stale_count))
    except OSError:
        pass

    return stale_count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyse PDFs for accessibility issues")
    parser.add_argument(
        "--manifest",
        default="reports/manifest.yaml",
        help="Path to the YAML manifest (default: reports/manifest.yaml)",
    )
    parser.add_argument(
        "--crawled-dir",
        default="crawled_files",
        help="Root directory where crawled files are stored (default: crawled_files)",
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Do not delete local PDF files after analysis",
    )
    parser.add_argument(
        "--site",
        default=None,
        help="Only analyse entries for this site/domain (e.g. energy.gov)",
    )
    parser.add_argument(
        "--max-file-size",
        type=float,
        default=200.0,
        help="Skip files larger than this size in MB (default: 200)",
    )
    parser.add_argument(
        "--per-file-timeout",
        type=int,
        default=120,
        help="Maximum seconds to spend analysing a single file (default: 120; POSIX only)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help=(
            "Mark pending entries whose local file is not found and whose "
            "crawled_at date is older than this many days as stale errors "
            "(default: disabled). Useful for clearing out stale manifest "
            "entries from previous crawl runs."
        ),
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help=(
            "Stop after analysing this many PDF files (default: unlimited). "
            "Entries skipped as file-not-found, non-PDF, or oversized do not "
            "count toward this limit. Useful for bounding run time on sites "
            "with a large number of pending entries."
        ),
    )
    parser.add_argument(
        "--total-timeout",
        type=int,
        default=None,
        help=(
            "Stop analysis after this many seconds of total wall-clock time "
            "(default: unlimited). Remaining entries stay pending and will be "
            "processed on the next run. Useful for staying within CI time "
            "limits so that report generation can still complete in the same job."
        ),
    )
    parser.add_argument(
        "--verapdf",
        action="store_true",
        help=(
            "Run veraPDF (PDF/UA-1) against each PDF in addition to the built-in "
            "checks. Results are stored in the manifest under the 'veraPDF' key. "
            "Requires veraPDF to be installed and available on PATH; "
            "see https://verapdf.org/. Has no effect when veraPDF is absent."
        ),
    )
    args = parser.parse_args()
    main(
        manifest_path=args.manifest,
        crawled_dir=args.crawled_dir,
        keep_files=args.keep_files,
        site_filter=args.site,
        max_file_size_mb=args.max_file_size,
        per_file_timeout=args.per_file_timeout,
        max_age_days=args.max_age_days,
        max_files=args.max_files,
        total_timeout=args.total_timeout,
        run_verapdf=args.verapdf,
    )
