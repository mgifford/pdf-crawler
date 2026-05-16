# reports

This directory stores the outputs produced by the crawl and analysis workflows.

| File | Description |
|------|-------------|
| `manifest.yaml` | YAML tracking file – one entry per discovered PDF with URL, MD5 hash, crawl timestamp, and accessibility results |
| `report.md` | Human-readable Markdown report generated from the manifest |
| `report.json` | Machine-readable JSON report |
| `report_structured.json` | Structured JSON report with rule categories (`Summary`, `Detailed Report`, `PDF Metadata`) for each analysed file |

## manifest.yaml schema

```yaml
- url: https://example.com/document.pdf
  md5: d41d8cd98f00b204e9800998ecf8427e
  filename: document.pdf
  site: example.com
  crawled_at: "2024-01-15T10:30:00+00:00"
  status: analysed          # pending | analysed | error
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
