@reporting
Feature: Manifest and report continuity
  To keep output maintainable over time
  As a maintainer and report consumer
  I want deduplicated processing and stable output formats

  @us-004
  Scenario: Unchanged files are skipped on subsequent runs
    Given a file URL already exists in the manifest with the same MD5 hash
    When a new crawl run updates the manifest
    Then that file remains excluded from re-analysis work

  @us-004
  Scenario: Manifest preserves status and error history per file
    Given the analyser has processed files with pass and error outcomes
    When the manifest is saved
    Then each file entry retains its status and associated result fields

  @us-005
  Scenario: Reports remain available for both humans and machines
    Given analysis results are present in the manifest
    When report generation runs
    Then markdown and JSON outputs are produced consistently

