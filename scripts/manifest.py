"""
Manifest management for the PDF crawler.

The manifest is a YAML file that keeps track of every PDF discovered across
crawl runs.  Each entry records:

  url      – the canonical URL where the file was found
  md5      – MD5 hash of the file contents (used to detect changed files)
  filename – local filename (basename only, no directory)
  site     – originating domain
  crawled_at – ISO-8601 timestamp of the crawl that first found this file
  status   – "pending" | "analysed" | "error"
  report   – nested mapping of accessibility check results (filled by analyser)
  errors   – list of human-readable error strings (filled by analyser)

The manifest is stored at ``reports/manifest.yaml`` by default.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


DEFAULT_MANIFEST_PATH = Path("reports") / "manifest.yaml"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _md5(path: str | Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path: str | Path = DEFAULT_MANIFEST_PATH) -> List[Dict[str, Any]]:
    """Return the list of manifest entries, or an empty list if no file exists."""
    path = Path(manifest_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, list) else []


def save_manifest(
    entries: List[Dict[str, Any]],
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
) -> None:
    """Persist the manifest to disk."""
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(entries, fh, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_entry(url: str, local_path: str | Path, site: str) -> Dict[str, Any]:
    """Create a new manifest entry for *local_path*."""
    return {
        "url": url,
        "md5": _md5(local_path),
        "filename": Path(local_path).name,
        "site": site,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "report": None,
        "errors": [],
    }


def needs_analysis(entry: Dict[str, Any], local_path: str | Path) -> bool:
    """Return True if *local_path* should be (re-)analysed.

    A file needs analysis when:
    - its status is "pending", or
    - the file exists on disk and its MD5 has changed since the last crawl.
    """
    if entry.get("status") == "pending":
        return True
    path = Path(local_path)
    if path.exists() and _md5(path) != entry.get("md5"):
        return True
    return False


def update_entry_from_file(
    entry: Dict[str, Any], local_path: str | Path
) -> Dict[str, Any]:
    """Refresh the MD5 and crawled_at fields when a file has been re-downloaded."""
    entry["md5"] = _md5(local_path)
    entry["crawled_at"] = datetime.now(timezone.utc).isoformat()
    entry["status"] = "pending"
    entry["report"] = None
    entry["errors"] = []
    return entry


def upsert_entry(
    entries: List[Dict[str, Any]],
    url: str,
    local_path: str | Path,
    site: str,
) -> tuple[List[Dict[str, Any]], bool]:
    """Add or update the manifest entry for *url*.

    Returns the updated list and a boolean indicating whether the file needs
    to be analysed (True) or can be skipped (False).
    """
    for entry in entries:
        if entry.get("url") == url:
            new_md5 = _md5(local_path)
            if new_md5 != entry.get("md5"):
                # File has changed – reset for re-analysis
                update_entry_from_file(entry, local_path)
                return entries, True
            # File unchanged – only skip if it has already been successfully
            # analysed.  If the status is 'pending' or 'error', we still need
            # to (re-)analyse it even though the content has not changed.
            return entries, entry.get("status") != "analysed"

    # Brand-new entry
    entries.append(build_entry(url, local_path, site))
    return entries, True


def mark_analysed(
    entries: List[Dict[str, Any]],
    url: str,
    report: Dict[str, Any],
    errors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Record analysis results for *url* in the manifest."""
    for entry in entries:
        if entry.get("url") == url:
            entry["status"] = "analysed"
            entry["report"] = report
            entry["errors"] = errors or []
            return entries
    return entries


def mark_error(
    entries: List[Dict[str, Any]],
    url: str,
    errors: List[str],
) -> List[Dict[str, Any]]:
    """Record analysis failure for *url*."""
    for entry in entries:
        if entry.get("url") == url:
            entry["status"] = "error"
            entry["errors"] = errors
            return entries
    return entries


def pending_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return entries whose status is 'pending'."""
    return [e for e in entries if e.get("status") == "pending"]
