# reports

This directory stores the outputs produced by the crawl and analysis workflows.

| File | Description |
|------|-------------|
| `manifest.yaml` | YAML tracking file – one entry per discovered PDF with URL, hashes, crawl timestamp, and engine-specific accessibility results |
| `report.md` | Human-readable Markdown report generated from the manifest |
| `report.json` | Machine-readable JSON report |
| `report_structured.json` | Structured JSON report with rule categories (`Summary`, `Detailed Report`, `PDF Metadata`) for each analysed file |

## manifest.yaml schema

```yaml
- url: https://example.com/document.pdf
  md5: d41d8cd98f00b204e9800998ecf8427e
  file_hash: 8f434346648f6b96df89dda901c5176b10a6d83961db1f4c3d410a8f1f6677f2
  filename: document.pdf
  site: example.com
  crawled_at: "2024-01-15T10:30:00+00:00"
  status: analysed          # pending | analysed | error
```

**Status values:**

| Value | Meaning |
|-------|---------|
| `pending` | Discovered but not yet analysed.  If the downloaded PDF file is no longer available (e.g. the GitHub Actions artifact expired after 90 days or the crawl was interrupted), the entry is counted as *stale* and skipped for that analysis pass.  Stale entries are cleared on the next successful crawl. |
| `analysed` | Successfully analysed; results are stored in `report`. |
| `error` | Analysis was attempted but failed; details are in `errors`. |

```yaml
  link_text: "Annual Report 2023"   # optional – anchor text of the link that pointed to this PDF
  report:
    Accessible: false
    TotallyInaccessible: true
    BrokenFile: false
    TaggedTest: Fail
    EmptyTextTest: Fail
    ProtectedTest: Pass
    TitleTest: Fail
    LanguageTest: Fail
    BookmarksTest: Pass
    Exempt: false
    Date: "2017-03-01 00:00:00+00:00"
    hasTitle: false
    hasDisplayDocTitle: null
    hasLang: false
    InvalidLang: null
    Form: null
    xfa: null
    hasBookmarks: true
    hasXmp: true
    PDFVersion: "1.4"
    Creator: "Microsoft Word"
    Producer: "Adobe PDF Library"
    Pages: 5
    DocCategory: Report     # optional – inferred document category (see below)
  errors:
    - "title, lang, tagged, "
  analyses:
    original:
      status: analysed
      analysed_at: "2024-01-15T10:40:00+00:00"
      report: { ... }
      errors: []
    bloom:
      status: pending
      analysed_at: null
      report: null
      errors: []
```

### `link_text` field

The `link_text` field records the visible text of the HTML anchor tag (`<a>`) that
linked to the PDF, captured by the spider during crawling.  It is omitted when no
anchor text was found (e.g. an image-only link, or when the PDF was detected via
`Content-Type` rather than a `.pdf` file extension).

This field is used by the analyser when classifying the document category (see
`DocCategory` below), and may be useful for manual audit prioritisation.

### `DocCategory` field

The `DocCategory` field is an optional report key that records a rule-based
document category inferred from the PDF's URL path, filename, and anchor-link
text.  It is omitted when no category can be determined.

The classifier uses the same category labels as Code for America's
[asap_pdf](https://github.com/codeforamerica/asap_pdf) project, with two
additions that are common in government document audits:

| Category | Description |
|----------|-------------|
| `Agenda` | Meeting agenda |
| `Minutes` | Meeting minutes *(additional)* |
| `Budget` | Budget or financial document *(additional)* |
| `Policy` | Policy, procedure, guideline, ordinance, resolution, or by-law |
| `Procurement` | RFP, RFI, RFQ, bid, tender, contract, or vendor proposal |
| `Form` | Application form, registration, or enrolment document |
| `Job` | Job posting, vacancy, or recruitment document |
| `Notice` | Public notice, notification, advisory, or bulletin |
| `Press` | Press release or media release |
| `Slides` | Presentation or slide deck |
| `Brochure` | Brochure, pamphlet, leaflet, or fact sheet |
| `Report` | Report, assessment, audit, review, study, or analysis |

The category is determined by keyword matching and is a best-effort
classification — it should be treated as a hint rather than a definitive label.
For ML-powered classification, see
[asap_pdf](https://github.com/codeforamerica/asap_pdf).

### Hashes and engine-specific analyses

- `md5` is retained for backward compatibility and lightweight change detection.
- `file_hash` stores a SHA-256 digest to uniquely identify file content across runs.
- `analyses` stores per-engine state (`original`, `bloom`, etc.).

This allows the same unchanged PDF to be skipped for an engine that has already
analysed it, while still allowing analysis by another engine for comparison.
