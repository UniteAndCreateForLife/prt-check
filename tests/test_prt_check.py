"""prt_check.py: what GitHub's 2026 pull_request_target changes do to a workflow.

Each case is a small workflow written the way real repositories write them.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prt_check as P  # noqa: E402


def codes(text, name="w.yml"):
    return [(f.code, f.level, f.line) for f in P.analyse(name, text).findings]


LABELER = """name: Labeler
on: pull_request_target
permissions:
  contents: read
  pull-requests: write
jobs:
  label:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/labeler@v5
"""


def test_plain_ci_is_not_affected():
    text = "on: [push, pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: make test\n"
    report = P.analyse("ci.yml", text)
    assert report.triggers == ["push", "pull_request"] and report.findings == []
    assert P.summarise([report])["verdict"] == "not_affected"


def test_the_trigger_alone_is_affected_on_november_2():
    assert codes(LABELER) == [("PRT001", "warning", 2)]
    assert P.summarise([P.analyse("l.yml", LABELER)])["verdict"] == "affected_on_2026_11_02"


@pytest.mark.parametrize("on_block, expected", [
    ("on:\n  pull_request_target:\n    types: [opened, synchronize]\n  push:\n    branches: [main]\n",
     ["pull_request_target", "push"]),
    ("on: {pull_request_target: {types: [opened]}, push: {}}\n", ["pull_request_target", "push"]),
    ("on:\n  - push\n  - pull_request_target\n", ["push", "pull_request_target"]),
    ('"on":\n  pull_request_target:\n', ["pull_request_target"]),
    ("true:\n  pull_request_target:\n", ["pull_request_target"]),
    ("on: [ push, 'pull_request_target' ]  # comment\n", ["push", "pull_request_target"]),
    ("# on: pull_request_target\non: push\n", ["push"]),
])
def test_trigger_layouts(on_block, expected):
    assert P.triggers(on_block.split("\n"))[0] == expected


def checkout_workflow(with_lines, trigger="pull_request_target", after="      - run: npm ci && npm test\n",
                      extra=""):
    return (f"on: {trigger}\n{extra}jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n        with:\n" + with_lines + after)


def test_checking_out_the_fork_head_already_fails_for_fork_prs():
    text = checkout_workflow("          ref: ${{ github.event.pull_request.head.sha }}\n")
    assert ("PRT002", "error", 8) in codes(text)
    assert P.summarise([P.analyse("t.yml", text)])["verdict"] == "failing_for_fork_prs"


@pytest.mark.parametrize("with_lines", [
    "          ref: refs/pull/${{ github.event.pull_request.number }}/merge\n",
    "          ref: ${{ github.event.pull_request.merge_commit_sha }}\n",
    "          repository: ${{ github.event.pull_request.head.repo.full_name }}\n          ref: ${{ github.head_ref }}\n",
])
def test_other_forms_of_fork_code(with_lines):
    assert "PRT002" in [c for c, _, _ in codes(checkout_workflow(with_lines))]


def test_a_branch_name_alone_is_not_fork_code():
    assert codes(checkout_workflow("          ref: ${{ github.head_ref }}\n")) == [("PRT001", "warning", 1)]


def test_workflow_run_checkouts_of_the_pr_head_are_refused_too_but_not_blocked_on_november_2():
    text = checkout_workflow("          ref: ${{ github.event.workflow_run.head_sha }}\n", trigger="workflow_run",
                             extra="")
    found = codes(text)
    assert [c for c, _, _ in found] == ["PRT002"]


def test_opting_in_with_secrets_and_later_steps_names_the_risk():
    text = checkout_workflow("          ref: ${{ github.event.pull_request.head.sha }}\n"
                             "          allow-unsafe-pr-checkout: true\n",
                             after="      - run: npm ci && npm test\n        env:\n          NPM_TOKEN: ${{ secrets.NPM_TOKEN }}\n")
    prt003 = [f for f in P.analyse("t.yml", text).findings if f.code == "PRT003"]
    assert prt003 and prt003[0].level == "warning" and "secrets.NPM_TOKEN" in prt003[0].message
    assert "later steps run after the checkout" in prt003[0].message
    assert "PRT002" not in [f.code for f in P.analyse("t.yml", text).findings]


def test_opting_in_to_read_the_code_as_data_is_a_warning():
    text = checkout_workflow("          ref: ${{ github.event.pull_request.head.sha }}\n"
                             "          allow-unsafe-pr-checkout: true\n", after="", extra="permissions: {}\n")
    prt003 = [f for f in P.analyse("t.yml", text).findings if f.code == "PRT003"]
    assert prt003 and prt003[0].level == "warning"


def test_git_fetch_of_the_pr_is_flagged_because_checkouts_guard_does_not_cover_it():
    text = ("on: pull_request_target\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
            "      - run: git fetch origin pull/${{ github.event.pull_request.number }}/head:pr && git checkout pr\n")
    assert ("PRT004", "warning", 7) in codes(text)


def test_ai_review_actions_on_the_trigger_are_named():
    text = LABELER + "      - uses: anthropics/claude-code-action@v1\n"
    found = [f for f in P.analyse("r.yml", text).findings if f.code == "PRT005"]
    assert found and "anthropics/claude-code-action" in found[0].message and "2026-11-02" in found[0].message


def test_comments_do_not_count(tmp_path):
    text = "on: push\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          # ref: ${{ github.event.pull_request.head.sha }}\n          fetch-depth: 0\n"
    assert codes(text) == []


def _repo(tmp_path, files):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name, text in files.items():
        (workflows / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_cli_formats_and_exit_codes(tmp_path, capsys):
    repo = _repo(tmp_path, {"label.yml": LABELER,
                            "test.yml": checkout_workflow("          ref: ${{ github.event.pull_request.head.sha }}\n")})
    assert P.main([str(repo)]) == 0
    assert "Already failing for fork pull requests" in capsys.readouterr().out
    assert P.main([str(repo), "--fail-on", "error"]) == 1
    capsys.readouterr()
    assert P.main([str(repo), "--format", "github"]) == 0
    out = capsys.readouterr().out
    assert "::error file=.github/workflows/test.yml,line=8,title=prt-check PRT002::" in out
    assert P.main([str(repo), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["verdict"] == "failing_for_fork_prs" and data["summary"]["pull_request_target_workflows"] == 2
    summary = tmp_path / "summary.md"
    assert P.main([str(repo), "--summary", str(summary)]) == 0
    assert "| error | PRT002 | `.github/workflows/test.yml:8` |" in summary.read_text(encoding="utf-8")


def test_a_repository_without_workflows_is_not_affected(tmp_path, capsys):
    assert P.main([str(tmp_path)]) == 0
    assert "Not affected" in capsys.readouterr().out


def test_remote_names_are_validated():
    with pytest.raises(ValueError):
        P.read_remote("../etc/passwd")


def test_a_step_or_job_that_skips_forks_does_not_fail():
    step_guard = checkout_workflow(
        "          ref: ${{ github.event.pull_request.head.sha }}\n",
        after="      - run: echo ok\n").replace(
        "      - uses: actions/checkout@v4\n",
        "      - if: github.event.pull_request.head.repo.full_name == github.repository\n"
        "        uses: actions/checkout@v4\n")
    assert "PRT002" not in [c for c, _, _ in codes(step_guard)]
    job_guard = ("on: pull_request_target\njobs:\n  preview:\n"
                 "    if: ${{ github.event.pull_request.head.repo.fork == false }}\n"
                 "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with:\n"
                 "          ref: ${{ github.event.pull_request.head.sha }}\n")
    assert "PRT002" not in [c for c, _, _ in codes(job_guard)]
    other_job = ("on: pull_request_target\njobs:\n  a:\n    if: github.event.pull_request.head.repo.fork == false\n"
                 "    runs-on: ubuntu-latest\n    steps:\n      - run: echo a\n  b:\n    runs-on: ubuntu-latest\n"
                 "    steps:\n      - uses: actions/checkout@v4\n        with:\n"
                 "          ref: ${{ github.event.pull_request.head.sha }}\n")
    assert "PRT002" in [c for c, _, _ in codes(other_job)], "a guard on another job does not count"


@pytest.mark.parametrize("spec, line, expected", [
    ("v1", "", False), ("v2", "", True), ("v4", "", True), ("v4.2.2", "", False), ("v4.4.0", "", True),
    ("v7.0.0", "", False), ("v7.0.1", "", True), ("v8", "", True), ("main", "", True),
    ("11d5960a326750d5838078e36cf38b85af677262", "uses: actions/checkout@11d5960a3 ", None),
    ("3d3c42e5aac5ba805825da76410c181273ba90b1", "uses: actions/checkout@3d3c42e5 # v7.0.1", True),
    ("b4ffde65f46336ab88eb53be808477a3936bae11", "uses: actions/checkout@b4ffde65 # v4.1.1", False),
])
def test_which_checkout_versions_carry_the_guard(spec, line, expected):
    assert P.checkout_refuses(spec, line) is expected


def test_an_old_pin_still_checks_out_fork_code_and_is_the_most_urgent_finding():
    text = checkout_workflow("          ref: ${{ github.event.pull_request.head.sha }}\n",
                             after="      - run: npm ci && npm test\n        env:\n          TOKEN: ${{ secrets.DEPLOY }}\n"
                             ).replace("actions/checkout@v4", "actions/checkout@v4.2.2")
    found = [f for f in P.analyse("t.yml", text).findings if f.code in ("PRT002", "PRT006")]
    assert [(f.code, f.level) for f in found] == [("PRT006", "error")]
    assert "predates the guard" in found[0].message and "secrets.DEPLOY" in found[0].message
    assert P.summarise([P.analyse("t.yml", text)])["verdict"] == "fork_code_in_privileged_workflow"


def test_workflow_run_after_a_push_only_workflow_never_meets_fork_code():
    build = "name: Nightly Build\non:\n  push:\n    branches: [main]\n  schedule:\n    - cron: '0 3 * * *'\njobs: {}\n"
    deploy = ("on:\n  workflow_run:\n    workflows: ['Nightly Build']\n    types: [completed]\njobs:\n  d:\n"
              "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with:\n"
              "          ref: ${{ github.event.workflow_run.head_sha }}\n")
    reports = P.analyse_all([(".github/workflows/build.yml", build), (".github/workflows/deploy.yml", deploy)])
    assert [f.code for r in reports for f in r.findings] == []
    pr_build = build.replace("  push:\n", "  pull_request:\n  push:\n")
    reports = P.analyse_all([(".github/workflows/build.yml", pr_build), (".github/workflows/deploy.yml", deploy)])
    assert [f.code for r in reports for f in r.findings] == ["PRT002"]


def test_block_list_of_upstream_workflows():
    lines = "on:\n  workflow_run:\n    workflows:\n      - CI\n      - \"Docs build\"\n    types: [completed]\n".split("\n")
    assert P.upstream_workflows(lines) == ["CI", "Docs build"]
