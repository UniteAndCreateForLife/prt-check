// prt-check for the browser and Node: a line-for-line port of prt_check.py (same rules, same messages).
// web/parity.mjs runs both on the same workflow files and fails on any difference, so keep them in step.
export const VERSION = "1.1.0";

const CHECKOUT_CHANGELOG = "https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/";
const POLICY_CHANGELOG = "https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/";
const SECURE_GUIDE = "https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target";
export const LEVELS = { notice: 0, warning: 1, error: 2 };

const FORK_REF_RE = /github\.event\.pull_request\.head\.sha|github\.event\.pull_request\.merge_commit_sha|refs\/pull\/|github\.event\.workflow_run\.head_sha/i;
const FORK_REPO_RE = /github\.event\.pull_request\.head\.repo\.full_name|github\.event\.workflow_run\.head_repository\.full_name/i;
const GIT_PR_FETCH_RE = /\bgit\s+fetch\b[^\n]*\bpull\/|\bgh\s+pr\s+checkout\b|\bgit\s+(?:checkout|switch|merge|reset)\b[^\n]*(?:github\.event\.pull_request\.head\.sha|github\.event\.workflow_run\.head_sha)/i;
const AI_ACTION_RE = /claude|anthropic|coderabbit|pr-agent|qodo|codium|openai|codex|gemini|copilot|chatgpt|\bgpt|llm|deepseek|ollama|mistral|groq|ai-?review|ai-?code|code-?review-?(?:gpt|ai)|review-?bot|ai-pr/i;
const SECRET_RE = /secrets\.(?!GITHUB_TOKEN\b)[A-Za-z_][A-Za-z0-9_]*/g;
const WRITE_PERMISSION_RE = /^\s*permissions\s*:\s*write-all\b|^\s*[a-z-]+\s*:\s*write\b/i;
const KEY_RE = /^(\s*)(?:-\s+)?(["']?)([A-Za-z0-9_-]+)\2\s*:(.*)$/;
const SAME_REPO_GUARD_RE = /head\.repo\.full_name\s*==\s*github\.repository|github\.repository\s*==\s*github\.event\.pull_request\.head\.repo\.full_name|head\.repo\.fork\s*==\s*false|!\s*github\.event\.pull_request\.head\.repo\.fork|head_repository\.full_name\s*==\s*github\.repository/i;
const BOT_ONLY_RE = /(?:pull_request\.user\.login|github\.actor|github\.triggering_actor)\s*==\s*['"](?:dependabot|renovate)\[bot\]/i;
const EVENT_EQ_RE = /github\.event_name\s*==\s*['"]([a-z_]+)['"]/gi;
const RUN_EVENT_EQ_RE = /github\.event\.workflow_run\.event\s*==\s*['"]([a-z_]+)['"]/gi;

// Python's str.strip() / str.strip(chars), without JavaScript's extra ﻿.
const WS = " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0                　";
function strip(s, chars = WS) {
  let a = 0, b = s.length;
  while (a < b && chars.includes(s[a])) a++;
  while (b > a && chars.includes(s[b - 1])) b--;
  return s.slice(a, b);
}
const rstrip = (s, chars = WS) => { let b = s.length; while (b > 0 && chars.includes(s[b - 1])) b--; return s.slice(0, b); };
const lstrip = (s, chars = WS) => { let a = 0; while (a < s.length && chars.includes(s[a])) a++; return s.slice(a); };
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\\-]/g, "\\$&");
const findall = (re, text) => [...text.matchAll(re)].map((m) => m[1]);

function stripComment(line) {
  let quote = "";
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if ((ch === '"' || ch === "'") && (!quote || quote === ch)) quote = quote ? "" : ch;
    else if (ch === "#" && !quote && (i === 0 || line[i - 1] === " " || line[i - 1] === "\t")) return rstrip(line.slice(0, i));
  }
  return rstrip(line);
}

const indent = (line) => line.length - lstrip(line, " ").length;

function flowKeys(text) {
  text = strip(text);
  if (!text || !"[{".includes(text[0])) return text ? [strip(text, "\"' ")] : [];
  const body = "]}".includes(text.slice(-1)) ? text.slice(1, -1) : text.slice(1);
  let depth = 0, part = "";
  const parts = [];
  for (const ch of body) {
    if ("[{".includes(ch)) depth++;
    else if ("]}".includes(ch)) depth--;
    if (ch === "," && depth === 0) { parts.push(part); part = ""; } else part += ch;
  }
  parts.push(part);
  const names = [];
  for (const piece of parts) {
    const name = strip(strip(piece.split(":")[0]), "\"'");
    if (name) names.push(name);
  }
  return names;
}

export function triggers(lines) {
  for (let number = 0; number < lines.length; number++) {
    const line = stripComment(lines[number]);
    const match = /^(["']?)(on|true)\1\s*:(.*)$/.exec(line);
    if (!match) continue;
    const inline = strip(match[3]);
    if (inline) return [flowKeys(inline), number + 1];
    const names = [];
    let childIndent = null;
    for (const below of lines.slice(number + 1)) {
      const text = stripComment(below);
      if (!strip(text)) continue;
      const depth = indent(text);
      if (depth === 0) break;
      if (childIndent === null) childIndent = depth;
      if (depth !== childIndent) continue;
      const item = strip(text);
      if (item.startsWith("- ")) names.push(...flowKeys(item.slice(2)));
      else {
        const key = KEY_RE.exec(text);
        if (key) names.push(key[3]);
      }
    }
    return [names, number + 1];
  }
  return [[], 0];
}

function block(lines, start) {
  let head = start;
  while (head > 0 && !lstrip(stripComment(lines[head])).startsWith("- ")) head--;
  const itemIndent = indent(lines[head]);
  const out = [[head, lines[head]]];
  for (let number = head + 1; number < lines.length; number++) {
    const text = stripComment(lines[number]);
    if (strip(text) && indent(text) <= itemIndent) break;
    out.push([number, lines[number]]);
  }
  return out;
}

function value(blk, key) {
  const re = new RegExp("^\\s*(?:-\\s+)?" + escapeRe(key) + "\\s*:\\s*(.*)$");
  for (const [number, raw] of blk) {
    const match = re.exec(stripComment(raw));
    if (match) return [strip(strip(match[1]), "\"'"), number + 1];
  }
  return ["", 0];
}

function condition(lines, number) {
  const text = stripComment(lines[number]);
  const inline = text.replace(/^\s*(?:-\s+)?if\s*:\s*/, "");
  if (!["|", ">", "|-", ">-", "|+", ">+"].includes(strip(inline))) return inline;
  const body = [], depth = indent(text);
  for (const below of lines.slice(number + 1)) {
    const part = stripComment(below);
    if (strip(part) && indent(part) <= depth) break;
    body.push(strip(part));
  }
  return body.join(" ");
}

function skipsForkCode(cond, privileged) {
  const text = cond.split("${{").join("").split("}}").join("");
  let branches = [], depth = 0, part = "", i = 0;
  while (i < text.length) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")") depth--;
    if (text.startsWith("||", i) && depth === 0) { branches.push(part); part = ""; i += 2; continue; }
    part += text[i];
    i++;
  }
  branches = [...branches, part].filter((b) => strip(b));
  const branchSkips = (branch) => {
    if (SAME_REPO_GUARD_RE.test(branch) || BOT_ONLY_RE.test(branch)) return true;
    const triggering = new Set(findall(RUN_EVENT_EQ_RE, branch).map((e) => e.toLowerCase()));
    if (triggering.size && !triggering.has("pull_request") && !triggering.has("pull_request_target")) return true;
    const events = new Set(findall(EVENT_EQ_RE, branch).map((e) => e.toLowerCase()));
    return events.size > 0 && !privileged.some((p) => events.has(p));
  };
  return branches.length > 0 && branches.every(branchSkips);
}

function jobIndent(lines) {
  for (let number = 0; number < lines.length; number++) {
    if (/^jobs\s*:\s*$/.test(stripComment(lines[number]))) {
      for (const below of lines.slice(number + 1)) {
        const text = stripComment(below);
        if (strip(text)) return indent(text);
      }
    }
  }
  return 2;
}

function guardedForForks(lines, blk, privileged) {
  for (const [number, raw] of blk) {
    if (/^\s*(?:-\s+)?if\s*:/.test(stripComment(raw)) && skipsForkCode(condition(lines, number), privileged)) return true;
  }
  const stepIndent = indent(lines[blk[0][0]]), jobInd = jobIndent(lines);
  for (let number = blk[0][0] - 1; number >= 0; number--) {
    const text = stripComment(lines[number]);
    if (!strip(text)) continue;
    if (indent(text) <= jobInd) break;
    if (indent(text) < stepIndent && /^\s*if\s*:/.test(text) && skipsForkCode(condition(lines, number), privileged)) return true;
  }
  return false;
}

function laterSteps(lines, blockEnd, itemIndent) {
  for (const raw of lines.slice(blockEnd)) {
    const text = stripComment(raw);
    if (!strip(text)) continue;
    if (indent(text) < itemIndent) return false;
    if (indent(text) === itemIndent && lstrip(text).startsWith("- ")) return true;
  }
  return false;
}

const CHECKOUT_RELEASES = {
  af513c7a0160: "1.0.0", "0b496e91ec7a": "1.1.0", "50fbc622fc4e": "1.2.0", "722adc63f1aa": "2.0.0",
  "01aecccf739c": "2.1.0", "86f86b36ef15": "2.1.1", aabbfeb2ce60: "2.2.0", b4483adec309: "2.3.0",
  "28c7f3d2b516": "2.3.1", "2036a08e25fa": "2.3.2", a81bbbf8298c: "2.3.3", "5a4ac9002d0b": "2.3.4",
  "1e204e9a9253": "2.3.5", ec3a7ce11313: "2.4.0", f25a3a9f25bd: "2.4.1", "7884fcad6b5d": "2.4.2",
  e2f20e631ae6: "2.5.0", dc323e67f16f: "2.6.0", ee0669bd1cc5: "2.7.0", "0717577d4573": "2.8.0",
  a12a3943b4bd: "3.0.0", dcd71f646680: "3.0.1", "2541b1294d27": "3.0.2", "93ea575cb5d8": "3.1.0",
  "755da8c3cf11": "3.2.0", ac593985615e: "3.3.0", "24cb90801772": "3.4.0", "8f4b7f848644": "3.5.0",
  "83b7061638ee": "3.5.1", "8e5e7e5ab8b3": "3.5.2", c85c95e3d725: "3.5.3", f43a0e5ff2bd: "3.6.0",
  a37ce9120846: "3.7.0", "1e31de5234b9": "4.0.0", "8ade135a41bc": "4.1.0", b4ffde65f463: "4.1.1",
  "9bb56186c3b0": "4.1.2", "1d96c772d194": "4.1.3", "0ad4b8fadaa2": "4.1.4", "44c2b7a8a4ea": "4.1.5",
  a5ac7e51b410: "4.1.6", "692973e3d937": "4.1.7", d632683dd7b4: "4.2.0", eef61447b9ff: "4.2.1",
  "11bd71901bbe": "4.2.2", "08eba0b27e82": "4.3.0", "34e114876b0b": "4.3.1", "11d5960a3267": "4.4.0",
  "08c6903cd8c0": "5.0.0", "93cb6efe1820": "5.0.1", fbc6f3992d24: "5.1.0", "1af3b93b6815": "6.0.0",
  "8e8c483db84b": "6.0.1", de0fac2e4500: "6.0.2", df4cb1c069e1: "6.0.3", d23441a48e51: "6.1.0",
  "9c091bb21b7c": "7.0.0", "3d3c42e5aac5": "7.0.1",
};
const GUARDED_FROM = { 2: [2, 8, 0], 3: [3, 7, 0], 4: [4, 4, 0], 5: [5, 1, 0], 6: [6, 1, 0], 7: [7, 0, 1] };
const VERSION_RE = /^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$/;

export function checkoutRefuses(spec, rawLine = "") {
  spec = strip(spec);
  if (spec === "main" || spec === "master") return true;
  let version = VERSION_RE.exec(spec);
  if (!version && /^[0-9a-f]{7,40}$/.test(spec)) {
    const known = spec.length >= 12 ? CHECKOUT_RELEASES[spec.slice(0, 12)] : undefined;
    if (known) return checkoutRefuses(known);
    const comment = /#\s*v?(\d+\.\d+(?:\.\d+)?)\b/.exec(rawLine);
    if (!comment) return null;
    version = VERSION_RE.exec(comment[1]);
  }
  if (!version) return null;
  const major = parseInt(version[1], 10);
  if (major < 2) return false;
  if (major > 7) return true;
  if (version[2] === undefined) return true;
  const exact = [major, parseInt(version[2], 10), parseInt(version[3] || "0", 10)];
  const floor = GUARDED_FROM[major];
  for (let k = 0; k < 3; k++) if (exact[k] !== floor[k]) return exact[k] > floor[k];
  return true;
}

function upstreamWorkflows(lines) {
  for (let number = 0; number < lines.length; number++) {
    const match = /^\s*workflows\s*:(.*)$/.exec(stripComment(lines[number]));
    if (!match) continue;
    if (strip(match[1])) return flowKeys(strip(match[1]));
    const names = [], depth = indent(lines[number]);
    for (const below of lines.slice(number + 1)) {
      const text = stripComment(below);
      if (!strip(text)) continue;
      if (indent(text) <= depth) break;
      if (lstrip(text).startsWith("- ")) names.push(strip(strip(lstrip(text).slice(2)), "\"'"));
    }
    return names;
  }
  return [];
}

function workflowRunBranchFilter(lines) {
  for (let number = 0; number < lines.length; number++) {
    const match = /^(\s*)workflow_run\s*:\s*$/.exec(stripComment(lines[number]));
    if (!match) continue;
    const depth = match[1].length;
    for (const below of lines.slice(number + 1)) {
      const text = stripComment(below);
      if (!strip(text)) continue;
      if (indent(text) <= depth) break;
      if (/^\s*branches\s*:/.test(text)) return true;
    }
  }
  return false;
}

function workflowName(path, lines) {
  for (const raw of lines) {
    const match = /^name\s*:\s*(.+)$/.exec(stripComment(raw));
    if (match) return strip(strip(match[1]), "\"'");
  }
  return path;
}

const splitLines = (text) => text.split("\r\n").join("\n").split("\n");

export function analyseAll(files) {
  const prWorkflows = new Set();
  for (const [path, text] of files) {
    const lines = splitLines(text);
    const events = triggers(lines)[0];
    if (events.includes("pull_request") || events.includes("pull_request_target")) {
      prWorkflows.add(workflowName(path, lines));
      prWorkflows.add(path);
    }
  }
  return files.map(([path, text]) => analyse(path, text, prWorkflows));
}

export function analyse(name, text, prWorkflows = null) {
  const lines = splitLines(text);
  const [events, onLine] = triggers(lines);
  const report = { file: name, triggers: events, findings: [] };
  const privileged = events.filter((e) => e === "pull_request_target" || e === "workflow_run");
  if (!privileged.length) return report;
  const add = (line, level, code, message) => report.findings.push({ file: name, line, level, code, message });

  const secrets = [...new Set([...text.matchAll(SECRET_RE)].map((m) => m[0]))].sort();
  const writes = lines.some((l) => WRITE_PERMISSION_RE.test(stripComment(l)));
  const noPermissions = !lines.some((l) => /^\s*permissions\s*:/.test(stripComment(l)));
  const power = [
    secrets.length ? "uses " + secrets.slice(0, 3).join(", ") + (secrets.length > 3 ? " and more" : "") : "",
    writes ? "grants write permissions" : "",
    noPermissions ? "sets no permissions (the token may have write access)" : "",
  ].filter(Boolean).join(", ");
  const priv = privileged.join("/");

  if (events.includes("pull_request_target")) {
    add(onLine, "warning", "PRT001",
      "Triggered by pull_request_target. From 2026-11-02 GitHub blocks this trigger on public repositories " +
      "unless an Actions policy allows it, and this workflow stops running. Move it to pull_request (plus " +
      "workflow_run for steps that need write access), or allow the trigger in Settings > Actions > Policies " +
      `after a review. ${POLICY_CHANGELOG}`);
  }

  const forkReachable = events.includes("pull_request_target") || (!workflowRunBranchFilter(lines) && (
    prWorkflows === null || upstreamWorkflows(lines).some((w) => prWorkflows.has(w))));

  for (let number = 0; number < lines.length; number++) {
    const raw = lines[number];
    const textLine = stripComment(raw);
    const uses = /\buses\s*:\s*["']?([^\s"'@]+)@?([^\s"']*)/.exec(textLine);
    if (uses && uses[1].toLowerCase() === "actions/checkout") {
      const blk = block(lines, number);
      const [ref, refLine] = value(blk, "ref");
      const [repo, repoLine] = value(blk, "repository");
      const [optedIn, optLine] = value(blk, "allow-unsafe-pr-checkout");
      const target = [ref, repo].filter(Boolean).join(" ");
      // As in Python, `continue` also skips this line's PRT004 check below.
      if (!(FORK_REF_RE.test(ref) || FORK_REPO_RE.test(repo)) || !forkReachable) continue;
      const where = refLine || repoLine || number + 1;
      if (guardedForForks(lines, blk, privileged)) continue;
      const refuses = checkoutRefuses(uses[2], raw);
      if (optedIn.toLowerCase() !== "true" && refuses) {
        add(where, "error", "PRT002",
          `Checks out pull request code (${target}) in a ${priv} workflow. Since ` +
          `2026-07-20 actions/checkout@${uses[2]} refuses this for pull requests from forks, so this ` +
          "step fails for every fork PR. Do not add allow-unsafe-pr-checkout unless the code is only read " +
          `as data; prefer running untrusted code in a pull_request workflow. ${CHECKOUT_CHANGELOG}`);
      } else if (optedIn.toLowerCase() !== "true") {
        const later = laterSteps(lines, blk[blk.length - 1][0] + 1, indent(lines[blk[0][0]]));
        const pinned = refuses === false ? `actions/checkout@${uses[2]} predates the guard`
          : `actions/checkout is pinned to ${uses[2].slice(0, 12)}, which may predate the guard`;
        add(where, later && power ? "error" : "warning", "PRT006",
          `Checks out pull request code (${target}) in a ${priv} workflow, and ${pinned} ` +
          "(2026-07-20), so fork code still gets checked out here" +
          (power ? `; the workflow ${power}` : "") +
          (later ? "; later steps run after the checkout" : "") +
          ". Fix the workflow first: updating the pin alone makes the step fail for fork PRs. " +
          `${SECURE_GUIDE}`);
      } else {
        const later = laterSteps(lines, blk[blk.length - 1][0] + 1, indent(lines[blk[0][0]]));
        add(optLine || where, "warning", "PRT003",
          "Opts in to checking out fork code with allow-unsafe-pr-checkout" +
          (power ? `; the workflow ${power}` : "") +
          (later ? "; and later steps run after the checkout" : "") +
          ". A fork can then run code with this workflow's token and secrets (a pwn request) unless " +
          `no later step executes the checked-out files. ${SECURE_GUIDE}`);
      }
    } else if (uses && AI_ACTION_RE.test(uses[1])) {
      add(number + 1, "notice", "PRT005",
        `Runs ${uses[1]} (an AI or review action) on ${priv}. ` +
        (events.includes("pull_request_target") ? "It stops running on 2026-11-02 unless the trigger is allowed by an Actions policy. " : "") +
        "Keep model output away from privileged steps: a pull request can steer an AI reviewer.");
    }
    if (GIT_PR_FETCH_RE.test(textLine) && forkReachable && !guardedForForks(lines, block(lines, number), privileged)) {
      add(number + 1, "warning", "PRT004",
        "Fetches pull request code with git or gh in a privileged workflow. actions/checkout's guard " +
        "does not cover this, so fork code still reaches a job that " + (power || "has repository access") +
        `. ${SECURE_GUIDE}`);
    }
  }
  return report;
}

export function summarise(reports) {
  const findings = reports.flatMap((r) => r.findings);
  const codes = new Set(findings.map((f) => f.code));
  let verdict = "not_affected";
  if (codes.has("PRT006")) verdict = "fork_code_in_privileged_workflow";
  else if (codes.has("PRT002")) verdict = "failing_for_fork_prs";
  else if (codes.has("PRT001")) verdict = "affected_on_2026_11_02";
  else if (codes.has("PRT003") || codes.has("PRT004")) verdict = "review_privileged_checkout";
  return {
    verdict, workflows: reports.length,
    pull_request_target_workflows: reports.filter((r) => r.triggers.includes("pull_request_target")).length,
    findings: findings.length, codes: [...codes].sort(),
  };
}

export const VERDICT_TEXT = {
  fork_code_in_privileged_workflow: "Checks out fork code in a privileged workflow with a pinned actions/checkout that predates the 2026-07-20 guard: fix this first.",
  failing_for_fork_prs: "Already failing for fork pull requests (actions/checkout refuses the checkout since 2026-07-20).",
  affected_on_2026_11_02: "Affected on 2026-11-02: pull_request_target workflows stop running without an allowing Actions policy.",
  review_privileged_checkout: "Runs fork code in a privileged workflow: review before 2026-11-02.",
  not_affected: "Not affected: no pull_request_target workflows and no fork-code checkouts in workflow_run.",
};

export function markdown(reports, summary, source) {
  const lines = [`## prt-check: ${source}`, "", `**${VERDICT_TEXT[summary.verdict]}**`, "",
    `${summary.workflows} workflow files, ${summary.pull_request_target_workflows} triggered by ` +
    `pull_request_target, ${summary.findings} findings.`, ""];
  const findings = reports.flatMap((r) => r.findings);
  if (findings.length) {
    lines.push("| Level | Code | Where | What to do |", "|---|---|---|---|");
    for (const f of findings) lines.push(`| ${f.level} | ${f.code} | \`${f.file}:${f.line}\` | ${f.message.split("|").join("/")} |`);
  }
  return lines.join("\n") + "\n";
}

export const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/;
