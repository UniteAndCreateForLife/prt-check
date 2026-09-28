from dataclasses import dataclass

import pytest

import prt_discord as D


@dataclass
class Finding:
    file: str
    line: int
    level: str
    code: str
    message: str


@dataclass
class Report:
    findings: list


def test_limiter_enforces_fixed_window_per_user():
    limiter = D.ScanLimiter(max_calls=2, window_s=60)
    assert limiter.allow(1, now=0)
    assert limiter.allow(1, now=1)
    assert not limiter.allow(1, now=2)
    assert limiter.allow(2, now=2)
    assert limiter.allow(1, now=61)


def test_limiter_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        D.ScanLimiter(max_calls=0)
    with pytest.raises(ValueError):
        D.ScanLimiter(window_s=0)


def test_render_scan_prioritizes_errors(monkeypatch):
    monkeypatch.setattr(D.P, "VERDICT_TEXT", {"x": "headline"})
    findings = [
        Finding("b.yml", 2, "notice", "N", "notice"),
        Finding("a.yml", 3, "warning", "W", "warning"),
        Finding("a.yml", 1, "error", "E", "error"),
    ]
    text = D.render_scan(
        "o/r",
        [Report(findings)],
        {
            "verdict": "x",
            "workflows": 2,
            "pull_request_target_workflows": 1,
            "findings": 3,
        },
    )
    assert text.index("E error") < text.index("W warning") < text.index("N notice")
    assert "**prt-check `o/r`**" in text


def test_render_scan_truncates_to_discord_safe_length(monkeypatch):
    monkeypatch.setattr(D.P, "VERDICT_TEXT", {"x": "h"})
    finding = Finding("a.yml", 1, "error", "E", "x" * 4000)
    text = D.render_scan(
        "o/r",
        [Report([finding])],
        {
            "verdict": "x",
            "workflows": 1,
            "pull_request_target_workflows": 1,
            "findings": 1,
        },
        max_chars=500,
    )
    assert len(text) <= 500
    assert "truncated" in text


def test_scan_repository_reuses_core_scanner(monkeypatch):
    calls = []
    monkeypatch.setattr(
        D.P,
        "read_remote",
        lambda repo, token="": calls.append((repo, token)) or [("x.yml", "text")],
    )
    monkeypatch.setattr(D.P, "analyse_all", lambda files: [Report([])])
    monkeypatch.setattr(
        D.P,
        "summarise",
        lambda reports: {
            "verdict": "not_affected",
            "workflows": 1,
            "pull_request_target_workflows": 0,
            "findings": 0,
        },
    )
    monkeypatch.setattr(D.P, "VERDICT_TEXT", {"not_affected": "ok"})
    out = D.scan_repository("owner/repo", "token")
    assert calls == [("owner/repo", "token")]
    assert "ok" in out
