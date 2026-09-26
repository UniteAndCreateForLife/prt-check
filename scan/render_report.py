#!/usr/bin/env python3
"""Render REPORT.md from scan/scan_top_repos.py's aggregate.json. Every number comes from the file.

    python scan/render_report.py scan-results/aggregate.json > REPORT.md
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

DEADLINE = date(2026, 11, 2)


def render(data: dict) -> str:
    scanned = datetime.fromisoformat(data["scanned_at"]).date()
    days = (DEADLINE - scanned).days
    n = data["repositories"]
    lines = [
        f"# What GitHub's `pull_request_target` changes break in the {n:,} most-starred repositories",
        "",
        f"*Scanned {scanned.isoformat()}, {days} days before GitHub's default block on 2026-11-02, with "
        "[prt-check](README.md). Aggregate numbers only: this report names no repository.*",
        "",
        "## The numbers",
        "",
        f"- **{data['use_pull_request_target']} of {n:,} ({data['use_pull_request_target_pct']}%)** run at least one "
        f"workflow on `pull_request_target` ({data['pull_request_target_workflow_files']} workflow files). Unless "
        "their maintainers allow the trigger in an Actions policy, those workflows stop running on 2026-11-02.",
        f"- **{data['failing_for_fork_prs']} ({data['failing_for_fork_prs_pct']}%)** check out fork pull request code "
        "in a privileged workflow without the new opt-in. Since 2026-07-20 `actions/checkout` refuses that, so "
        "these steps already fail for every pull request from a fork.",
        f"- **{data['opted_in_unsafe_checkout']}** opted in with `allow-unsafe-pr-checkout: true`: a deliberate "
        "decision, safe only if no later step executes the fork's files.",
        f"- **{data['git_fetch_of_pr_code_in_privileged_workflow']}** fetch pull request code with `git fetch "
        "...pull/...` or `gh pr checkout` in a privileged workflow. That path bypasses the new guard in "
        "`actions/checkout`.",
        f"- **{data['ai_or_review_action_on_pull_request_target']}** run an AI or review action on "
        "`pull_request_target`. Those reviewers go quiet on 2026-11-02 unless the trigger is allowed.",
        "",
        "## What runs on `pull_request_target` today",
        "",
        "The actions used most often in these workflows, by the number of repositories that use them there:",
        "",
        "| Action | Repositories |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | {count} |" for name, count in data["top_actions_in_pull_request_target_workflows"][:12]]
    if data["ai_or_review_actions_on_pull_request_target"]:
        lines += ["", "AI and review actions on the trigger:", "", "| Action | Repositories |", "|---|---|"]
        lines += [f"| `{name}` | {count} |" for name, count in data["ai_or_review_actions_on_pull_request_target"]]
    lines += [
        "",
        "## Method",
        "",
        f"- Selection: {data['selection']}, from GitHub's repository search on {scanned.isoformat()}. "
        f"{data['with_workflows']:,} of them have workflow files ({data['workflow_files']:,} files in total). "
        f"{data.get('unreadable', 0)} could not be read.",
        "- Only `.github/workflows/*.yml` and `*.yaml` on the default branch were read, through the GitHub API "
        "and raw.githubusercontent.com. No workflow was run, and nothing was written anywhere.",
        "- Each file went through `prt_check.py`, which reads YAML line by line. "
        "[README](README.md#what-it-reports) lists the codes, and the tests show the layouts it understands.",
        "- Actions policies (repository, organization or enterprise) are not visible from outside, so some of "
        "these repositories may already allow `pull_request_target` and keep running it after 2026-11-02. The "
        "numbers count workflows that depend on the trigger, not workflows that will certainly stop.",
        "",
        "## What to do",
        "",
        "Run `prt-check` on your repository (Action or one command), then follow the three fixes in the "
        "[README](README.md#how-to-fix): switch to `pull_request`, split into `pull_request` + `workflow_run`, "
        "or keep the trigger deliberately with an Actions policy.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
