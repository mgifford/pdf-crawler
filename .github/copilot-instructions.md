# Copilot Instructions

For a full orientation to this repository — including architecture, local setup,
testing, workflows, coding conventions, and pull-request requirements — read
**[AGENTS.md](../AGENTS.md)** in the repository root first.

---

## Key policy documents

| Document | Purpose |
|----------|---------|
| [AGENTS.md](../AGENTS.md) | Primary agent guide: architecture, setup, testing, conventions, PR checklist |
| [ACCESSIBILITY.md](../ACCESSIBILITY.md) | Accessibility policy, form requirements, WCAG checks |
| [SUSTAINABILITY.md](../SUSTAINABILITY.md) | Sustainability policy, AI usage guidelines, PR requirements |
| [README.md](../README.md) | User-facing documentation and quick-start guide |
| [reports/README.md](../reports/README.md) | Manifest schema reference |

---

## Quick rules

1. **Read AGENTS.md first.**  It explains the full architecture, how to run
   tests (`python -m pytest tests/ -v`), and what every script does.

2. **Check ACCESSIBILITY.md** before touching `docs/index.html` or
   `scripts/pdf_analyser.py`.

3. **Check SUSTAINABILITY.md** before every change.  Ask: is this change
   needed?  Choose the simplest, lowest-compute approach.  Note sustainability
   impact (`improves / neutral / regresses`) in every PR description.

4. **Run the test suite** and ensure all tests pass before submitting a PR.

5. **Disclose AI assistance** in the PR description when used.
