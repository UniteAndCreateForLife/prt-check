from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

import prt_check as P


@dataclass
class ScanLimiter:
    max_calls: int = 3
    window_s: float = 60.0

    def __post_init__(self) -> None:
        if self.max_calls < 1 or self.window_s <= 0:
            raise ValueError("rate limit must be positive")
        self._calls: Dict[int, Deque[float]] = defaultdict(deque)

    def allow(self, user_id: int, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        calls = self._calls[int(user_id)]
        cutoff = current - self.window_s
        while calls and calls[0] <= cutoff:
            calls.popleft()
        if len(calls) >= self.max_calls:
            return False
        calls.append(current)
        return True


def render_scan(repo: str, reports, summary: dict, *, max_chars: int = 1900) -> str:
    verdict = str(summary.get("verdict") or "unknown")
    headline = P.VERDICT_TEXT.get(verdict, verdict.replace("_", " "))
    lines = [
        f"**prt-check `{repo}`**",
        headline,
        (
            f"{summary.get('workflows', 0)} workflow files · "
            f"{summary.get('pull_request_target_workflows', 0)} pull_request_target · "
            f"{summary.get('findings', 0)} findings"
        ),
    ]
    findings = [finding for report in reports for finding in report.findings]
    severity = {"error": 0, "warning": 1, "notice": 2}
    findings.sort(
        key=lambda finding: (
            severity.get(finding.level, 9),
            finding.file,
            finding.line,
        )
    )
    for finding in findings[:8]:
        lines.append(
            f"- **{finding.code} {finding.level}** "
            f"`{finding.file}:{finding.line}` — {finding.message}"
        )
    if len(findings) > 8:
        lines.append(
            f"- …and {len(findings) - 8} more findings; "
            "run the CLI for the full report."
        )
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    suffix = (
        "\n…truncated; run `prt-check --repo OWNER/NAME` "
        "for the complete report."
    )
    return text[: max(1, max_chars - len(suffix))].rstrip() + suffix


def scan_repository(repo: str, token: str = "") -> str:
    files = P.read_remote(repo, token)
    reports = P.analyse_all(files)
    summary = P.summarise(reports)
    return render_scan(repo, reports, summary)


def _required_int(name: str) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be positive")
    return parsed


def build_client():
    try:
        import discord
        from discord import app_commands
    except ImportError as exc:
        raise RuntimeError(
            "Discord support is optional; "
            "install with `pip install 'prt-check[discord]'`."
        ) from exc

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")

    guild_id = _required_int("DISCORD_GUILD_ID")
    owner_id = _required_int("HAL_DISCORD_OWNER_ID")
    channel_raw = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    channel_id = int(channel_raw) if channel_raw else 0
    github_token = os.environ.get("GITHUB_TOKEN", "")

    guild = discord.Object(id=guild_id)
    limiter = ScanLimiter()

    class PRTClient(discord.Client):
        def __init__(self):
            super().__init__(intents=discord.Intents.none())
            self.tree = app_commands.CommandTree(self)

        async def setup_hook(self):
            await self.tree.sync(guild=guild)

    client = PRTClient()

    def channel_allowed(interaction) -> bool:
        return not channel_id or int(interaction.channel_id or 0) == channel_id

    @client.tree.command(
        name="scan",
        description=(
            "Check a public GitHub repository for the 2026 "
            "pull_request_target changes."
        ),
        guild=guild,
    )
    @app_commands.describe(repository="Public GitHub repository as OWNER/NAME")
    async def scan(interaction: discord.Interaction, repository: str):
        if not channel_allowed(interaction):
            await interaction.response.send_message(
                "Use /scan in the configured prt-check channel.",
                ephemeral=True,
            )
            return
        if not limiter.allow(interaction.user.id):
            await interaction.response.send_message(
                "Rate limit: up to 3 scans per minute per user.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    scan_repository,
                    repository,
                    github_token,
                ),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            result = (
                "prt-check timed out while reading that public repository. "
                "Try again shortly."
            )
        except Exception as exc:
            result = (
                f"prt-check could not scan `{repository}`: "
                f"{type(exc).__name__}"
            )
        await interaction.followup.send(result)

    @client.tree.command(
        name="halt",
        description="Owner-only: stop the prt-check bot.",
        guild=guild,
    )
    async def halt(interaction: discord.Interaction):
        if int(interaction.user.id) != owner_id:
            await interaction.response.send_message(
                "Owner-only command.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Stopping prt-check bot.",
            ephemeral=True,
        )
        await client.close()

    return client, token


def main() -> int:
    client, token = build_client()
    client.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
