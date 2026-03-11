"""Tests for SUSTAINABILITY.md – validate that the file exists and contains
the required policy sections and commitments."""

from pathlib import Path

import pytest
import yaml

# Path helpers
REPO_ROOT = Path(__file__).parent.parent
SUSTAINABILITY_PATH = REPO_ROOT / "SUSTAINABILITY.md"

# ---------------------------------------------------------------------------
# Required top-level sections (H2 headings) that must exist in the document.
# ---------------------------------------------------------------------------
REQUIRED_SECTIONS = [
    "## Status and ownership",
    "## Team commitment",
    "## Scope",
    "## Sustainability in early ideation",
    "## Baseline metrics",
    "## Pull request requirements",
    "## Sustainability as code",
    "## AI usage policy",
    "## AI disclosure",
    "## Third-party assessment",
    "## Known limitations",
    "## Release gate criteria",
    "## References",
]

# ---------------------------------------------------------------------------
# Required policy keywords that must appear somewhere in the document.
# These act as a lightweight regression guard to ensure key commitments are
# preserved when the file is edited.
# ---------------------------------------------------------------------------
REQUIRED_KEYWORDS = [
    "WSG",           # References the Web Sustainability Guidelines
    "pytest",        # Links sustainability to the existing test suite
    "third-party",   # Third-party assessment is a core WSG requirement
    "AI",            # AI usage policy must be present
    "accessibility", # Sustainability is paired with accessibility in this project
    "carbon",        # Carbon / emissions language must be present
    "budget",        # Budgets are a core sustainability mechanism
    "@mgifford",     # An accountable owner must be named
]


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------

def test_sustainability_file_exists():
    """SUSTAINABILITY.md must exist at the repository root."""
    assert SUSTAINABILITY_PATH.exists(), (
        "SUSTAINABILITY.md not found. "
        "Create it at the repository root following the project template."
    )


def test_sustainability_file_is_not_empty():
    """SUSTAINABILITY.md must not be an empty file."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert len(content.strip()) > 0, "SUSTAINABILITY.md is empty."


# ---------------------------------------------------------------------------
# Front-matter (YAML)
# ---------------------------------------------------------------------------

def _parse_front_matter(content: str) -> dict | None:
    """Return the YAML front-matter dict if present, else None."""
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None


def test_front_matter_is_valid_yaml():
    """If a YAML front-matter block is present it must parse without errors."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    if not content.startswith("---"):
        pytest.skip("No YAML front-matter block present.")
    fm = _parse_front_matter(content)
    assert fm is not None, "YAML front-matter block failed to parse."


def test_front_matter_has_sustainability_key():
    """Front-matter must contain a top-level 'sustainability' key."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    if not content.startswith("---"):
        pytest.skip("No YAML front-matter block present.")
    fm = _parse_front_matter(content)
    assert fm is not None, "YAML front-matter block failed to parse."
    assert "sustainability" in fm, (
        "Front-matter must contain a top-level 'sustainability' key."
    )


def test_front_matter_has_ownership():
    """Front-matter 'sustainability' block must declare at least a sustainability_lead."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    if not content.startswith("---"):
        pytest.skip("No YAML front-matter block present.")
    fm = _parse_front_matter(content)
    assert fm is not None
    s = fm.get("sustainability", {})
    assert "ownership" in s, "Front-matter must contain an 'ownership' section."
    assert "sustainability_lead" in s["ownership"], (
        "Front-matter ownership must include a 'sustainability_lead' field."
    )


# ---------------------------------------------------------------------------
# Required sections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading", REQUIRED_SECTIONS)
def test_required_section_present(heading):
    """Each required H2 heading must appear in SUSTAINABILITY.md."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert heading in content, (
        f"Required section '{heading}' is missing from SUSTAINABILITY.md."
    )


# ---------------------------------------------------------------------------
# Required keywords
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("keyword", REQUIRED_KEYWORDS)
def test_required_keyword_present(keyword):
    """Each required keyword must appear at least once in SUSTAINABILITY.md."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert keyword.lower() in content.lower(), (
        f"Required keyword '{keyword}' not found in SUSTAINABILITY.md."
    )


# ---------------------------------------------------------------------------
# Policy commitments
# ---------------------------------------------------------------------------

def test_ai_disclosure_section_has_in_building():
    """The AI disclosure section must describe usage 'In building'."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "### In building" in content or "in building" in content.lower(), (
        "AI disclosure section must include an 'In building' sub-section."
    )


def test_ai_disclosure_section_has_in_execution():
    """The AI disclosure section must describe usage 'In execution'."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "### In execution" in content or "in execution" in content.lower(), (
        "AI disclosure section must include an 'In execution' sub-section."
    )


def test_known_limitations_has_at_least_one_entry():
    """Known limitations table must contain at least one open entry."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    # The table uses markdown pipe syntax; look for at least one 'open' cell.
    assert "open" in content.lower(), (
        "Known limitations table must contain at least one open entry."
    )


def test_release_gate_has_pytest():
    """Release gate criteria must reference the pytest test suite."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "pytest" in content, (
        "Release gate criteria must mention the pytest test suite "
        "(`python -m pytest tests/ -v`)."
    )


def test_scope_mentions_github_actions():
    """Scope section must acknowledge GitHub Actions workflows."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "github actions" in content.lower() or "workflow" in content.lower(), (
        "Scope section must mention GitHub Actions workflows."
    )


def test_scope_mentions_github_pages():
    """Scope section must acknowledge the GitHub Pages web interface."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "github pages" in content.lower() or "docs/index.html" in content.lower(), (
        "Scope section must mention the GitHub Pages web interface."
    )


def test_wsg_reference_url_present():
    """Document must contain a URL reference to the Web Sustainability Guidelines."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "w3.org/TR/web-sustainability-guidelines" in content, (
        "SUSTAINABILITY.md must link to the W3C Web Sustainability Guidelines."
    )


def test_ownership_has_accountable_person():
    """Status and ownership section must name at least one accountable person (@ handle)."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "@" in content, (
        "SUSTAINABILITY.md must name at least one accountable person using an @ handle."
    )


def test_last_updated_field_present():
    """Document must contain a 'Last updated' date field."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "last updated" in content.lower(), (
        "SUSTAINABILITY.md must contain a 'Last updated' date field."
    )


def test_ai_agent_instruction_block_present():
    """An AI agent instruction block must be present to guide automated tools."""
    content = SUSTAINABILITY_PATH.read_text(encoding="utf-8")
    assert "ai agent instruction" in content.lower(), (
        "SUSTAINABILITY.md must contain an 'AI agent instruction' block for automated tools."
    )
