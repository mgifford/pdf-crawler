"""Regression checks for analyse workflow auto-continue wiring.

These tests validate critical snippets in `.github/workflows/analyse.yml`
so partial-scan continuation does not regress silently.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
ANALYSE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "analyse.yml"


def _workflow_text() -> str:
    return ANALYSE_WORKFLOW.read_text(encoding="utf-8")


def test_workflow_dispatch_inputs_include_auto_continue_fields():
    """workflow_dispatch must accept issue metadata for continuation runs."""
    content = _workflow_text()
    assert "issue_number:" in content
    assert "continue_attempt:" in content
    assert "scan_language:" in content


def test_meta_step_reads_dispatch_continue_attempt():
    """Read scan metadata step must parse and export continue_attempt."""
    content = _workflow_text()
    assert "DISPATCH_CONTINUE_ATTEMPT" in content
    assert "continue_attempt=$CONTINUE_ATTEMPT" in content


def test_auto_continue_step_dispatches_follow_up_analysis():
    """Partial scans should dispatch analyse.yml with incremented attempt."""
    content = _workflow_text()
    assert "name: Auto-continue partial scan" in content
    assert "workflow_id: 'analyse.yml'" in content
    assert "continue_attempt: String(nextAttempt)" in content
    assert "MAX_CONTINUE_ATTEMPTS" in content
    assert 'MAX_CONTINUE_ATTEMPTS: "5"' in content


def test_issue_closure_allows_dispatch_runs_with_issue_number():
    """Close-issue step should no longer require workflow_run events."""
    content = _workflow_text()
    close_step_header = "- name: Close issue after scan and report"
    assert close_step_header in content
    close_step_chunk = content.split(close_step_header, 1)[1].split("- name:", 1)[0]
    assert "steps.meta.outputs.issue_number != ''" in close_step_chunk
    assert "github.event_name == 'workflow_run'" not in close_step_chunk
