"""scan/watch.py: how a repository changed since the baseline scan. No network."""
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scan"))
import scan_top_repos as S  # noqa: E402
import watch as W  # noqa: E402


@pytest.mark.parametrize("before, after, expected", [
    (["PRT001"], ["PRT001", "PRT002"], "newly_failing"),
    (["PRT001", "PRT002"], ["PRT001"], "checkout_fixed"),
    (["PRT001"], [], "trigger_removed"),
    (["PRT001", "PRT002"], ["PRT001", "PRT002"], "still_failing"),
    (["PRT001"], ["PRT001"], "unchanged"),
])
def test_status_follows_the_codes(before, after, expected):
    assert W.status_of({"codes": before}, {"codes": after}) == expected


def test_a_failed_scan_is_never_reported_as_fixed():
    old = {"codes": ["PRT001", "PRT002"]}
    new = {"codes": [], "error": "RuntimeError: could not download https://example.invalid/w.yml"}
    assert W.status_of(old, new) == "unreadable"


def test_raw_raises_instead_of_returning_empty_text(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(S.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="could not download"):
        S.raw("https://example.invalid/w.yml")


def test_only_a_failure_at_the_checkout_step_counts_as_evidence():
    runs = {"workflow_runs": [
        {"id": 1, "html_url": "run-1", "created_at": "2026-09-24", "conclusion": "failure",
         "head_repository": {"full_name": "someone/fork"}},
        {"id": 2, "html_url": "run-2", "created_at": "2026-08-27", "conclusion": "failure",
         "head_repository": {"full_name": "other/fork"}},
    ]}
    jobs = {1: {"jobs": [{"steps": [{"name": "Set up job", "conclusion": "success"},
                                    {"name": "Run tests", "conclusion": "failure"}]}]},
            2: {"jobs": [{"steps": [{"name": "Run actions/checkout@v4", "conclusion": "failure"}]}]}}

    def api(path):
        if "/jobs" in path:
            return jobs[int(path.split("/runs/")[1].split("/")[0])]
        return runs

    found = W.failed_fork_run("o/r", "ci.yml", api=api)
    assert found == {"run": "run-2", "at": "2026-08-27", "failed_step": "Run actions/checkout@v4"}
