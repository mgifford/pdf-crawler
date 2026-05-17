@ui @accessibility
Feature: Submit scan requests from the web form
  To request scans reliably
  As a requester
  I want clear validation and predictable issue creation behavior

  @smoke @us-001
  Scenario: Valid URL can be prepared for SCAN issue submission
    Given I open the PDF crawler form
    When I enter "https://example.com" in the site URL field
    Then I should see a validation message containing "Valid URL"

  @smoke @us-002
  Scenario: Direct PDF URLs are blocked before submit
    Given I open the PDF crawler form
    When I enter "https://example.com/file.pdf" in the site URL field
    Then I should see a validation message containing "direct link to a PDF"

  @smoke @us-002
  Scenario: Private and localhost URLs are blocked
    Given I open the PDF crawler form
    When I enter "http://localhost/internal" in the site URL field
    Then I should see a validation message containing "Private / localhost URLs are not allowed"

