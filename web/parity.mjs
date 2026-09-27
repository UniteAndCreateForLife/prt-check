// Runs the JavaScript port on a corpus written by export_parity.py and compares every report with Python's.
//   node web/parity.mjs CORPUS.jsonl
import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import { analyseAll, markdown, summarise } from "./prt_check.mjs";

const corpus = process.argv[2];
let repos = 0, workflows = 0, findings = 0, mismatches = 0;
const shown = [];
for await (const line of createInterface({ input: createReadStream(corpus, "utf8"), crlfDelay: Infinity })) {
  if (!line.trim()) continue;
  const row = JSON.parse(line);
  const reports = analyseAll(row.files);
  const summary = summarise(reports);
  const got = JSON.stringify({ summary, workflows: reports, markdown: markdown(reports, summary, row.repo) });
  const want = JSON.stringify({ summary: row.summary, workflows: row.workflows, markdown: row.markdown });
  repos++;
  workflows += reports.length;
  findings += summary.findings;
  if (got !== want) {
    mismatches++;
    if (shown.length < 5) {
      const a = reports.flatMap((r) => r.findings).map((f) => `${f.file}:${f.line} ${f.level} ${f.code}`);
      const b = row.workflows.flatMap((r) => r.findings).map((f) => `${f.file}:${f.line} ${f.level} ${f.code}`);
      shown.push({ repo: row.repo, js_only: a.filter((x) => !b.includes(x)), py_only: b.filter((x) => !a.includes(x)),
        triggers_differ: reports.some((r, i) => JSON.stringify(r.triggers) !== JSON.stringify(row.workflows[i].triggers)) });
    }
  }
}
console.log(JSON.stringify({ repos, workflows, findings, mismatches, examples: shown }, null, 2));
process.exit(mismatches ? 1 : 0);
