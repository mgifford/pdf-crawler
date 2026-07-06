# FEATURES.md

This file is the single source of truth for behavior in **pdf-crawler**.

## User personas

- **Requester**: submits a scan request for a website.
- **Maintainer**: keeps workflows reliable and sustainable over time.
- **Report consumer**: reads scan output to understand accessibility risk.

## User stories and acceptance criteria

### US-001 — Submit a scan request from the web form

**As a** requester  
**I want** to submit a website URL from the GitHub Pages form  
**So that** a `SCAN:` issue can be created quickly and consistently.

Acceptance criteria:
- Valid `http`/`https` URLs show clear positive feedback before submit.
- Submit opens a GitHub new-issue page with title `SCAN: <url>`.
- Optional fields are included in the prefilled issue body when provided.

### US-002 — Block invalid or out-of-scope URL inputs

**As a** requester  
**I want** invalid/private/direct-PDF URLs blocked  
**So that** the scan system only receives actionable website requests.

Acceptance criteria:
- Invalid URL syntax shows an actionable error message.
- Private/localhost URLs are rejected.
- Direct `.pdf` URLs are rejected with guidance to submit a website URL.

### US-003 — Scan issue titles trigger crawl workflow

**As a** maintainer  
**I want** issue titles prefixed with `SCAN:` to trigger crawl  
**So that** requests are processed automatically and consistently.

Acceptance criteria:
- Issues with `SCAN:` or `PDF-CRAWL:` are treated as scan requests.
- Non-scan issue titles do not start crawl execution.
- URL extraction falls back from title to body when needed.
- Optional issue-body limits (`Number:`, `PDFs:`) are parsed into crawl/analyse caps.
- Crawler engine selection from title suffix (`BLOOM` / `ORIGINAL`) is propagated to analysis metadata.

### US-004 — Unchanged files are not rescanned

**As a** maintainer  
**I want** manifest MD5 deduplication  
**So that** compute and runtime are reduced across repeated scans.

Acceptance criteria:
- Manifest tracks URL, file identity, and processing status.
- Manifest stores both MD5 and a stable file hash to identify content changes.
- Unchanged files remain skipped on subsequent runs for the same engine.
- The same unchanged file can still be analysed by a different engine.
- Reports still include complete state for decision-making.
- Reports include engine-aware comparison data when both engines analysed the same file.

### US-005 — Reports remain human-readable and machine-readable

**As a** report consumer  
**I want** clear report formats  
**So that** I can review findings manually or automate downstream use.

Acceptance criteria:
- Markdown report remains understandable for non-technical users.
- JSON reports remain available for integrations.
- Structured report output remains compatibility-safe.

## Canonical Gherkin coverage

All behavior specs live in `features/`.

Rules:
- Every scenario must include exactly one user-story tag (`@us-001`, etc.).
- Use scope tags from this set: `@ui`, `@workflow`, `@reporting`, `@accessibility`, `@smoke`.
- Keep language business-focused; avoid implementation details.

## Traceability matrix

| User story | Gherkin scenario(s) | Existing pytest coverage | Playwright/Cucumber coverage | Workflow / owner surface |
|---|---|---|---|---|
| US-001 | `features/ui_scan_submission.feature` → “Valid URL can be prepared for SCAN issue submission” | `tests/test_crawl.py` (URL normalization supports request quality) | `features/ui_scan_submission.feature` (`@ui`) | `docs/index.html`, `.github/workflows/crawl.yml` |
| US-002 | `features/ui_scan_submission.feature` → “Direct PDF URLs are blocked before submit”; “Private and localhost URLs are blocked” | `tests/test_crawl.py` (`is_pdf_url` coverage) | `features/ui_scan_submission.feature` (`@ui @accessibility`) | `docs/index.html`, `.github/workflows/crawl.yml` |
| US-003 | `features/workflow_scan_trigger.feature` scenarios | `tests/test_crawl.py`, `tests/test_crawl_workflow_config.py` | Planned BDD step expansion (currently documented spec) | `.github/workflows/crawl.yml` |
| US-004 | `features/reporting_manifest_behavior.feature` scenarios | `tests/test_manifest.py`, `tests/test_crawl.py`, `tests/test_generate_report_engine_comparison.py` | N/A (domain covered by pytest) | `scripts/manifest.py`, `scripts/crawl.py`, `scripts/pdf_analyser.py`, `scripts/generate_report.py` |
| US-005 | `features/reporting_manifest_behavior.feature` scenarios | `tests/test_generate_report.py`, `tests/test_structured_report.py` | N/A (domain covered by pytest) | `scripts/generate_report.py`, `reports/` |

## Governance for maintainability

Any change to expected behavior must update:
1. A user story in this file.
2. At least one Gherkin scenario with user-story tag.
3. At least one executable test (pytest or Cucumber/Playwright).

Review checklist:
- Reuse existing step vocabulary before adding new phrases.
- Keep scenarios readable by non-technical stakeholders.
- Keep CI compute low using path filters and smoke/full split.
