# prt-check

**Is your repository ready for GitHub's 2026 `pull_request_target` changes?** One command or one Action step tells you, workflow by workflow, with the line to fix.

**Check a public repository in your browser:** https://uniteandcreateforlife.github.io/prt-check/ (no signup; it runs on your device with the same rules as the CLI).

Two changes break GitHub Actions workflows on public repositories this year:

| Date | Change | What breaks |
|---|---|---|
| **2026-07-20** (already live) | `actions/checkout` refuses to check out fork pull request code in `pull_request_target` workflows, and in `workflow_run` workflows started by a pull request, unless the step sets `allow-unsafe-pr-checkout: true`. It applies to every supported major version, so `@v4` picked it up too. ([GitHub changelog](https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/)) | The checkout step fails for every pull request from a fork. Same-repository pull requests still pass, so maintainers often don't notice. |
| **2026-11-02** | A default Actions policy blocks the `pull_request_target` trigger on public repositories that have no applicable policy allowing it. ([GitHub changelog](https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/), [docs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/actions-policies/workflow-execution-protections)) | The workflow stops running: labelers, welcome bots, PR-title checks, preview deploys and AI reviewers that use the trigger go quiet. |

## Run it

As a GitHub Action (it reads your workflow files; it needs no token and no permissions beyond `contents: read`):

```yaml
name: prt-check
on:
  pull_request:
    paths: [".github/workflows/**"]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  prt-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: UniteAndCreateForLife/prt-check@v1
        with:
          fail-on: none   # or: notice, warning, error
```

Findings show up as annotations on the workflow lines, plus a summary table on the run page.

Locally (Python 3.9+, no dependencies):

```bash
pipx install git+https://github.com/UniteAndCreateForLife/prt-check@v1.1.0   # or: pip install git+…
prt-check                                 # the repository in the current directory
prt-check --repo OWNER/NAME               # any public repository, through the GitHub API
prt-check --format json                   # machine-readable
```

`python prt_check.py` works the same without installing anything.

As a [pre-commit](https://pre-commit.com) hook, run whenever a workflow file changes:

```yaml
repos:
  - repo: https://github.com/UniteAndCreateForLife/prt-check
    rev: v1.1.0
    hooks:
      - id: prt-check        # fails on warnings; set args: [--fail-on, error] to fail on errors only
```

## What it reports

| Code | Level | Meaning |
|---|---|---|
| PRT001 | warning | The workflow runs on `pull_request_target`. From 2026-11-02 it stops running unless an Actions policy allows the trigger. |
| PRT002 | error | The workflow checks out fork code (the PR's head or merge SHA, `refs/pull/…`, or the fork repository) without the opt-in, using an `actions/checkout` that has the guard. That covers `@v2` to `@v7`, `@main`, and v2.8.0, v3.7.0, v4.4.0, v5.1.0, v6.1.0, v7.0.1 or later. The step already fails for every fork pull request. |
| PRT006 | error or warning | The same checkout, but pinned to a version or commit from before 2026-07-20, or to v1. The guard doesn't apply, so fork code is still checked out in the privileged job; whether it runs depends on the later steps. Fix the workflow before you update the pin: updating alone just makes the step fail. |
| PRT003 | warning | The workflow opts in with `allow-unsafe-pr-checkout: true`. The report says whether it also has secrets, write permissions or later steps, which is how a "pwn request" happens. |
| PRT004 | warning | The workflow fetches PR code with `git fetch …pull/…` or `gh pr checkout`. The guard in `actions/checkout` does not cover that. |
| PRT005 | notice | An AI or review action runs in the privileged workflow. On `pull_request_target` it stops on 2026-11-02, and a pull request can steer its output. |

The verdict for the repository is the most urgent of:
- *fork code in a privileged workflow* (PRT006);
- *already failing for fork PRs*;
- *affected on 2026-11-02*;
- *review a privileged checkout*;
- *not affected*.

A `workflow_run` workflow is checked only if a workflow it follows runs on pull requests. Otherwise it never sees fork code.

## How to fix

1. **The workflow doesn't need secrets or write access for fork PRs.** Change the trigger to `pull_request`. For fork PRs it runs with a read-only token and no secrets, which is all such a workflow needs.
2. **It needs write access, for example to comment or label.** Split it in two. A `pull_request` workflow does the untrusted work and uploads the result as an artifact, including the PR number, because `workflow_run` doesn't list pull requests from forks. A `workflow_run` workflow downloads it, checks that the PR number is a number, and comments. It never executes the PR's files. GitHub's guide [Securely using `pull_request_target`](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target) walks through the pattern.
3. **You decide to keep `pull_request_target`.** Allow the trigger in an Actions policy (Settings → Actions → Policies). Policy insights show which runs would be blocked. Check out fork code only to read it as data, with `allow-unsafe-pr-checkout: true`, and never run it.

## Limits

- It reads YAML line by line, without a YAML library. Unusual layouts (anchors, multi-document files) can be missed.
- It understands conditions that keep forks out: same-repository checks, Dependabot/Renovate-only jobs, jobs limited to non-privileged events, and `workflow_run.event == 'push'`. Other conditions, such as labels, are treated as letting forks through.
- A `workflow_run` workflow with a `branches:` filter is treated as not reached by forks. A fork can still name its branch to match, so such a filter is not a security boundary.
- Commit pins of `actions/checkout` are resolved against its releases up to 2026-09-26. A newer unknown commit counts as "may predate the guard".
- It can't see your repository's Actions policies. If one already allows `pull_request_target`, PRT001 is informational.
- AI actions are recognised by name, so a local or renamed action can be missed.
- It never runs workflow code and makes no changes.

## Who made this

[HAL SUPREME](https://github.com/UniteAndCreateForLife/HAL_SUPREME), an evidence-first AI engineering system, built this. If you'd rather have the migration done for you: [Verified fix](https://github.com/UniteAndCreateForLife/HAL_SUPREME/blob/main/docs/WORK_WITH_HAL.md#fixed-price-offer-verified-fix) delivers a tested pull request, and you pay only if you merge.

Licensed under Apache-2.0.
