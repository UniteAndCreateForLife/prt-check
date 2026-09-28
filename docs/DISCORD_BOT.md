# Discord scan bot

The Discord adapter turns the existing read-only `prt-check` scanner into one
bounded community command:

```
/scan OWNER/REPO
```

The scanner core is unchanged and remains dependency-free. Discord support is an
optional install:

```bash
pip install "prt-check[discord]"
```

## Required configuration

- `DISCORD_BOT_TOKEN` — bot token, stored outside Git.
- `DISCORD_GUILD_ID` — the one server where commands are registered.
- `HAL_DISCORD_OWNER_ID` — user allowed to run `/halt`.
- `DISCORD_CHANNEL_ID` — optional single channel for `/scan`.
- `GITHUB_TOKEN` — optional read token to increase GitHub API allowance.

Start it with:

```bash
prt-check-discord
```

## Boundaries

- No privileged Discord intents are requested.
- `/scan` reads public GitHub workflow files only.
- Per-user limit is 3 scans per 60 seconds.
- The command times out after 25 seconds.
- Output is capped below Discord's message limit.
- `/halt` is owner-only.
- Wrong-channel and rate-limit responses are ephemeral.
- The bot does not DM, mass-post, moderate users, or send unsolicited messages.
- It does not write to GitHub or modify scanned repositories.

For initial rollout, register the app only in the HAL community server and keep
`DISCORD_CHANNEL_ID` set to the dedicated checker channel.
