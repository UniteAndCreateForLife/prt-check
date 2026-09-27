"""Writes a parity corpus: workflow files plus what prt_check.py reports for them, one repository per line.

    python web/export_parity.py CACHE_DIR OUT.jsonl        # CACHE_DIR/<owner__name>/<workflow files>
    python web/export_parity.py --tests TEST_FILE OUT.jsonl  # every workflow string in a test file

web/parity.mjs then runs the JavaScript port on the same strings and fails on any difference.
"""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prt_check  # noqa: E402

Files = List[Tuple[str, str]]


def cached_repositories(cache: Path) -> Iterator[Tuple[str, Files]]:
    for folder in sorted(p for p in cache.iterdir() if p.is_dir()):
        names = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))
        yield folder.name, [(f".github/workflows/{n}", (folder / n).read_bytes().decode("utf-8", "replace"))
                            for n in names]


def test_workflows(test_file: Path) -> Iterator[Tuple[str, Files]]:
    tree = ast.parse(test_file.read_text(encoding="utf-8"))
    strings = sorted({n.value for n in ast.walk(tree)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str) and "jobs:" in n.value})
    for i, text in enumerate(strings):
        yield f"case{i}", [(f".github/workflows/case{i}.yml", text)]


def write(rows: Iterator[Tuple[str, Files]], out: Path) -> int:
    count = 0
    with out.open("w", encoding="utf-8") as handle:
        for name, files in rows:
            reports = prt_check.analyse_all(files)
            summary = prt_check.summarise(reports)
            handle.write(json.dumps({"repo": name, "files": files, "summary": summary,
                                     "workflows": [asdict(r) for r in reports],
                                     "markdown": prt_check.markdown(reports, summary, name)},
                                    ensure_ascii=False) + "\n")
            count += 1
    return count


def main(argv: List[str]) -> int:
    if argv[:1] == ["--tests"]:
        count = write(test_workflows(Path(argv[1])), Path(argv[2]))
    else:
        count = write(cached_repositories(Path(argv[0])), Path(argv[1]))
    print(f"{count} repositories -> {argv[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
