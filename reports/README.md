# reports

This directory stores the outputs produced by the crawl and analysis workflows.

| File | Description |
|------|-------------|
| `manifest.yaml` | YAML tracking file – one entry per discovered PDF with URL, MD5 hash, crawl timestamp, and accessibility results |
| `report.md` | Human-readable Markdown report generated from the manifest |
| `report.json` | Machine-readable JSON report |

## manifest.yaml schema

```yaml
- url: https://example.com/document.pdf
  md5: d41d8cd98f00b204e9800998ecf8427e
  filename: document.pdf
  site: example.com
  crawled_at: "2024-01-15T10:30:00+00:00"
  status: analysed          # pending | analysed | error
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
  errors:
    - "title, lang, tagged, "
```
