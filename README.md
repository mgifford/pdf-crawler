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

### 1 – Submit a crawl via GitHub Pages

Open the [PDF Crawler form](https://mgifford.github.io/pdf-crawler/), enter a
URL, and click **Submit Crawl Request**.  This opens a pre-filled GitHub issue
with the `PDF-CRAWL:` prefix, which automatically triggers the
*1 – Crawl Site for PDFs* workflow.

### 2 – Trigger manually

Go to **Actions → 1 – Crawl Site for PDFs → Run workflow** and enter the URL
you want to crawl.

Once the crawl finishes, the *2 – Analyse PDFs for Accessibility* workflow
starts automatically.  You can also trigger it manually.

---

## Workflows

| Workflow | File | Trigger |
|----------|------|---------|
| Crawl Site for PDFs | `.github/workflows/crawl.yml` | Manual dispatch or issue with `PDF-CRAWL:` title |
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
