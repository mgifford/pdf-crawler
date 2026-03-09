# Accessibility Commitment (ACCESSIBILITY.md)

## 1. Our Commitment

We believe accessibility is a subset of quality. **pdf-crawler** is a tool
that *evaluates* PDFs for accessibility defects — it does not produce PDFs.
This project commits to **WCAG 2.2 AA** standards for:

- the [GitHub Pages submission form](https://mgifford.github.io/pdf-crawler/)
  that users interact with directly, and
- the accessibility checks applied to every PDF the tool analyses.

We track our progress publicly to remain accountable to our users.

## 2. Real-Time Health Metrics

| Metric | Status / Value |
| :--- | :--- |
| **Open A11y Issues** | [View open accessibility issues](https://github.com/mgifford/pdf-crawler/labels/accessibility) |
| **Automated Test Pass Rate** | Monitored via GitHub Actions CI |
| **A11y PRs Merged (MTD)** | Tracked in [project insights](https://github.com/mgifford/pdf-crawler/pulse) |
| **Browser Support** | Last 2 major versions of Chrome, Firefox, and Safari |

## 3. Scope

### 3.1 Web Interface (`docs/index.html`)

The GitHub Pages form is the primary human-facing surface of this project.
It must meet WCAG 2.2 AA for all interactive controls, status messages, and
error handling (see [§6 – Form Accessibility](#6-form-accessibility)).

### 3.2 PDF Accessibility Checks (`scripts/pdf_analyser.py`)

The analyser checks each crawled PDF for the following issues:

| Check | WCAG SC | Description |
|---|---|---|
| `TaggedTest` | – | Is the document tagged with a logical structure tree? |
| `EmptyTextTest` | 1.4.5 | Does the document contain real text (not just images)? |
| `ProtectedTest` | – | Is the document free of restrictions that block assistive technologies? |
| `TitleTest` | 2.4.2 | Does the document have a title with `DisplayDocTitle` set? |
| `LanguageTest` | 3.1.1 | Does the document declare a valid default language? |
| `BookmarksTest` | 2.4.1 | For documents > 20 pages, does the document have bookmarks? |
| `Form` / `xfa` | – | Does the document contain an AcroForm or dynamic XFA form? |

PDFs containing AcroForm or dynamic XFA form fields are flagged for additional
manual review; automated checks alone cannot confirm form accessibility.

## 4. Contributor Requirements (The Guardrails)

To contribute to this repo you must follow these guidelines:

- **Documentation:** All user-facing documentation must use plain language and
  follow accessibility best practices.
- **Web form changes:** Any change to `docs/index.html` must pass the
  [Form Accessibility checklist](#6-form-accessibility) below.
- **PDF check changes:** New or modified accessibility checks in
  `scripts/pdf_analyser.py` must reference the relevant WCAG success criterion
  or PDF/UA requirement.
- **Inclusive Language:** Use person-centred, respectful language throughout.
- **Link Validation:** All documentation links must resolve correctly.

## 5. Reporting & Severity Taxonomy

Please use our
[issue templates](https://github.com/mgifford/pdf-crawler/issues/new) when
reporting issues. We prioritise based on:

- **Critical:** A barrier that prevents users with disabilities from submitting
  a crawl request or accessing reports.
- **High:** A significant gap in the PDF accessibility checks (e.g. a
  well-known WCAG failure that the tool silently ignores).
- **Medium:** Incomplete guidance, unclear error messages, or partial coverage
  of a WCAG success criterion.
- **Low:** Minor improvements, typos, or enhancements to reports.

## 6. Form Accessibility

The submission form at `docs/index.html` is the primary entry point for users.
The following requirements apply to *any* change that touches the web form.

### 6.1 Labels and Instructions

- Every form control must have a programmatically associated `<label>`.
- Placeholder text must not be used as the *only* label.
- Required fields must be identified in text, not by colour alone.
  The `required` HTML attribute must be present so that assistive technologies
  can announce the required state.
- Concise instructions must appear *before* each input group.

### 6.2 Grouping and Structure

- Related controls must use `<fieldset>` and `<legend>`.
- Visual and semantic grouping must remain aligned.
- Headings (`<h2>`, etc.) must be used to separate distinct sections.

### 6.3 Input Purpose and Autocomplete

- Use the most specific `type` attribute available (`url`, `email`, `tel`,
  `number`, `date`) so browsers and assistive technologies can provide
  appropriate support.
- Do not block paste or standard keyboard shortcuts in input fields.

### 6.4 Validation and Error Messaging

- Validate on submit; avoid disruptive real-time validation.
- Error messages must be specific and actionable (state *what* is wrong and
  *how* to fix it).
- Error text must be programmatically associated with the invalid field via
  `aria-describedby`.
- Mark invalid controls with `aria-invalid="true"`.
- Do not rely on colour alone to indicate an error state.

### 6.5 Status and Live Regions

- Use `aria-live="polite"` for non-critical status updates (e.g. the URL
  validation preview already present in `docs/index.html`).
- Use `aria-live="assertive"` only for blocking failures that require
  immediate attention.
- Submission confirmation and async feedback must be announced to assistive
  technologies.

### 6.6 Error Summary Pattern

For forms with multiple fields:

- Show an error summary near the top of the form after a failed submit.
- Move keyboard focus to the error summary after a failed submit.
- Each item in the error summary must link to the corresponding invalid field.

### 6.7 Keyboard and Assistive Technology Requirements

- All form controls and interactive elements must be fully operable by
  keyboard alone (Tab, Shift+Tab, Enter, Space, arrow keys).
- Focus must never be trapped unexpectedly.
- Focus order must follow a logical reading sequence.

### 6.8 Time Limits

- If a session timeout is introduced in future, users must be warned before
  expiry and given a way to extend their session.
- Any data entered in the form must be preserved where safe and feasible.

### 6.9 Definition of Done – Form Changes

A form change is complete only when:

- All controls have accessible names and roles.
- Required state is conveyed programmatically.
- Validation errors are specific, actionable, and linked to the offending
  field.
- Keyboard-only navigation completes the full submit workflow.
- A screen reader (NVDA + Firefox or VoiceOver + Safari) announces labels,
  required state, validation feedback, and submission outcome correctly.
- No blocking accessibility defects remain open.

## 7. PDF Form Evaluation

When the analyser detects an AcroForm or XFA form in a PDF (`Form: true` or
`xfa: true` in the manifest), the PDF is flagged because interactive PDF
forms carry significant accessibility requirements that automated checks
cannot fully cover.

Known limitations of automated PDF form checking:

| What automated checks can detect | What requires manual review |
|---|---|
| Presence of an AcroForm / XFA | Whether form fields have accessible names |
| Dynamic XFA `dynamicRender` value | Tab order correctness |
| Document-level tag structure | Error identification and description |
| Language declaration | Instructions for complex inputs |

Reporters and consumers of the JSON / Markdown outputs should treat
`Form: true` as a prompt to perform a manual accessibility review of the
PDF's interactive controls against
[PDF/UA-1 (ISO 14289-1)](https://www.pdfa.org/resource/iso-14289-pdfua/) and
WCAG 2.x Success Criteria 1.3.1, 2.1.1, 3.3.1, 3.3.2, and 4.1.2.

## 8. Assistive Technology Testing

Contributors are encouraged to test the web form with:

- **Screen readers:** NVDA (Windows + Firefox), JAWS (Windows + Chrome),
  VoiceOver (macOS/iOS + Safari), TalkBack (Android + Chrome)
- **Keyboard-only navigation:** Tab, Shift+Tab, Enter, Space, arrow keys
- **Magnification:** Browser zoom to 200 % and 400 %
- **Voice control:** Dragon NaturallySpeaking, Windows Voice Access

## 9. Machine-Readable Standards

This project leverages
[wai-yaml-ld](https://github.com/mgifford/wai-yaml-ld) for machine-readable
accessibility standards, enabling AI agents to provide standards-grounded
guidance.

Relevant specifications:

- [WCAG 2.2 (YAML)](https://github.com/mgifford/wai-yaml-ld/blob/main/kitty-specs/001-wai-standards-yaml-ld-ingestion/research/wcag-2.2-normative.yaml) – normative WCAG 2.2 content including form-related success criteria
- [ARIA Informative (YAML)](https://github.com/mgifford/wai-yaml-ld/blob/main/kitty-specs/001-wai-standards-yaml-ld-ingestion/research/wai-aria-informative.yaml) – ARIA roles and properties for form controls
- [HTML Living Standard Accessibility (YAML)](https://github.com/mgifford/wai-yaml-ld/blob/main/kitty-specs/001-wai-standards-yaml-ld-ingestion/research/html-living-standard-accessibility.yaml) – HTML form element accessibility

## 10. Known Limitations

- Automated PDF checks cover a subset of WCAG/PDF UA requirements; a "Pass"
  result does not guarantee full accessibility.
- PDF forms (`Form: true`) require manual review — see [§7](#7-pdf-form-evaluation).
- The tool does not check PDFs for colour contrast, reading order within
  complex layouts, or meaningful alternative text for figures.
- The GitHub Pages form has no server-side component; JavaScript must be
  enabled for URL validation and crawl-request submission.

## 11. Getting Help

- **Questions:** Open a [discussion](https://github.com/mgifford/pdf-crawler/discussions)
- **Bugs or accessibility barriers:** Open an [issue](https://github.com/mgifford/pdf-crawler/issues)
- **Contributions:** See [CONTRIBUTING.md](./CONTRIBUTING.md) (if present)
- **Accommodations:** Request via the `accessibility-accommodation` label

## 12. Continuous Improvement

We regularly review and update:

- WCAG conformance as standards evolve (WCAG 2.2 → 3.0)
- PDF accessibility checks based on community feedback
- Coverage of interactive PDF form evaluation
- Inclusive language and terminology

Last updated: 2026-03-09
