#!/usr/bin/env python3
"""prt-check: is this repository ready for GitHub's 2026 pull_request_target changes?

Two changes break GitHub Actions workflows on public repositories in 2026:

1. Since 2026-07-20, actions/checkout (every supported major version, not only v7) refuses
   to check out fork pull request code in `pull_request_target` workflows, and in
   `workflow_run` workflows started by a pull request. The step fails unless it sets
   `allow-unsafe-pr-checkout: true`. Same-repository pull requests are not affected.
   https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/
2. From 2026-11-02, a default Actions policy blocks the `pull_request_target` trigger on
   public repositories that have no applicable policy allowing it. Such workflows stop running.
   https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/

This script reads workflow files and reports what each change does to them. It never runs
workflow code. It reads YAML line by line, without a YAML library, so it has no
dependencies; unusual layouts can be missed, and every finding gives a line to check.

    python prt_check.py [PATH]                  # scans PATH/.github/workflows (default: .)
    python prt_check.py --repo OWNER/NAME       # reads a public repository through the GitHub API
    python prt_check.py --format json | github  # machine output, or GitHub Actions annotations
    python prt_check.py --summary FILE          # also writes a Markdown summary (e.g. $GITHUB_STEP_SUMMARY)
    python prt_check.py --fail-on error|warning # exit 1 when a finding at that level or worse exists

Exit codes: 0 report written, 1 a finding at or above --fail-on, 2 usage or read error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

__version__ = "1.0.0"

CHECKOUT_CHANGELOG = "https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/"
POLICY_CHANGELOG = "https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/"
SECURE_GUIDE = "https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target"
LEVELS = {"notice": 0, "warning": 1, "error": 2}

# What actions/checkout refuses for fork pull requests (GitHub changelog, 2026-06-18): a `ref` that is
# refs/pull/N/head or /merge or resolves to the fork PR's head or merge SHA, or a `repository` that
# resolves to the fork. A branch name alone (github.head_ref, ...head.ref) names a branch of the base
# repository, so it is not refused and is not fork code.
FORK_REF_RE = re.compile(
    r"github\.event\.pull_request\.head\.sha|github\.event\.pull_request\.merge_commit_sha|refs/pull/"
    r"|github\.event\.workflow_run\.head_sha", re.I)
FORK_REPO_RE = re.compile(
    r"github\.event\.pull_request\.head\.repo\.full_name|github\.event\.workflow_run\.head_repository\.full_name",
    re.I)
# Shell commands that fetch pull request code without actions/checkout (its guard does not apply).
GIT_PR_FETCH_RE = re.compile(
    r"\bgit\s+fetch\b[^\n]*\bpull/|\bgh\s+pr\s+checkout\b"
    r"|\bgit\s+(?:checkout|switch|merge|reset)\b[^\n]*(?:github\.event\.pull_request\.head\.sha"
    r"|github\.event\.workflow_run\.head_sha)", re.I)
# Actions that run an AI model over the pull request (reviewers, agents).
AI_ACTION_RE = re.compile(
    r"claude|anthropic|coderabbit|pr-agent|qodo|codium|openai|codex|gemini|copilot|chatgpt|\bgpt|llm|deepseek"
    r"|ollama|mistral|groq|ai-?review|ai-?code|code-?review-?(?:gpt|ai)|review-?bot|ai-pr", re.I)
SECRET_RE = re.compile(r"secrets\.(?!GITHUB_TOKEN\b)[A-Za-z_][A-Za-z0-9_]*")
WRITE_PERMISSION_RE = re.compile(r"^\s*permissions\s*:\s*write-all\b|^\s*[a-z-]+\s*:\s*write\b", re.I)
KEY_RE = re.compile(r"^(\s*)(?:-\s+)?([\"']?)([A-Za-z0-9_-]+)\2\s*:(.*)$")


@dataclass
class Finding:
    file: str
    line: int
    level: str
    code: str
    message: str


@dataclass
class WorkflowReport:
    file: str
    triggers: List[str]
    findings: List[Finding] = field(default_factory=list)


def _strip_comment(line: str) -> str:
    """Drops a trailing YAML comment; a '#' inside quotes is kept."""
    quote = ""
    for i, ch in enumerate(line):
        if ch in "\"'" and (not quote or quote == ch):
            quote = "" if quote else ch
        elif ch == "#" and not quote and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _flow_keys(text: str) -> List[str]:
    """Top-level names in a flow sequence or mapping: "[push, pull_request_target]" or "{push: {...}}"."""
    text = text.strip()
    if not text or text[0] not in "[{":
        return [text.strip("\"' ")] if text else []
    body, depth, part, parts = text[1:-1] if text[-1:] in "]}" else text[1:], 0, "", []
    for ch in body:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(part)
            part = ""
        else:
            part += ch
    parts.append(part)
    names = []
    for piece in parts:
        name = piece.split(":", 1)[0].strip().strip("\"'")
        if name:
            names.append(name)
    return names


def triggers(lines: List[str]) -> Tuple[List[str], int]:
    """(event names, 1-based line of the `on` key). The key can be on, "on", 'on' or YAML 1.1's true."""
    for number, raw in enumerate(lines):
        line = _strip_comment(raw)
        match = re.match(r"^([\"']?)(on|true)\1\s*:(.*)$", line)
        if not match:
            continue
        inline = match.group(3).strip()
        if inline:
            return _flow_keys(inline), number + 1
        names: List[str] = []
        child_indent: Optional[int] = None
        for below in lines[number + 1:]:
            text = _strip_comment(below)
            if not text.strip():
                continue
            depth = _indent(text)
            if depth == 0:
                break
            if child_indent is None:
                child_indent = depth
            if depth != child_indent:
                continue
            item = text.strip()
            if item.startswith("- "):
                names += _flow_keys(item[2:])
            else:
                key = KEY_RE.match(text)
                if key:
                    names.append(key.group(3))
        return names, number + 1
    return [], 0


