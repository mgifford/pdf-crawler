# BDD test scaffolding

This directory documents executable behavior-driven tests for this repository.

## Execution model

- Canonical behavior specs live in `/home/runner/work/pdf-crawler/pdf-crawler/features/`.
- UI scenarios (`@ui`) run through Cucumber + Playwright step definitions in:
  - `/home/runner/work/pdf-crawler/pdf-crawler/features/step_definitions/hooks.js`
  - `/home/runner/work/pdf-crawler/pdf-crawler/features/step_definitions/ui_steps.js`
- Domain behavior remains executable through existing pytest suites in `/home/runner/work/pdf-crawler/pdf-crawler/tests/`.

## Commands

- Smoke UI BDD:
  - `npm run bdd:ui:smoke`
- Full UI BDD:
  - `npm run bdd:ui:all`
- Full domain tests:
  - `python -m pytest tests/ -v`

