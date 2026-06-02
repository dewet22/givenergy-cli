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

- **Filename format:** `<unix-epoch>-<recipient>-<description>.md`
  - `recipient` is one of `cli`, `modbus`, or `hass`
  - `description` is a brief slug, optionally referencing an issue (e.g. `mock-pdu-logging-#42`)
  - Example: `1780409632-modbus-mock-pdu-logging.md`
- **Writing a message:** create a new file; never mutate an existing one
- **Replying:** create a new file with the current epoch, the original recipient as addressee, and a description prefixed with `re-`
- **Content:** describe the expected outcome at the API boundary — not how to implement it; include enough context to act without this conversation's history
- **Scanning:** after every turn, scan the inbox for new `*-cli-*.md` files and act on any that haven't been seen (the Stop hook in `.claude/settings.json` does this automatically)

The old `.claude/handoffs/` location is superseded by this inbox.

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

Pre-commit runs automatically on `git commit` and includes ruff, mypy, bandit, and several other linters. Fix failures rather than skipping hooks (`--no-verify`). If a commit fails due to a hook, fix the issue and create a new commit — do not amend the previous one.

## Commits and branches

- Conventional commits: `feat:`, `fix:`, `build:`, `docs:`, `refactor:`, `chore:`, etc.
- One logical change per commit.
- Git worktrees go under `.claude/worktrees/<name>/` — not `/tmp` or outside the project root.

## Releases

Releases are triggered via GitHub Actions `workflow_dispatch` — not by pushing a tag:

```bash
gh workflow run release.yml --field bump=<major|minor|patch>
```

The workflow handles versioning, CHANGELOG generation, tagging, building, and publishing to PyPI.

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

## Hardware context

The GivEnergy inverter's local Modbus link is hardware-flaky:

- Transient bad register values are normal (e.g. enum fields with out-of-range integers).
- Timeouts occur even with generous `timeout` and `retries` settings.
- A single failed export is not evidence of a bug — partial data with a warning is the correct outcome.

Be defensive when decoding plant data: catch `ValueError` / `Exception` around register decoding, surface what was captured, and continue rather than crashing.
