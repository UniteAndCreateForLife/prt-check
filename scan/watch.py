#!/usr/bin/env python3
"""Daily watch of the repositories that use pull_request_target (read-only).

    python scan/watch.py --baseline DIR --out DIR

Re-reads the workflow files of every repository that used pull_request_target, or had a finding, in the
baseline scan (DIR/repos.jsonl from scan_top_repos.py), analyses them again with prt_check, and for each
repository whose checkout now fails for fork PRs looks for a real failed fork run since 2026-07-20.

Writes to --out (private: it names repositories, so keep it out of public reports):
  watch.json   per repository: status now vs the baseline, and evidence
  WATCH.md     the summary: fixed since the baseline, newly failing, failing with evidence
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prt_check as P  # noqa: E402
import scan_top_repos as S  # noqa: E402

GUARD_DATE = "2026-07-20"


def failed_fork_run(repo: str, workflow: str, api=None) -> dict | None:
    """The newest fork-PR run of `workflow` since the checkout guard that failed AT a checkout step, if any.

    A run that failed for another reason (a flaky test, a cancelled job) is not evidence of the guard."""
    api = api or S.api
    runs = api(f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=30&created=%3E%3D{GUARD_DATE}") or {}
    for run in runs.get("workflow_runs", []):
        head = (run.get("head_repository") or {}).get("full_name")
        if not head or head == repo or run.get("conclusion") != "failure":
            continue
        jobs = (api(f"repos/{repo}/actions/runs/{run.get('id')}/jobs") or {}).get("jobs", [])
        step = next((s.get("name") for j in jobs for s in j.get("steps") or []
                     if s.get("conclusion") == "failure" and "checkout" in str(s.get("name")).lower()), None)
        if step:
            return {"run": run.get("html_url"), "at": run.get("created_at"), "failed_step": step}
    return None


def status_of(old: dict, new: dict) -> str:
    """How the repository changed since the baseline. A scan that failed proves nothing, so it is
    "unreadable" and never "checkout_fixed" or "trigger_removed"."""
    if new.get("error"):
        return "unreadable"
    return classify(set(old["codes"]), set(new["codes"]))


def classify(before: set, after: set) -> str:
    if "PRT002" in after and "PRT002" not in before:
        return "newly_failing"
    if "PRT002" in before and "PRT002" not in after:
        return "checkout_fixed"
    if "PRT001" in before and "PRT001" not in after:
        return "trigger_removed"
    if "PRT002" in after:
        return "still_failing"
    return "unchanged"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    S.CACHE = args.out / "files"
    baseline = [json.loads(line) for line in (args.baseline / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
    watched = [r for r in baseline if r["summary"]["pull_request_target_workflows"] or r["codes"]]
    rows = []
    for old in watched:
        repo = {k: old[k] for k in ("full_name", "stars", "language")}
        new = S.scan(repo)
        status = status_of(old, new)
        evidence = []
        if "PRT002" in new["codes"]:
            files = S._fetch(repo)
            for report in P.analyse_all(files):
                if any(f.code == "PRT002" for f in report.findings):
                    found = failed_fork_run(repo["full_name"], report.file.split("/")[-1])
                    if found:
                        evidence.append({"workflow": report.file, **found})
        rows.append({"repo": repo["full_name"], "stars": repo["stars"], "status": status,
                     "codes_before": old["codes"], "codes_now": new["codes"], "evidence": evidence,
                     "error": new.get("error")})
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (args.out / "watch.json").write_text(json.dumps({"at": stamp, "rows": rows}, indent=2), encoding="utf-8")
    by = {}
    for row in rows:
        by.setdefault(row["status"], []).append(row)
    lines = [f"# pull_request_target watch: {stamp}", "", "Private: names repositories. Read-only.", ""]
    for status, title in (("newly_failing", "Newly failing for fork PRs"), ("checkout_fixed", "Checkout fixed since the baseline"),
                          ("trigger_removed", "Stopped using pull_request_target"), ("still_failing", "Still failing"),
                          ("unreadable", "Could not read (no conclusion)")):
        items = sorted(by.get(status, []), key=lambda r: -(r["stars"] or 0))
        lines += [f"## {title} ({len(items)})", ""]
        for row in items:
            proof = "; ".join(f"{e['workflow'].split('/')[-1]}: failed fork run {e['run']}" for e in row["evidence"])
            proof = proof or (row["error"] or "")
            lines.append(f"- {row['repo']} ({row['stars']:,} stars){': ' + proof if proof else ''}")
        lines.append("")
    lines.append(f"Watched {len(rows)} repositories; unchanged: {len(by.get('unchanged', []))}.")
    (args.out / "WATCH.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"watch: {len(rows)} repositories; " + ", ".join(f"{k}={len(v)}" for k, v in sorted(by.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
