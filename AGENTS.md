# Agent instructions — givenergy-cli

This file provides context and working rules for AI agents operating on this repository.

## What this repo is

A command-line tool (`givenergy-cli`) for monitoring and interacting with GivEnergy inverters over the local network. It is a thin CLI/TUI layer built on top of the [`givenergy-modbus`](https://github.com/dewet22/givenergy-modbus) library. Keep changes scoped to the CLI layer; do not reach into the library to work around CLI-level problems.

## Scope and sister repos

This agent owns `givenergy-cli` only. Two sibling repositories exist, each with their own dedicated agent:

- **`givenergy-modbus`** — the Modbus TCP client and data model
- **`givenergy-hass`** — the Home Assistant integration

When a CLI change requires corresponding work in a sister repo, communicate via the **shared coordination inbox** at `/tmp/givenergy-coordination/`.

### Coordination inbox protocol

- **Shared directory:** `/tmp/givenergy-coordination`
- **Filename format:** `<unix-epoch>-<recipient>-<description>.md`
  - `recipient` is one of `cli`, `modbus`, or `hass`
  - `description` is a brief slug, optionally referencing an issue (e.g. `mock-pdu-logging-#42`)
  - Example: `1780409632-modbus-mock-pdu-logging.md`
- **Writing a message:** create a new file; never mutate an existing one
- **Replying:** create a new file with the current epoch, the original sender as addressee, and a description prefixed with `re-`. Only reply if actionable, save on pleasantries.
- **Content:** describe the expected outcome at the API boundary — not how to implement it; include enough context to act without this conversation's history. It does not need to be overly verbose since agents share a lot of common knowledge across these repos.
- **Scanning:** after every turn, scan the inbox for new files through the stop hook and script defined in `.claude/settings.json`; make a decision whether to immediately act on the message, park it for when the current work winds up, or handing off to a subagent. Check with the user if any uncertainty.

The old `.claude/handoffs/` and bare `/tmp` locations are superseded by this inbox.

## Tooling

This project uses `uv` for dependency management.

```bash
uv sync                        # install / sync deps
uv add --dev <tool>            # add a dev dependency
uv run <tool>                  # invoke any tool (ruff, pytest, etc.)
```

Before considering any Python change done:

```bash
uv run ruff check --fix        # lint and auto-fix
uv run ruff format             # format
uv run pytest                  # run the test suite
```

Do not use `uvx`, system-installed binaries, or `pip` for project tooling — everything should be pinned in the lockfile.

## Pre-commit hooks

Hooks run automatically on `git commit` via **`prek`** (a Rust pre-commit framework), configured in `prek.toml` — not `.pre-commit-config.yaml`. Run them manually with `prek run`. The set includes ruff (`--fix`) + ruff-format, mypy, bandit (`--exclude=tests`), codespell, and the pre-commit-hooks/pygrep batteries. Fix failures rather than skipping hooks (`--no-verify`). If a commit fails due to a hook, fix the issue and create a new commit — do not amend the previous one.

Two hooks have bitten us before:
- **`name-tests-test --django`** requires test files to be named `test_*.py` (not `*_test.py`).
- **`codespell`** flags `hass` → `hash`; project-specific terms are allow-listed in `.codespellrc` — add to `ignore-words-list` there rather than rewording legitimate uses.

## Commits and branches

- Conventional commits: `feat:`, `fix:`, `build:`, `docs:`, `refactor:`, `chore:`, etc.
- One logical change per commit.
- Git worktrees go under `.claude/worktrees/<name>/` — not `/tmp` or outside the project root.

## Releases

Releases are triggered via GitHub Actions `workflow_dispatch` — not by pushing a tag:

```bash
gh workflow run release.yml --field bump=<major|minor|patch>
```

The workflow handles versioning, CHANGELOG generation, tagging, building, and publishing to PyPI. Preview the generated CHANGELOG section before triggering with:

```bash
uv run python scripts/release.py generate <next-version> --preview
```

For multi-commit work accumulated on `main`, prefer a PR-based review flow over a direct release.

## GitHub identity — bot vs your voice

GitHub interactions split into two identities. The rule of thumb: **anything that
publishes prose as the user goes out under their keyring auth; mechanical, structural,
and read-only actions go out under the automation bot.**

- **Git pushes are unaffected** — git uses SSH, so commits/pushes always go under the
  user's key regardless of token. This split only governs `gh` / `gh api` calls.
- `gh` token precedence is `GH_TOKEN` > `GITHUB_TOKEN` > keyring.
- Bot identity is `dewet22-claude`; the user's keyring identity is `dewet22`.

**Bot identity (autonomous)** — prefix with `source "$CLAUDE_CONFIG_DIR/gh-env" &&` to
load the bot `GH_TOKEN`, e.g. `source "$CLAUDE_CONFIG_DIR/gh-env" && gh pr merge 35`. Covers:
- All reads: `gh pr checks/view/list/diff`, `gh run list/view/watch`, `gh api` GETs,
  `gh issue/release/repo view/list`, `gh search`
- `gh workflow run` (release trigger)
- `gh pr merge`
- Resolving review threads (GraphQL `resolveReviewThread`)
- Labels (`gh label`, `gh pr edit --add-label`)

**Your voice (the user)** — force keyring auth by prefixing with
`env -u GH_TOKEN -u GITHUB_TOKEN gh …`, so it pins to the user regardless of any bot
token in the environment. Covers anything that authors prose as the user:
- Review-thread replies (`gh api …/comments/…/replies`)
- `gh pr comment` / `gh issue comment`
- PR review submissions (`gh pr review`)
- `gh pr create` (title/body are prose authored as the user)
- `gh issue create`, closing with a comment
- Editing PR/issue descriptions

The bot token lives at `$CLAUDE_CONFIG_DIR/gh-env` (`export GH_TOKEN=…`) and needs `repo`
(read, merge, labels) and `workflow` (trigger releases) scopes. This is a shared
cross-agent convention — the modbus and hass agents follow the same split.

## Python version notes

The project targets Python 3.14+. Unparenthesised multi-exception catch is valid syntax at this baseline:

```python
except TypeError, ValueError:   # correct — do not add parens
```

Ruff will enforce this form. Ignore reviewer suggestions to parenthesise it.

## Codebase conventions

- Broad `except Exception` clauses should carry `# noqa: BLE001` to match the project convention.
- The `_silence_shutdown_noise()` context manager in `registers.py` must wrap any `asyncio.run()` call that closes a `Client` — the modbus shutdown emits noisy CRITICAL log lines otherwise.
- Client lifecycle: always close in a `try/finally` block.

## Testing

Tests live in `tests/test_*.py` and run with `uv run pytest` (no coverage plugin installed — `--cov` will error). CLI commands are tested with `typer.testing.CliRunner`:

- **Monkeypatch the delegating function** (e.g. `cli.probe_registers`, `cli.serve_mock`) rather than opening real connections — patch the name imported into `__main__`, and assert on the recorded kwargs. Set `env={"GIVENERGY_HOST": "127.0.0.1"}` when the command requires a host.
- **When asserting on rich-rendered error text**, pass `env={"COLUMNS": "200"}` — otherwise rich wraps the message across the error-panel border at 80 cols and substring matches fail.
- `.codacy.yaml` excludes `tests/**` from static analysis (Codacy's bandit flags pytest `assert` as B101); this mirrors prek's `bandit --exclude=tests`. Don't weaken test asserts to appease it.

## Hardware context

The GivEnergy inverter's local Modbus link is hardware-flaky:

- Transient bad register values are normal (e.g. enum fields with out-of-range integers).
- Timeouts occur even with generous `timeout` and `retries` settings.
- A single failed export is not evidence of a bug — partial data with a warning is the correct outcome.

Be defensive when decoding plant data: catch `ValueError` / `Exception` around register decoding, surface what was captured, and continue rather than crashing.
