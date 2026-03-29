---
sustainability:
  standard: WSG 1.0

  applies_to:
    - github_actions_workflows
    - python_scripts
    - github_pages_web_interface

  ownership:
    sustainability_lead: "@mgifford"
    engineering_owner: "@mgifford"

  automated_tools:
    - lighthouse
  ci_pipeline: .github/workflows/analyse.yml

  budgets:
    page_weight_budget_kb: 100
    request_count: 10

  release_gates:
    - ci sustainability checks pass
    - no new unjustified third-party scripts
    - no new critical accessibility regressions

  known_limitations: "#known-limitations"
---

# Sustainability Commitment (SUSTAINABILITY.md)

> **AI disclosure:** This file was drafted with AI-assisted support (GitHub Copilot), with
> human review and editing applied throughout.

## Status and ownership

| Field | Value |
| :--- | :--- |
| **Status** | Active |
| **Sustainability lead** | @mgifford |
| **Engineering owner** | @mgifford |
| **Last updated** | 2026-03-11 |
| **Review cadence** | Quarterly |

## Team commitment

We commit to reducing the digital footprint of **pdf-crawler** by treating sustainability
as a quality attribute alongside accessibility, reliability, and security. This means
making deliberate choices about compute, data transfer, and third-party dependencies at
every stage of development and operation.

We optimize for measurable improvement over perfection.

## Scope

This policy applies to:

- **Repository:** `mgifford/pdf-crawler`
- **GitHub Pages web interface:** `docs/index.html`
- **GitHub Actions workflows:** `crawl.yml`, `analyse.yml`, `pages.yml`, `process_scan_queue.yml`, `rescue_abandoned_scans.yml`, `a11y-scan.yml`
- **Python scripts:** `scripts/` directory
- **Third-party services:** GitHub Actions hosted runners, GitHub Pages CDN

Out of scope for now:

- Downstream systems that host PDFs being crawled (outside our control)
- GitHub infrastructure energy mix (not user-selectable for hosted runners)

## Sustainability in early ideation