def _block(lines: List[str], start: int) -> List[Tuple[int, str]]:
    """The step that contains line `start`: from its "- " item to the next item at the same indent."""
    head = start
    while head > 0 and not _strip_comment(lines[head]).lstrip().startswith("- "):
        head -= 1
    item_indent = _indent(lines[head])
    block = [(head, lines[head])]
    for number in range(head + 1, len(lines)):
        text = _strip_comment(lines[number])
        if text.strip() and _indent(text) <= item_indent:
            break
        block.append((number, lines[number]))
    return block


def _value(block: List[Tuple[int, str]], key: str) -> Tuple[str, int]:
    for number, raw in block:
        match = re.match(r"^\s*(?:-\s+)?" + re.escape(key) + r"\s*:\s*(.*)$", _strip_comment(raw))
        if match:
            return match.group(1).strip().strip("\"'"), number + 1
    return "", 0


# A condition that skips the step or job for fork pull requests, so the checkout never meets fork code.
SAME_REPO_GUARD_RE = re.compile(
    r"head\.repo\.full_name\s*==\s*github\.repository|github\.repository\s*==\s*github\.event\.pull_request"
    r"\.head\.repo\.full_name|head\.repo\.fork\s*==\s*false|!\s*github\.event\.pull_request\.head\.repo\.fork"
    r"|head_repository\.full_name\s*==\s*github\.repository", re.I)


def _guarded_for_forks(lines: List[str], block: List[Tuple[int, str]]) -> bool:
    """Whether the step, or the job it belongs to, only runs for same-repository pull requests."""
    if any(SAME_REPO_GUARD_RE.search(_strip_comment(raw)) for _, raw in block):
        return True
    step_indent = _indent(lines[block[0][0]])
    for number in range(block[0][0] - 1, -1, -1):
        text = _strip_comment(lines[number])
        if not text.strip():
            continue
        if _indent(text) < step_indent and re.match(r"^\s*if\s*:", text) and SAME_REPO_GUARD_RE.search(text):
            return True
        if re.match(r"^ {0,4}[A-Za-z0-9_-]+\s*:\s*$", text) and _indent(text) <= 4 and number < block[0][0] - 1:
            # Reached the job's own key (two or four spaces under `jobs:`): stop.
            if _indent(text) <= 2 or not text.lstrip().startswith(("steps", "runs-on", "if", "permissions")):
                break
    return False


def _later_steps(lines: List[str], block_end: int, item_indent: int) -> bool:
    """Whether the job runs more steps after this one (same list, same indent)."""
    for raw in lines[block_end:]:
        text = _strip_comment(raw)
        if not text.strip():
            continue
        if _indent(text) < item_indent:
            return False
        if _indent(text) == item_indent and text.lstrip().startswith("- "):
            return True
    return False


