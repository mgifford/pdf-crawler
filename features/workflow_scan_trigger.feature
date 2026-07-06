@workflow
Feature: Triggering crawl from issue events
  To process scan requests automatically
  As a maintainer
  I want scan-prefixed issues to map to crawl execution behavior

  @us-003
  Scenario: Scan-prefixed issue title is recognized
    Given a GitHub issue title begins with "SCAN:"
    When the crawl workflow receives the issue event
    Then the crawl job is eligible to run

  @us-003
  Scenario: Non-scan issue title does not trigger crawl
    Given a GitHub issue title does not begin with "SCAN:" or "PDF-CRAWL:"
    When the crawl workflow receives the issue event
    Then a note is posted and crawl execution is skipped

  @us-003
  Scenario: URL extraction can fall back to issue body
    Given a scan issue title without a full protocol URL
    And the issue body contains a URL field
    When crawl parameters are resolved
    Then a normalized crawl URL is produced

  @us-003
  Scenario: Issue body limits are parsed for crawl and analysis caps
    Given a scan issue body contains "Number:" and "PDFs:" limits
    When crawl parameters are resolved
    Then max pages and max PDFs values are emitted to workflow outputs

  @us-003
  Scenario: Engine selection suffix is propagated
    Given a scan issue title ends with "BLOOM"
    When crawl parameters are resolved
    Then crawler_version output is set to "bloom"

