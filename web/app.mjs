// The checker page: reads a public repository's workflows from GitHub and runs prt-check on them in the browser.
import { analyseAll, markdown, REPO_RE, summarise, VERDICT_TEXT, VERSION } from "./prt_check.mjs";

const $ = (id) => document.getElementById(id);
const form = $("form"), input = $("repo"), go = $("go"), status = $("status"), report = $("report");
$("version").textContent = `v${VERSION}`;

const DEADLINE = new Date("2026-11-02T00:00:00Z");
const days = Math.ceil((DEADLINE - new Date()) / 86400000);
$("countdown").textContent = days > 1 ? `${days} days until Nov 2, 2026` : days === 1 ? "1 day until Nov 2, 2026"
  : "GitHub changed the default on Nov 2, 2026";

const TITLES = {
  PRT001: "Stops running on Nov 2 without an Actions policy",
  PRT002: "Already failing for pull requests from forks",
  PRT003: "Opted in to checking out fork code",
  PRT004: "Fetches pull request code outside actions/checkout",
  PRT005: "AI or review action on a privileged trigger",
  PRT006: "Fork code checked out by a checkout pin older than the guard",
};
const TONE = { not_affected: "", affected_on_2026_11_02: "warn", review_privileged_checkout: "warn",
  failing_for_fork_prs: "bad", fork_code_in_privileged_workflow: "bad" };
const ORDER = { error: 0, warning: 1, notice: 2 };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null && child !== false) node.append(child);
  return node;
}

// Message text with its https links made clickable (as text nodes and anchors, never as HTML).
function linkify(text) {
  return text.split(/(https:\/\/\S+[^\s.,;)])/).map((part, i) => (i % 2 ? el("a", { href: part }, part) : part));
}

function parseRepo(text) {
  const cleaned = text.trim().replace(/^https?:\/\//, "").replace(/^(www\.)?github\.com\//, "").replace(/\.git$/, "");
  const [owner, name] = cleaned.split(/[/?#]/).filter(Boolean);
  const repo = owner && name ? `${owner}/${name}` : "";
  return REPO_RE.test(repo) ? repo : "";
}

class ScanError extends Error {}

async function github(path) {
  const response = await fetch(`https://api.github.com/repos/${path}`, { headers: { Accept: "application/vnd.github+json" } });
  if (response.status === 403 || response.status === 429) {
    if (response.headers.get("x-ratelimit-remaining") === "0") {
      const reset = new Date(Number(response.headers.get("x-ratelimit-reset")) * 1000);
      throw new ScanError(`GitHub allows 60 requests an hour from your network without signing in, and they're used up. ` +
        `Try again after ${reset.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}, or run the CLI locally.`);
    }
  }
  return response;
}

async function fetchWorkflows(repo, sha) {
  let response = await github(`${repo}/commits/${sha || "HEAD"}`);
  if (response.status === 404 || response.status === 422) throw new ScanError(`${repo} wasn't found. It may be private, renamed, or empty.`);
  if (!response.ok) throw new ScanError(`GitHub answered ${response.status} for ${repo}.`);
  const commit = (await response.json()).sha;
  response = await github(`${repo}/contents/.github/workflows?ref=${commit}`);
  if (response.status === 404) return { commit, files: [] };
  if (!response.ok) throw new ScanError(`GitHub answered ${response.status} when listing ${repo}'s workflows.`);
  const listing = await response.json();
  const items = (Array.isArray(listing) ? listing : []).filter((item) =>
    item.type === "file" && /\.ya?ml$/.test(item.name || "") && item.download_url);
  const files = [];
  for (let i = 0; i < items.length; i += 6) {
    const batch = items.slice(i, i + 6).map(async (item) => {
      const raw = await fetch(item.download_url);
      if (!raw.ok) throw new ScanError(`Couldn't read ${item.name} (${raw.status}).`);
      return [`.github/workflows/${item.name}`, await raw.text()];
    });
    files.push(...await Promise.all(batch));
  }
  return { commit, files };
}

function copyButton(label, text) {
  const button = el("button", { class: "secondary", type: "button" }, label);
  button.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text()); button.textContent = "Copied"; }
    catch { button.textContent = "Copy failed"; }
    setTimeout(() => { button.textContent = label; }, 1600);
  });
  return button;
}