def analyse(name: str, text: str) -> WorkflowReport:
    lines = text.replace("\r\n", "\n").split("\n")
    events, on_line = triggers(lines)
    report = WorkflowReport(file=name, triggers=events)
    privileged = [e for e in events if e in ("pull_request_target", "workflow_run")]
    if not privileged:
        return report

    def add(line: int, level: str, code: str, message: str) -> None:
        report.findings.append(Finding(name, line, level, code, message))

    secrets = sorted({m.group(0) for m in SECRET_RE.finditer(text)})
    writes = any(WRITE_PERMISSION_RE.match(_strip_comment(l)) for l in lines)
    no_permissions = not any(re.match(r"^\s*permissions\s*:", _strip_comment(l)) for l in lines)
    power = ", ".join(filter(None, [
        "uses " + ", ".join(secrets[:3]) + (" and more" if len(secrets) > 3 else "") if secrets else "",
        "grants write permissions" if writes else "",
        "sets no permissions (the token may have write access)" if no_permissions else "",
    ]))

    if "pull_request_target" in events:
        add(on_line, "warning", "PRT001",
            "Triggered by pull_request_target. From 2026-11-02 GitHub blocks this trigger on public repositories "
            "unless an Actions policy allows it, and this workflow stops running. Move it to pull_request (plus "
            "workflow_run for steps that need write access), or allow the trigger in Settings > Actions > Policies "
            f"after a review. {POLICY_CHANGELOG}")

    for number, raw in enumerate(lines):
        text_line = _strip_comment(raw)
        uses = re.search(r"\buses\s*:\s*[\"']?([^\s\"'@]+)@?([^\s\"']*)", text_line)
        if uses and uses.group(1).lower() == "actions/checkout":
            block = _block(lines, number)
            ref, ref_line = _value(block, "ref")
            repo, repo_line = _value(block, "repository")
            opted_in, opt_line = _value(block, "allow-unsafe-pr-checkout")
            target = " ".join(x for x in (ref, repo) if x)
            if not (FORK_REF_RE.search(ref) or FORK_REPO_RE.search(repo)):
                continue
            where = ref_line or repo_line or number + 1
            if _guarded_for_forks(lines, block):
                continue
            if opted_in.lower() != "true":
                add(where, "error", "PRT002",
                    f"Checks out pull request code ({target}) in a {'/'.join(privileged)} workflow. Since "
                    "2026-07-20 actions/checkout refuses this for pull requests from forks, so this step fails "
                    "for every fork PR. Do not add allow-unsafe-pr-checkout unless the code is only read as data; "
                    f"prefer running untrusted code in a pull_request workflow. {CHECKOUT_CHANGELOG}")
            else:
                later = _later_steps(lines, block[-1][0] + 1, _indent(lines[block[0][0]]))
                add(opt_line or where, "warning", "PRT003",
                    "Opts in to checking out fork code with allow-unsafe-pr-checkout" +
                    (f"; the workflow {power}" if power else "") +
                    ("; and later steps run after the checkout" if later else "") +
                    ". A fork can then run code with this workflow's token and secrets (a pwn request) unless "
                    f"no later step executes the checked-out files. {SECURE_GUIDE}")
        elif uses and AI_ACTION_RE.search(uses.group(1)):
            add(number + 1, "notice", "PRT005",
                f"Runs {uses.group(1)} (an AI or review action) on {'/'.join(privileged)}. "
                + ("It stops running on 2026-11-02 unless the trigger is allowed by an Actions policy. "
                   if "pull_request_target" in events else "")
                + "Keep model output away from privileged steps: a pull request can steer an AI reviewer.")
        if GIT_PR_FETCH_RE.search(text_line):
            add(number + 1, "warning", "PRT004",
                "Fetches pull request code with git or gh in a privileged workflow. actions/checkout's guard "
                "does not cover this, so fork code still reaches a job that " + (power or "has repository access")
                + f". {SECURE_GUIDE}")
    return report


