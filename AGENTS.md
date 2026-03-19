# AGENTS.md – Coding Agent Guide for pdf-crawler

> This file contains orientation instructions for AI coding agents (GitHub Copilot,
> Claude, GPT, etc.) working on the **mgifford/pdf-crawler** repository.
> Read this file first before exploring or modifying any code.

---

## What this repository does

**pdf-crawler** is a serverless PDF accessibility auditor that runs entirely on
GitHub Actions + GitHub Pages.  It:

1. **Crawls** a target website with a Scrapy spider (`scripts/pdf_spider.py`) to
   discover PDF (and other document) URLs.
2. **Maintains a YAML manifest** (`reports/manifest.yaml`) with every discovered
   file's URL, MD5 hash, and accumulated accessibility results.  Files whose
   MD5 hash has not changed since the last run are skipped automatically.
3. **Analyses** each pending PDF for WCAG / EN 301 549 accessibility issues
   (`scripts/pdf_analyser.py`).
4. **Generates** Markdown and JSON reports (`scripts/generate_report.py`).
5. **Publishes** results to GitHub Pages (`docs/`).

Users submit scan requests by opening a GitHub issue with the title
`SCAN: https://example.com`.  The crawl workflow fires automatically, then
chains into the analysis workflow.

---

## Repository layout

```
pdf-crawler/
├── .github/
│   ├── copilot-instructions.md   # points here and to policy docs
│   ├── ISSUE_TEMPLATE/scan.md    # issue template for scan requests
│   └── workflows/
│       ├── crawl.yml             # Step 1 – crawl a site for PDFs
│       ├── analyse.yml           # Step 2 – analyse PDFs for accessibility
│       ├── pages.yml             # Deploy GitHub Pages
│       └── process_scan_queue.yml# Sequential queue processor
├── docs/
│   └── index.html                # GitHub Pages submission form (static HTML)
├── reports/
│   ├── README.md                 # Manifest schema documentation
│   ├── manifest.yaml             # Committed; grows over time (MD5-deduplicated)
│   ├── report.md                 # Regenerated on each run
│   └── report.json               # Regenerated on each run
├── scripts/
│   ├── pdf_spider.py             # Scrapy spider – downloads PDF files
│   ├── crawl.py                  # Crawl wrapper + manifest update
│   ├── manifest.py               # YAML manifest management (MD5 dedup)
│   ├── pdf_analyser.py           # Accessibility checks (pikepdf-based)
│   └── generate_report.py        # Markdown + JSON report generator
├── tests/                        # pytest test suite
├── ACCESSIBILITY.md              # Accessibility policy and contributor requirements
├── SUSTAINABILITY.md             # Sustainability policy and AI usage guidelines
├── requirements.txt              # Python dependencies
└── README.md                     # User-facing documentation
```

---

## How to set up locally

```bash
# Clone
git clone https://github.com/mgifford/pdf-crawler.git
cd pdf-crawler

# Create and activate a virtual environment
python3 -m venv env
source env/bin/activate   # Windows: env\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install pytest          # for running tests

# Run the test suite
python -m pytest tests/ -v
```

---

## Running the tools

```bash
# Crawl a site (up to 2500 pages by default)
python scripts/crawl.py --url https://example.com

# Limit page crawl
python scripts/crawl.py --url https://example.com --max-pages 200

# Analyse downloaded PDFs
python scripts/pdf_analyser.py

# Generate reports
python scripts/generate_report.py
```

---

## Testing

All tests live in `tests/` and use **pytest**.

```bash
python -m pytest tests/ -v
```

All tests must pass before any PR is merged.  Do not delete or weaken
existing tests.  New behaviour must be covered by a new test that follows the
patterns in `tests/`.

---

## GitHub Actions workflows

| Workflow | File | Trigger |
|----------|------|---------|
| Crawl Site for PDFs | `crawl.yml` | Issue opened/reopened with `SCAN:` title, or manual dispatch |
| Analyse PDFs for Accessibility | `analyse.yml` | After crawl succeeds, or manual dispatch |
| Deploy GitHub Pages | `pages.yml` | After analysis, or manual dispatch |
| Process Scan Queue | `process_scan_queue.yml` | Manual dispatch |

### Key workflow details

- **Crawl limits:** default `max_pages=500` (URLs visited), default
  `max_pdfs=200` (PDFs analysed).  Both are saved to `scan-meta/` for the
  analysis workflow to read.
- **Timeout handling:** `pdf_analyser.py` uses `multiprocessing.Process` with a
  fork context for per-file analysis timeouts.  `SIGALRM` was removed because
  it cannot interrupt C-extension calls in pdfminer/pikepdf.
- **Deduplication:** `manifest.py` computes an MD5 hash for each discovered
  file.  Unchanged files are skipped on subsequent runs, keeping CI compute low.

---

## Accessibility policy

See **[ACCESSIBILITY.md](./ACCESSIBILITY.md)** for the full policy.  Key rules
for agents:

- All changes to `docs/index.html` must pass the Form Accessibility checklist
  in ACCESSIBILITY.md §6.
- New or modified PDF checks in `scripts/pdf_analyser.py` must reference the
  relevant WCAG success criterion or PDF/UA requirement.
- Use person-centred, inclusive language throughout.

---

## Sustainability policy

See **[SUSTAINABILITY.md](./SUSTAINABILITY.md)** for the full policy.  Key rules
for agents:

- Before proposing any change, ask: is this change genuinely needed?  Choose
  the simplest, lowest-compute approach.
- All PRs must include a sustainability impact note:
  `improves / neutral / regresses`.
- Do not add always-on CI steps that fire unconditionally on every push.
- Gate new CI steps on relevant path filters where practical.
- No new unjustified third-party `<script>` tags or CDN dependencies in
  `docs/index.html`.
- Disclose AI assistance used in the PR description.

---

## Pull request checklist

Before opening a PR, confirm:

- [ ] `python -m pytest tests/ -v` passes (all tests green)
- [ ] No existing tests deleted or weakened
- [ ] Accessibility impact noted (see ACCESSIBILITY.md)
- [ ] Sustainability impact noted: `improves / neutral / regresses`
- [ ] No new unjustified third-party dependencies
- [ ] AI assistance disclosed if used
- [ ] If AI assistance was used, the **`README.md` § AI Disclosure** table has
      been updated to reflect any new LLM or tool involved (model name,
      provider, and purpose)

---

## Coding conventions

- **Language:** Python 3.10+
- **Style:** follow the existing code style in `scripts/`.  No formatter is
  enforced, but keep changes consistent with the surrounding code.
- **Dependencies:** declared in `requirements.txt`; add new packages only when
  strictly necessary.
- **Comments:** match the style of existing comments; do not add verbose or
  redundant comments.
- **Errors:** document any errors encountered and the workaround applied (in the
  PR description or inline if the workaround is non-obvious).

---

## Known limitations

- Automated PDF checks cover a subset of WCAG / PDF UA requirements; a "pass"
  does not guarantee full accessibility.
- PDF forms (`Form: true` in the manifest) require manual accessibility review.
- The GitHub Pages form requires JavaScript for URL validation and form
  submission.
- GitHub Actions hosted-runner region and energy mix are not user-selectable.

---

*Last updated: 2026-03-17*