*Aligned with [WSG 2.8 – Integrate Sustainability Into Every Stage of the Ideation Process](https://www.w3.org/TR/web-sustainability-guidelines/#integrate-sustainability-into-every-stage-of-the-ideation-process).*

### Questions to ask before building

- Is this feature or change genuinely needed? What is the cost if we skip it?
- What is the expected compute, bandwidth, and storage footprint of this change?
- Can a simpler, lower-footprint approach meet the same need?
- Does this add third-party dependencies? Are they justified?

### Questions to ask before merging

- Does this change increase CI compute time unnecessarily?
- Are new third-party scripts or services reviewed and justified?
- Is AI assistance disclosed when used?

### For AI agents (WSG 2.8)

Before proposing any change, ask: is this change needed at all? If yes, what is the
simplest implementation that meets the requirement? Note expected sustainability impact
(improves / neutral / regresses) in every PR description.

## Baseline metrics

| Metric | Baseline | Target | Owner | Check cadence |
| :--- | :--- | :--- | :--- | :--- |
| Core page weight (`docs/index.html`) | Not yet measured | ≤ 100 KB | @mgifford | Quarterly |
| Request count (`docs/index.html`) | Not yet measured | ≤ 10 | @mgifford | Quarterly |
| CI compute (crawl + analyse job pair) | Not yet measured | Downward trend | @mgifford | Quarterly |
| Third-party scripts | 0 (static HTML) | 0 | @mgifford | Each PR |
| AI calls per PR | Not tracked | Downward trend | @mgifford | Monthly |

How to establish baselines: run Lighthouse CI against
`https://mgifford.github.io/pdf-crawler/` and record the output to set initial thresholds.

## Pull request requirements

All pull requests should include:

- Expected sustainability impact (improves / neutral / regresses)
- Accessibility impact summary
- Third-party impact summary (new deps? new CDN calls?)
- AI assistance disclosure when used

## Accessibility as code (required checks)

*See [ACCESSIBILITY.md](./ACCESSIBILITY.md) for the full accessibility policy.*

Minimum required CI checks for each pull request touching `docs/index.html`:

- Automated accessibility testing for changed pages/components
- Keyboard and focus-state checks for interactive elements
- No new critical WCAG 2.2 AA violations introduced

## Sustainability as code (required checks)

Minimum required CI checks for each pull request:

- Python scripts must pass the existing `pytest` test suite (`python -m pytest tests/ -v`)
- No new unjustified third-party `<script>` tags, external stylesheet imports, or CDN
  dependencies in `docs/index.html`
- No new unconditional always-on CI steps that fire on every push regardless of what changed

Suggested workflow policy:

- Gate CI steps on changed paths where practical (e.g., run PDF analysis checks only when
  `scripts/pdf_analyser.py` changes).
- Require explicit justification for any increase to the CI job matrix or step count.

## AI usage policy

### Default behavior

Apply this decision order before using AI for any task:

1. **Deterministic code first**: Can a script, linter, or static rule handle this? Write or configure it.
2. **Existing tooling**: Is there a CLI tool or library that already covers this? Use it.
3. **Caching**: Can the result be precomputed and reused without re-running? Cache it.
4. **Reduced frequency**: Can this run less often or only when relevant inputs change? Limit it.
5. **Human action**: Is this infrequent enough that a person can handle it directly? Do it manually.
6. **AI, only when justified**: Use AI only when the above are impractical and AI clearly reduces total lifecycle cost.

Only run a process if its output will be consumed. Gate CI steps on relevant path filters
rather than running unconditionally on every push.

This project already applies this principle via the MD5-based manifest deduplication: PDFs
whose content has not changed since the last crawl are **skipped automatically**, avoiding
redundant compute and bandwidth.

### Allowed uses

- Drafting and summarization where deterministic automation is unavailable
- One-time migration or refactoring support
- Triage and analysis tasks that reduce repeated manual work

### Restricted uses

- No always-on AI generation in CI for routine checks
- No large-context prompts for trivial formatting or deterministic transforms
- No AI calls when local tooling can produce equivalent output
- No automatic activation of browser built-in AI features; require explicit user opt-in

### AI controls

- Limit model size and retries where practical
- Cache reusable outputs and avoid duplicate prompts
- Track approximate AI call volume per issue/PR in the PR description
- Review monthly and set reduction goals

## AI disclosure

### In building

- Content drafting, structural editing, and documentation were assisted by AI
  (GitHub Copilot / GPT-4-class models) with human review and editing applied.
- Code suggestions and CI workflow improvements used AI assistance with human review.

### In execution

- No AI runs automatically at runtime or page load.
- The `docs/index.html` page is a static HTML form with no runtime AI features.
- GitHub Actions CI workflows do not include always-on AI generation steps.

### Models used

| Purpose | Model / tool | When used |
| :--- | :--- | :--- |
| Code assistance and PR support | GitHub Copilot (GPT-4-class) | During development |
| Content drafting and editing | OpenAI GPT-4-class via Copilot Chat | During development |

## Third-party assessment (WSG 4.10)

*Aligned with [WSG 4.10 – Give Third Parties the Same Priority as First Parties During Assessment](https://www.w3.org/TR/web-sustainability-guidelines/#give-third-parties-the-same-priority-as-first-parties-during-assessment).*

When a dependency, CDN service, analytics script, or external resource is added, require
explicit answers to:

- **Is it necessary?** Can the need be met with first-party or self-hosted code?
- **What is the transfer weight?** How many additional kilobytes does it add?
- **Where does it run?** Is the host region and energy mix known and acceptable?
- **What data does it send?** Are privacy implications acceptable?
- **What is the fallback?** Does the page degrade gracefully if it fails?

For code reviewers and AI agents: flag any new `<script src="...">`, external stylesheet
`@import`, or `<iframe>` embed as requiring the above checklist before merge.

## Time and space shifting

### Time shift

- Non-urgent batch jobs (e.g., full-site re-crawls, archival report generation) should be
  scheduled during lower-carbon windows where practical.
- Define maximum delay windows so delivery risk remains controlled.

### Space shift

- GitHub Actions hosted runners and GitHub Pages CDN regions are not user-selectable.
  This is a known limitation — see [§ Known limitations](#known-limitations).
- Use self-hosted runners for region-aware workloads if this becomes a priority.

### Current GitHub Actions constraints

- GitHub Pages deployment region is managed by GitHub/CDN and not user-pinnable.
- Exact physical region and real-time energy mix of hosted runners are not guaranteed or
  directly selectable in standard workflows.

## Governance and exceptions

- Issue labels: `sustainability`, `accessibility`, `performance-budget`, `ai-usage`,
  `third-party-impact`
- Decision owner: @mgifford
- Exception process:
  1. Open an issue with rationale, owner, and target expiry date
  2. Add a mitigation plan
  3. Revalidate before expiry

## Release gate criteria

All of the following must pass before any release ships:

- [ ] Python test suite passes (`python -m pytest tests/ -v`)
- [ ] No new unjustified third-party scripts or CDN dependencies in `docs/index.html`
- [ ] CI sustainability checks pass
- [ ] No new critical accessibility regressions (see [ACCESSIBILITY.md](./ACCESSIBILITY.md))
- [ ] AI usage disclosed if applicable

Temporary exceptions require an open issue with owner, rationale, and expiry date.

## Known limitations

Active sustainability debt for this project. Each entry has an owner and a target date.

| Issue | Status | Owner | Target date | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Green hosting status unknown — GitHub Pages CDN energy mix not published | open | @mgifford | 2026-12-31 | Monitor GitHub/Azure sustainability disclosures |
| No formal CI carbon budget established for compute time | open | @mgifford | 2026-12-31 | Run baseline measurement; set initial CI budget thresholds |
| AI call volume per PR tracked informally | open | @mgifford | 2026-12-31 | Add structured field to PR template; review monthly |
| Grid-aware serving not implemented | open | @mgifford | 2027-06-30 | Evaluate feasibility; document constraints |
| Page-weight baseline not yet measured | open | @mgifford | 2026-06-30 | Run Lighthouse CI against GitHub Pages URL to set initial baseline |

## Machine-readable standards

This project leverages
[wai-yaml-ld](https://github.com/mgifford/wai-yaml-ld) for machine-readable accessibility
and sustainability standards, enabling AI agents to provide standards-grounded guidance.

Relevant specifications:

- [Web Sustainability Guidelines 1.0](https://www.w3.org/TR/web-sustainability-guidelines/) – primary sustainability standard
- [WCAG 2.2 (YAML)](https://github.com/mgifford/wai-yaml-ld/blob/main/kitty-specs/001-wai-standards-yaml-ld-ingestion/research/wcag-2.2-normative.yaml) – normative WCAG 2.2 content

## References

- Web Sustainability Guidelines (WSG) 1.0: https://www.w3.org/TR/web-sustainability-guidelines/
- Sustainable Web Design: https://sustainablewebdesign.org/
- Green Web Foundation CO2.js: https://github.com/thegreenwebfoundation/co2.js
- Carbon.txt standard: https://www.thegreenwebfoundation.org/tools/carbon-txt/
- SUSTAINABILITY.md template: https://github.com/mgifford/SUSTAINABILITY.md

## AI agent instruction block

> Check `SUSTAINABILITY.md` before proposing or writing changes. At ideation, ask whether
> the change is needed and choose the simplest approach first. Prefer low-compute
> deterministic solutions. Run required accessibility and sustainability checks in CI.
> If AI is used, keep context minimal, avoid duplicate calls, and disclose usage in the PR.
> Note sustainability impact (improves / neutral / regresses) in every PR description.

Last updated: 2026-03-11
