#!/usr/bin/env python3
"""Scan the most-starred public repositories with prt_check and write aggregate numbers.

    python scan/scan_top_repos.py --top 1000 --out scan-results

Reads only `.github/workflows` files, through the GitHub API (GITHUB_TOKEN if set, else the
authenticated `gh` CLI) and raw.githubusercontent.com. Nothing is written to any repository.

Writes to --out:
  aggregate.json   the numbers the published report uses (no repository names)
  repos.jsonl      one line per repository (kept private: the report never names repositories)
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prt_check as P  # noqa: E402

TOKEN = os.environ.get("GITHUB_TOKEN", "")
USES_RE = re.compile(r"\buses\s*:\s*[\"']?([^\s\"'@]+)")


def api(path: str) -> object:
    """GET https://api.github.com/<path>. None on 404."""
    for attempt in range(4):
        if TOKEN:
            request = urllib.request.Request(f"https://api.github.com/{path}", headers={
                "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                "User-Agent": f"prt-check-scan/{P.__version__}"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in (403, 429) and attempt < 3:
                    time.sleep(61)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                time.sleep(10 * (attempt + 1))
                continue
        done = subprocess.run(["gh", "api", "-X", "GET", path], capture_output=True, text=True, encoding="utf-8",
                              timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode == 0:
            return json.loads(done.stdout or "null")
        if "HTTP 404" in done.stderr:
            return None
        if attempt < 3 and any(m in done.stderr for m in ("rate limit", "HTTP 403", "HTTP 429", "dial tcp",
                                                          "timeout", "HTTP 50")):
            time.sleep(61 if "rate" in done.stderr.lower() else 10 * (attempt + 1))
            continue
        raise RuntimeError(f"GET {path}: {done.stderr.strip()[-200:]}")
    raise RuntimeError(f"GET {path}: gave up")


def top_repositories(count: int) -> list:
    repos = []
    for page in range(1, (count + 99) // 100 + 1):
        found = api(f"search/repositories?q=stars:%3E1000+fork:false+archived:false&sort=stars&order=desc"
                    f"&per_page=100&page={page}")
        repos += [{"full_name": r["full_name"], "stars": r["stargazers_count"], "language": r.get("language")}
                  for r in (found or {}).get("items", [])]
        time.sleep(2.5)
    return repos[:count]


def raw(url: str) -> str:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "prt-check-scan"}),
                                        timeout=30) as response:
                return response.read(2_000_000).decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
    # Raise rather than return "": an empty text reads as a workflow without triggers, which would turn a
    # download failure into "fixed" or "trigger removed". scan() records the error for the repository.
    raise RuntimeError(f"could not download {url}")


def scan(repo: dict) -> dict:
    try:
        return _scan(repo)
    except Exception as exc:  # noqa: BLE001 - one unreadable repository must not end the scan
        return {**repo, "workflows": 0, "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                "summary": P.summarise([]), "prt_actions": [], "ai_on_prt": [], "codes": []}


CACHE: Path = Path("scan-results") / "files"


def _fetch(repo: dict) -> list:
    """(path, text) of the repository's workflow files, cached under CACHE so re-analysis is offline."""
    folder = CACHE / repo["full_name"].replace("/", "__")
    if (folder / ".complete").exists():
        return [(f".github/workflows/{f.name}", f.read_text(encoding="utf-8"))
                for f in sorted(folder.iterdir()) if f.name != ".complete"]
    listing = api(f"repos/{repo['full_name']}/contents/.github/workflows")
    files = [item for item in (listing if isinstance(listing, list) else [])
             if item.get("type") == "file" and str(item.get("name", "")).endswith((".yml", ".yaml"))]
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for item in files:
        text = raw(item["download_url"]) if item.get("download_url") else ""
        (folder / item["name"]).write_text(text, encoding="utf-8")
        out.append((f".github/workflows/{item['name']}", text))
    (folder / ".complete").write_text("", encoding="utf-8")
    return out


def _scan(repo: dict) -> dict:
    files = _fetch(repo)
    reports = P.analyse_all(files)
    prt_actions = []
    for (path, text), report in zip(files, reports):
        if "pull_request_target" in report.triggers:
            prt_actions += sorted({m.group(1).lower() for m in USES_RE.finditer(text)})
    summary = P.summarise(reports)
    ai_on_prt = sorted({f.message.split(" ", 2)[1] for r in reports for f in r.findings
                        if f.code == "PRT005" and "pull_request_target" in r.triggers})
    return {**repo, "workflows": len(files), "summary": summary, "prt_actions": prt_actions, "ai_on_prt": ai_on_prt,
            "codes": sorted({f.code for r in reports for f in r.findings})}


def aggregate(rows: list, top: int) -> dict:
    def share(n: int, of: int) -> float:
        return round(100.0 * n / of, 1) if of else 0.0

    with_workflows = [r for r in rows if r["workflows"]]
    uses_prt = [r for r in rows if r["summary"]["pull_request_target_workflows"]]
    failing = [r for r in rows if "PRT002" in r["codes"]]
    old_pin = [r for r in rows if "PRT006" in r["codes"]]
    opted_in = [r for r in rows if "PRT003" in r["codes"]]
    git_fetch = [r for r in rows if "PRT004" in r["codes"]]
    ai_prt = [r for r in rows if r["ai_on_prt"]]
    action_counts = collections.Counter(a for r in uses_prt for a in set(r["prt_actions"]) if a != "actions/checkout")
    ai_counts = collections.Counter(a for r in ai_prt for a in r["ai_on_prt"])
    return {
        "schema": "prt-check.scan.v1", "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection": f"the {top} most-starred public, non-fork, non-archived repositories on GitHub",
        "repositories": len(rows), "unreadable": sum(1 for r in rows if r.get("error")),
        "with_workflows": len(with_workflows),
        "workflow_files": sum(r["workflows"] for r in rows),
        "use_pull_request_target": len(uses_prt), "use_pull_request_target_pct": share(len(uses_prt), len(rows)),
        "pull_request_target_workflow_files": sum(r["summary"]["pull_request_target_workflows"] for r in rows),
        "failing_for_fork_prs": len(failing), "failing_for_fork_prs_pct": share(len(failing), len(rows)),
        "fork_code_with_old_checkout_pin": len(old_pin),
        "opted_in_unsafe_checkout": len(opted_in),
        "git_fetch_of_pr_code_in_privileged_workflow": len(git_fetch),
        "ai_or_review_action_on_pull_request_target": len(ai_prt),
        "top_actions_in_pull_request_target_workflows": action_counts.most_common(15),
        "ai_or_review_actions_on_pull_request_target": ai_counts.most_common(10),
        "note": "Repository Actions policies are not visible through the API: some of these repositories may "
                "already allow pull_request_target and will keep running it after 2026-11-02.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=1000)
    parser.add_argument("--out", type=Path, default=Path("scan-results"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    global CACHE
    CACHE = args.out / "files"
    listed = args.out / "top_repositories.json"
    if listed.exists():
        repos = json.loads(listed.read_text(encoding="utf-8"))
    else:
        repos = top_repositories(args.top)
        listed.write_text(json.dumps(repos), encoding="utf-8")
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool, \
            open(args.out / "repos.jsonl", "w", encoding="utf-8") as handle:
        for row in pool.map(scan, repos):
            rows.append(row)
            handle.write(json.dumps(row) + "\n")
    summary = aggregate(rows, args.top)
    (args.out / "aggregate.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not isinstance(v, list)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
