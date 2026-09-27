"""The browser port (web/prt_check.mjs) must report exactly what prt_check.py reports."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
import export_parity  # noqa: E402

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")

# Layouts the unit tests do not spell out as whole files: guarded jobs, flow-style triggers, pins.
EXTRA = [
    ("on: [push, pull_request_target]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
     "      - uses: actions/checkout@v3.6.0\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
     "      - run: make test\n        env:\n          TOKEN: ${{ secrets.DEPLOY_TOKEN }}\n"),
    ("'on':\n  pull_request_target:\n    types: [labeled]\njobs:\n  a:\n"
     "    if: github.event.pull_request.head.repo.full_name == github.repository\n    steps:\n"
     "      - uses: actions/checkout@v4\n        with:\n          ref: refs/pull/${{ github.event.number }}/merge\n"),
    ("name: Build\non:\n  pull_request:\n"),
    ("on:\n  workflow_run:\n    workflows: [Build]\n    types: [completed]\npermissions:\n  contents: write\n"
     "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@8e5e7e5ab8b370d6c329ec480221332ada57f0ab\n"
     "        with:\n          ref: ${{ github.event.workflow_run.head_sha }}  # comment\n"
     "      - run: git fetch origin pull/1/head\n      - uses: coderabbitai/ai-pr-reviewer@v1\n"),
]


def test_the_browser_port_matches_the_cli(tmp_path):
    rows = list(export_parity.test_workflows(ROOT / "tests" / "test_prt_check.py"))
    rows.append(("extra", [(f".github/workflows/x{i}.yml", text) for i, text in enumerate(EXTRA)]))
    corpus = tmp_path / "corpus.jsonl"
    assert export_parity.write(iter(rows), corpus) == len(rows)
    done = subprocess.run([NODE, str(ROOT / "web" / "parity.mjs"), str(corpus)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert '"mismatches": 0' in done.stdout
