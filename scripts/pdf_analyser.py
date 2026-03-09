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

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz
import pikepdf
from pikepdf import Pdf, String
from pikepdf.models.metadata import decode_pdf_date
import dateparser
from bitstring import BitArray
from langcodes import Language, tag_parser

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


# ---------------------------------------------------------------------------
# Core check function
# ---------------------------------------------------------------------------

def check_file(filename: str, site: str = None) -> Dict[str, Any]:
    """Run all accessibility checks on *filename* and return a result dict."""
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
        "_log": "",
    }

    try:
        pdf = Pdf.open(filename)
        result["PDFVersion"] = pdf.pdf_version
        result["Pages"] = len(pdf.pages)

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

    except pikepdf.qpdf.PdfError as err:
        result["BrokenFile"] = True
        result["Accessible"] = None
        result["_log"] += f"PdfError: {err}"
    except pikepdf.qpdf.PasswordError as err:
        result["BrokenFile"] = True
        result["Accessible"] = None
        result["_log"] += f"Password protected: {err}"
    except ValueError as err:
        result["BrokenFile"] = True
        result["Accessible"] = None
        result["_log"] += f"ValueError: {err}"

    # Derived flags
    if result["TaggedTest"] == "Fail" and result["EmptyTextTest"] == "Fail":
        result["TotallyInaccessible"] = True
    if result["ProtectedTest"] == "Fail":
        result["TotallyInaccessible"] = True

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(
    manifest_path: str = "reports/manifest.yaml",
    crawled_dir: str = "crawled_files",
    keep_files: bool = False,
) -> None:
    """Analyse pending PDFs and update the manifest."""
    entries = load_manifest(manifest_path)
    pending = pending_entries(entries)

    if not pending:
        print("No pending entries in manifest – nothing to do.")
        return

    print(f"Analysing {len(pending)} pending file(s)…")

    for entry in pending:
        url = entry["url"]
        site = entry.get("site", "")
        filename = entry.get("filename", "")
        local_path = Path(crawled_dir) / site / filename

        if not local_path.exists():
            print(f"  SKIP (file not found): {local_path}")
            entries = mark_error(entries, url, [f"File not found: {local_path}"])
            save_manifest(entries, manifest_path)
            continue

        print(f"  Checking: {local_path}")
        try:
            report = check_file(str(local_path), site=site)
            log_msg = report.pop("_log", "")
            errors = [log_msg] if log_msg else []
            entries = mark_analysed(entries, url, report, errors)
            status = "accessible" if report.get("Accessible") else "issues found"
            print(f"    → {status}")
        except Exception as exc:  # pragma: no cover
            entries = mark_error(entries, url, [str(exc)])
            print(f"    → ERROR: {exc}")

        save_manifest(entries, manifest_path)

        if not keep_files:
            try:
                local_path.unlink()
                print(f"    → Deleted local file: {local_path}")
            except OSError as exc:
                print(f"    → Could not delete {local_path}: {exc}")

    print("Analysis complete.")


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
    args = parser.parse_args()
    main(
        manifest_path=args.manifest,
        crawled_dir=args.crawled_dir,
        keep_files=args.keep_files,
    )