def summarise(reports: List[WorkflowReport]) -> Dict[str, object]:
    findings = [f for r in reports for f in r.findings]
    codes = {f.code for f in findings}
    if "PRT002" in codes:
        verdict = "failing_for_fork_prs"
    elif "PRT001" in codes:
        verdict = "affected_on_2026_11_02"
    elif codes & {"PRT003", "PRT004"}:
        verdict = "review_privileged_checkout"
    else:
        verdict = "not_affected"
    return {"verdict": verdict, "workflows": len(reports),
            "pull_request_target_workflows": sum("pull_request_target" in r.triggers for r in reports),
            "findings": len(findings), "codes": sorted(codes)}


VERDICT_TEXT = {
    "failing_for_fork_prs": "Already failing for fork pull requests (actions/checkout refuses the checkout since 2026-07-20).",
    "affected_on_2026_11_02": "Affected on 2026-11-02: pull_request_target workflows stop running without an allowing Actions policy.",
    "review_privileged_checkout": "Runs fork code in a privileged workflow: review before 2026-11-02.",
    "not_affected": "Not affected: no pull_request_target workflows and no fork-code checkouts in workflow_run.",
}


def read_local(path: Path) -> List[Tuple[str, str]]:
    folder = path / ".github" / "workflows"
    files = sorted(list(folder.glob("*.yml")) + list(folder.glob("*.yaml"))) if folder.is_dir() else []
    return [(f.relative_to(path).as_posix(), f.read_text(encoding="utf-8", errors="replace")) for f in files]


def _get(url: str, token: str = "") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"prt-check/{__version__}",
                                                   "Accept": "application/vnd.github+json"})
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(2_000_000)


def read_remote(repo: str, token: str = "") -> List[Tuple[str, str]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repo):
        raise ValueError(f"not a repository name: {repo!r}")
    try:
        listing = json.loads(_get(f"https://api.github.com/repos/{repo}/contents/.github/workflows", token))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    out = []
    for item in listing if isinstance(listing, list) else []:
        name = str(item.get("name") or "")
        if item.get("type") == "file" and name.endswith((".yml", ".yaml")) and item.get("download_url"):
            out.append((f".github/workflows/{name}", _get(item["download_url"]).decode("utf-8", "replace")))
    return out


def markdown(reports: List[WorkflowReport], summary: Dict[str, object], source: str) -> str:
    lines = [f"## prt-check: {source}", "", f"**{VERDICT_TEXT[str(summary['verdict'])]}**", "",
             f"{summary['workflows']} workflow files, {summary['pull_request_target_workflows']} triggered by "
             f"pull_request_target, {summary['findings']} findings.", ""]
    findings = [f for r in reports for f in r.findings]
    if findings:
        lines += ["| Level | Code | Where | What to do |", "|---|---|---|---|"]
        lines += [f"| {f.level} | {f.code} | `{f.file}:{f.line}` | {f.message.replace('|', '/')} |" for f in findings]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--repo", help="OWNER/NAME of a public repository (uses GITHUB_TOKEN if set)")
    parser.add_argument("--format", choices=["text", "json", "github"], default="text")
    parser.add_argument("--summary", help="also write a Markdown summary to this file (appends)")
    parser.add_argument("--fail-on", choices=["none", "notice", "warning", "error"], default="none")
    parser.add_argument("--version", action="version", version=f"prt-check {__version__}")
    args = parser.parse_args(argv)
    try:
        files = read_remote(args.repo, os.environ.get("GITHUB_TOKEN", "")) if args.repo else read_local(Path(args.path))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"prt-check: cannot read workflows: {exc}", file=sys.stderr)
        return 2
    reports = [analyse(name, text) for name, text in files]
    summary = summarise(reports)
    source = args.repo or str(Path(args.path).resolve().name)
    if args.format == "json":
        print(json.dumps({"source": source, "summary": summary,
                          "workflows": [asdict(r) for r in reports]}, indent=2))
    elif args.format == "github":
        for f in (f for r in reports for f in r.findings):
            message = f.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::{f.level} file={f.file},line={f.line},title=prt-check {f.code}::{message}")
        print(VERDICT_TEXT[str(summary["verdict"])])
    else:
        print(f"prt-check {source}: {VERDICT_TEXT[str(summary['verdict'])]}")
        for f in (f for r in reports for f in r.findings):
            print(f"  {f.level.upper():7} {f.code} {f.file}:{f.line}  {f.message}")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(markdown(reports, summary, source))
    if args.fail_on != "none" and any(LEVELS[f.level] >= LEVELS[args.fail_on] for r in reports for f in r.findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
