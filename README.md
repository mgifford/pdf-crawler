# pdf-crawler

An automated PDF accessibility crawler and analyser built on
[simplA11yPDFCrawler](https://github.com/accessibility-luxembourg/simplA11yPDFCrawler)
and deployable entirely on GitHub Pages + GitHub Actions – no server needed.

---

## What it does

1. **Crawls** a website for PDF (and other document) files using
   [Scrapy](https://scrapy.org).
2. **Maintains a YAML manifest** (`reports/manifest.yaml`) with every
   discovered file's URL, MD5 hash, and accessibility results.  Files whose
   MD5 hash has not changed since the last run are **skipped** automatically.
3. **Analyses** each pending PDF for the following accessibility issues
   (based on WCAG 2.x / EN 301 549):

   | Check | WCAG SC | Description |
   |-------|---------|-------------|
   | `TaggedTest` | – | Is the document tagged? |
   | `EmptyTextTest` | 1.4.5 | Does it contain real text (not just images)? |
   | `ProtectedTest` | – | Is it protected against assistive technologies? |
   | `TitleTest` | 2.4.2 | Does it have a title with `DisplayDocTitle` set? |
   | `LanguageTest` | 3.1.1 | Does it have a valid default language? |
   | `BookmarksTest` | 2.4.1 | For documents > 20 pages, does it have bookmarks? |

4. **Generates reports** in Markdown and JSON.
5. **Deletes the PDF files** after analysis to keep the repository small;
   only the YAML manifest is committed.

---

## Quick start

### 1 – Submit a crawl via the web form

Open the [PDF Crawler form](https://mgifford.github.io/pdf-crawler/#quick-start),
enter a URL, and click **Submit Crawl Request**.  You will be taken to GitHub
with the issue title pre-filled as `SCAN: https://…` — just click
*Submit new issue* to start the crawl.

The `SCAN:` prefix triggers the *Crawl Site for PDFs* workflow automatically.
The workflow will post a comment when the crawl starts and another comment with
the full accessibility report links when analysis is complete.

> **Note:** Issues are processed when **opened** or **reopened**.  Editing the
> issue body will not re-trigger a scan, so there is no risk of accidental
> recurring scans.  The legacy `PDF-CRAWL:` prefix is still accepted for
> backward compatibility.

#### Restarting a failed scan

If a crawl fails (the issue is labelled `scan-failed`), you can restart it by
**closing and then reopening** the issue.  The crawler will pick up the
`reopened` event and start a fresh crawl.

#### Issue lifecycle

| Label | Meaning |
|-------|---------|
| `scan-in-progress` | Crawl or analysis is currently running |
| `scan-failed` | The crawl workflow failed; reopen the issue to retry |
| `scan-complete` | Analysis finished and reports have been generated |

Issues are **automatically closed** once the accessibility report is posted.

### 2 – Submit a crawl manually

[Open a new issue](https://github.com/mgifford/pdf-crawler/issues/new)
and set the title to:

```
SCAN: https://example.com
```

### 3 – Trigger manually

Go to **Actions → 1 – Crawl Site for PDFs → Run workflow** and enter the URL
you want to crawl.

Once the crawl finishes, the *2 – Analyse PDFs for Accessibility* workflow
starts automatically.  You can also trigger it manually.

---

## Workflows

| Workflow | File | Trigger |
|----------|------|---------|
| Crawl Site for PDFs | `.github/workflows/crawl.yml` | Manual dispatch or issue opened/reopened with `SCAN:` title (legacy: `PDF-CRAWL:`) |
| Analyse PDFs for Accessibility | `.github/workflows/analyse.yml` | After crawl succeeds, or manual dispatch |

---

## Output files

| File | Description |
|------|-------------|
| `reports/manifest.yaml` | YAML tracking file – one entry per PDF |
| `reports/report.md` | Human-readable Markdown report |
| `reports/report.json` | Machine-readable JSON report |

See [`reports/README.md`](reports/README.md) for the full manifest schema.

---

## Local development

```bash
# Clone the repo
git clone https://github.com/mgifford/pdf-crawler.git
cd pdf-crawler

# Set up a Python virtual environment
python3 -m venv env
source env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Crawl a site (runs for up to 1 hour by default)
python scripts/crawl.py --url https://example.com

# Analyse the downloaded PDFs
python scripts/pdf_analyser.py

# Generate reports
python scripts/generate_report.py
```

---

## Architecture

```
pdf-crawler/
├── .github/
│   └── workflows/
│       ├── crawl.yml          # Step 1: crawl a site for PDFs
│       └── analyse.yml        # Step 2: analyse PDFs for accessibility
├── docs/
│   └── index.html             # GitHub Pages submission form
├── scripts/
│   ├── pdf_spider.py          # Scrapy spider (downloads PDF files)
│   ├── crawl.py               # Crawl wrapper + manifest update
│   ├── manifest.py            # YAML manifest management (MD5 dedup)
│   ├── pdf_analyser.py        # Accessibility checks (pikepdf-based)
│   └── generate_report.py     # Markdown + JSON report generator
├── reports/
│   ├── README.md              # Manifest schema docs
│   ├── manifest.yaml          # ← committed; grows over time
│   ├── report.md              # ← committed; regenerated each run
│   └── report.json            # ← committed; regenerated each run
├── requirements.txt           # Python dependencies
└── README.md
```

---

## Credits

- Accessibility checks are based on
  [simplA11yPDFCrawler](https://github.com/accessibility-luxembourg/simplA11yPDFCrawler)
  by [SIP Luxembourg](https://sip.gouvernement.lu/en.html) (MIT licence).
- Architecture inspired by [mgifford/open-scans](https://github.com/mgifford/open-scans).

---

## Licence

MIT