function render({ repo, commit, files, sample }) {
  const reports = analyseAll(files);
  const summary = summarise(reports);
  const findings = reports.flatMap((r) => r.findings).sort((a, b) => ORDER[a.level] - ORDER[b.level]);
  const source = sample ? "sample workflows" : `${repo}@${commit.slice(0, 7)}`;
  const permalink = sample ? "" : `${location.origin}${location.pathname}?repo=${repo}&sha=${commit}`;
  const blob = (file, line) => `https://github.com/${repo}/blob/${commit}/${file}#L${line}`;

  const meta = el("div", { class: "meta" },
    sample ? el("span", {}, "Sample workflows, analysed offline")
      : el("span", {}, el("a", { href: `https://github.com/${repo}` }, repo), " at ",
        el("a", { href: `https://github.com/${repo}/commit/${commit}`, class: "mono" }, commit.slice(0, 7))),
    el("span", {}, `${summary.workflows} workflow file${summary.workflows === 1 ? "" : "s"}`),
    el("span", {}, `${summary.pull_request_target_workflows} on pull_request_target`),
    el("span", {}, `${summary.findings} finding${summary.findings === 1 ? "" : "s"}`));
  const actions = el("div", { class: "actions" },
    copyButton("Copy report as Markdown", () => markdown(reports, summary, source)),
    permalink && copyButton("Copy link to this result", () => permalink));
  const verdict = el("div", { class: `verdict ${TONE[summary.verdict]}` },
    el("h2", {}, summary.workflows === 0 ? "No workflow files: nothing here is affected." : VERDICT_TEXT[summary.verdict]),
    meta, actions);

  const cards = findings.map((f) => el("div", { class: "finding" },
    el("div", { class: "finding-head" },
      el("span", { class: `badge ${f.level}` }, f.level), el("span", { class: "mono" }, f.code),
      el("span", { class: "finding-title" }, TITLES[f.code] || "")),
    el("div", { class: "where mono" }, sample ? `${f.file}:${f.line}` : el("a", { href: blob(f.file, f.line) }, `${f.file}:${f.line}`)),
    el("div", { class: "msg" }, linkify(f.message))));

  const affected = [...new Set(findings.filter((f) => f.code === "PRT001").map((f) => f.file))];
  const next = el("div", { class: "next" }, el("div", { class: "next-grid" },
    affected.length ? el("div", {},
      el("h3", {}, "Before Nov 2"),
      el("p", { class: "msg" }, "Either allow the trigger with an Actions policy scoped to just these files (a repository admin can add one; review them first), or move them to pull_request, with a workflow_run job for steps that need write access:"),
      el("pre", {}, affected.join("\n"))) : null,
    el("div", {},
      el("h3", {}, "Keep it checked"),
      el("p", { class: "msg" }, "Add the free Action, and new workflow changes are checked on every pull request:"),
      el("pre", {}, "- uses: UniteAndCreateForLife/prt-check@v1\n  with:\n    fail-on: warning")),
    summary.findings ? el("div", {},
      el("h3", {}, "Want the fix as a pull request?"),
      el("p", { class: "msg" }, "HAL SUPREME prepares it, re-runs your tests on a clean checkout, and discloses the AI use. You pay only if you merge. ",
        el("a", { href: "https://github.com/UniteAndCreateForLife/HAL_SUPREME/blob/main/docs/WORK_WITH_HAL.md#fixed-price-offer-verified-fix" }, "How it works"))) : null));

  report.replaceChildren(verdict, ...cards, next);
  report.hidden = false;
  status.textContent = "";
  status.className = "status";
}

async function run(repo, sha = "") {
  go.disabled = true;
  report.hidden = true;
  status.className = "status";
  status.textContent = `Reading ${repo}'s workflows from GitHub…`;
  try {
    const { commit, files } = await fetchWorkflows(repo, sha);
    render({ repo, commit, files });
    history.replaceState(null, "", `?repo=${repo}`);
  } catch (error) {
    status.className = "status error";
    status.textContent = error instanceof ScanError ? error.message : `Something went wrong: ${error.message}`;
  } finally {
    go.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const repo = parseRepo(input.value);
  if (!repo) {
    status.className = "status error";
    status.textContent = "Enter a public repository as owner/name, for example UniteAndCreateForLife/prt-check.";
    return;
  }
  input.value = repo;
  run(repo);
});
for (const chip of document.querySelectorAll("[data-repo]")) {
  chip.addEventListener("click", () => { input.value = chip.dataset.repo; run(chip.dataset.repo); });
}

const SAMPLE = [
  [".github/workflows/label.yml",
    "name: Label\non: pull_request_target\npermissions:\n  pull-requests: write\njobs:\n  label:\n    runs-on: ubuntu-latest\n" +
    "    steps:\n      - uses: actions/labeler@v5\n"],
  [".github/workflows/preview.yml",
    "name: Preview\non:\n  pull_request_target:\n    types: [opened, synchronize]\njobs:\n  build:\n    runs-on: ubuntu-latest\n" +
    "    steps:\n      - uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n" +
    "      - run: npm ci && npm run build\n        env:\n          DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}\n"],
  [".github/workflows/review.yml",
    "name: Review\non:\n  pull_request_target:\njobs:\n  review:\n    runs-on: ubuntu-latest\n    steps:\n" +
    "      - uses: actions/checkout@v3.1.0\n        with:\n          ref: refs/pull/${{ github.event.number }}/merge\n" +
    "      - run: git fetch origin pull/${{ github.event.number }}/head\n      - uses: coderabbitai/ai-pr-reviewer@v1\n"],
];
$("sample").addEventListener("click", () => {
  input.value = "";
  history.replaceState(null, "", location.pathname);
  render({ repo: "", commit: "", files: SAMPLE, sample: true });
});

const params = new URLSearchParams(location.search);
const initial = parseRepo(params.get("repo") || "");
if (initial) {
  input.value = initial;
  run(initial, /^[0-9a-f]{7,40}$/.test(params.get("sha") || "") ? params.get("sha") : "");
}
