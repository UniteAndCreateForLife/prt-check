# What GitHub's `pull_request_target` changes break in the 1,000 most-starred repositories

*Scanned 2026-09-26, 37 days before GitHub's default block on 2026-11-02, with [prt-check](README.md). Aggregate numbers only: this report names no repository.*

## The numbers

- **269 of 1,000 (26.9%)** run at least one workflow on `pull_request_target` (540 workflow files). Unless their maintainers allow the trigger in an Actions policy, those workflows stop running on 2026-11-02.
- **9 (0.9%)** check out fork pull request code in a privileged workflow, with an `actions/checkout` that has the new guard and no condition that keeps forks out. Since 2026-07-20 the guard refuses those checkouts, so the steps fail for pull requests from forks. Label-gated ones fail when the label is added.
- **3** check out fork code in a privileged workflow with `actions/checkout` pinned to a version or commit from before the guard. The guard doesn't apply to them, so the fork's code is checked out where the workflow's token and secrets are. They are the most urgent to fix, and this report does not name them.
- **4** opted in with `allow-unsafe-pr-checkout: true`: a deliberate decision, safe only if no later step executes the fork's files.
- **9** fetch pull request code with `git fetch ...pull/...` or `gh pr checkout` in a privileged workflow. That path bypasses the new guard in `actions/checkout`.
- **8** run an AI or review action on `pull_request_target`. Those reviewers go quiet on 2026-11-02 unless the trigger is allowed.

## What runs on `pull_request_target` today

The actions used most often in these workflows, by the number of repositories that use them there:

| Action | Repositories |
|---|---|
| `actions/github-script` | 125 |
| `actions/labeler` | 50 |
| `actions/create-github-app-token` | 25 |
| `actions/setup-node` | 22 |
| `actions/setup-python` | 16 |
| `amannn/action-semantic-pull-request` | 13 |
| `eps1lon/actions-label-merge-conflict` | 12 |
| `actions/upload-artifact` | 12 |
| `contributor-assistant/github-action` | 10 |
| `step-security/harden-runner` | 6 |
| `dorny/paths-filter` | 6 |
| `actions/download-artifact` | 6 |

AI and review actions on the trigger:

| Action | Repositories |
|---|---|
| `anthropics/claude-code-action` | 5 |
| `presubmit/ai-reviewer` | 1 |
| `anthropics/claude-code-base-action` | 1 |
| `openai/codex-action` | 1 |

## Method

- Selection: the 1,000 most-starred public, non-fork, non-archived repositories, from GitHub's repository search on 2026-09-26. 809 of them have workflow files (9,328 files in total). 0 could not be read.
- Only `.github/workflows/*.yml` and `*.yaml` on the default branch were read, through the GitHub API and raw.githubusercontent.com. No workflow was run, and nothing was written anywhere.
- Each file went through `prt_check.py`, which reads YAML line by line. [README](README.md#what-it-reports) lists the codes, and the tests show the layouts it understands.
- Every repository behind the second and third numbers was reviewed by hand, and the checker was corrected wherever the review found it wrong: job conditions, Dependabot-only jobs, `workflow_run` upstreams and branch filters, and commit pins resolved to exact `actions/checkout` releases.
- Actions policies (repository, organization or enterprise) are not visible from outside, so some of these repositories may already allow `pull_request_target` and keep running it after 2026-11-02. The numbers count workflows that depend on the trigger, not workflows that will certainly stop.

## What to do

Run `prt-check` on your repository (Action or one command), then follow the three fixes in the [README](README.md#how-to-fix): switch to `pull_request`, split into `pull_request` + `workflow_run`, or keep the trigger deliberately with an Actions policy.

