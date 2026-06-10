# Security Audit — givenergy-cli

- **Date:** 2026-06-10
- **Commit:** `252806c` (branch `claude/pedantic-haslett-b0093b`, clean tree)
- **Scope:** All first-party code (`givenergy_cli/`, `scripts/`, `tests/`), CI workflows (`.github/`), hook/lint configuration (`prek.toml`, `.bandit.yaml`), dependency management (`pyproject.toml`, `uv.lock`, `dependabot.yml`). The `givenergy-modbus` library was inspected only where this CLI relies on its security properties (redaction, frame parsing); a full audit of that library is out of scope.
- **Method:** Manual line-by-line review of every first-party module (~2,900 LOC), pattern sweep for dangerous constructs (`eval`/`exec`/`pickle`/`shell=True`/`yaml.load`/hardcoded secrets — none found), CI workflow injection analysis, and verification of upstream redaction claims against the installed `givenergy-modbus` source.

## Executive summary

This is a small, read-only LAN client for GivEnergy inverters with a deliberately conservative design. **No critical or high-severity issues were found.** The codebase avoids the classic Python footguns entirely (no `eval`/`exec`, no pickle, no shell-string subprocess, no secrets in the repo), the CLI never issues Modbus *write* commands, redaction of identifiers is on by default, and the automation workflows follow the env-var indirection pattern that prevents most GitHub Actions injection bugs.

The findings below are hardening opportunities, ordered by priority. The two that matter most are both supply-chain related: **(M-1)** GitHub Actions are pinned by mutable tag in a workflow that holds PyPI trusted-publishing credentials, and **(M-2)** the dominant attack surface — parsing untrusted network bytes — lives entirely in the `givenergy-modbus` dependency, with no automated vulnerability scanning in place.

| ID | Severity | Finding |
|----|----------|---------|
| M-1 | Medium | Actions pinned by tag, not SHA, in the PyPI-publishing workflow |
| M-2 | Medium | No automated dependency vulnerability scanning; network-parsing trust concentrated in one library |
| L-1 | Low | `capture` claims output is "safe to attach" but undecodable frames pass through unredacted |
| L-2 | Low | Rich markup injection from device-controlled strings (display spoofing) |
| L-3 | Low | Release workflow interpolates a step output directly into shell (`${{ }}` in `run:`) |
| L-4 | Low | `bump-givenergy-modbus` accepts an unvalidated version string from `repository_dispatch` |
| L-5 | Low | Single release job combines `contents: write`, `id-token: write`, and arbitrary repo-code execution |
| L-6 | Low | `inspect` / `mock-server` parse untrusted files with no size or schema guard |
| I-1 | Info | Modbus-TCP has no authentication or encryption (protocol-inherent) |
| I-2 | Info | Export/capture files written with default umask permissions |
| I-3 | Info | No `SECURITY.md` / vulnerability disclosure policy |

---

## Threat model

**What this tool is:** a CLI/TUI that connects over the local network to a GivEnergy inverter's data adapter (Modbus-TCP on port 8899), reads registers, and renders/exports them. It also replays captures as a mock server and generates releases via GitHub Actions.

**Relevant adversaries and assets:**

1. **A malicious or compromised device endpoint** (or an attacker on the LAN path) feeding crafted Modbus responses to the CLI. Assets at risk: the user's terminal/host. Frame parsing happens in `givenergy-modbus`; this repo consumes decoded values and renders them (see L-2).
2. **A malicious shared file** — an export JSON passed to `inspect`, or a capture log passed to `mock-server`. These commands are explicitly designed around users sharing files on GitHub issues, so hostile input is a realistic scenario (see L-6).
3. **Privacy leakage in shared artifacts** — serials, IPs, and network config embedded in exports/captures that users are encouraged to attach to public bug reports (see L-1).
4. **Supply chain** — compromise of a dependency or a GitHub Action tag leading to a poisoned PyPI release (see M-1, M-2, L-5).
5. **The inverter itself** — *not* meaningfully at risk from this tool today: the CLI only ever constructs `ReadHoldingRegistersRequest` / `ReadInputRegistersRequest` ([registers.py:408-417](givenergy_cli/registers.py#L408)), and the only write-capable code path (SOC calibration) is commented out ([app.py:1149-1152](givenergy_cli/app.py#L1149)). Preserving this read-only posture is a key recommendation.

Out of scope / accepted: anyone on the LAN can already talk Modbus to the inverter directly with or without this tool (I-1); local attackers with the user's privileges; physical access.

---

## Findings

### M-1 — GitHub Actions pinned by mutable tag in the publishing workflow

**Where:** [release.yml](.github/workflows/release.yml) (`actions/checkout@v6`, `astral-sh/setup-uv@v7`, `softprops/action-gh-release@v3`, `pypa/gh-action-pypi-publish@release/v1`), [bump-givenergy-modbus.yml](.github/workflows/bump-givenergy-modbus.yml) (same pattern).

**Issue:** All actions are referenced by tag or branch, which the action owner (or anyone who compromises the owner's account) can move at any time. The release job is the worst place for this: it carries `id-token: write` for PyPI trusted publishing and `contents: write` for tagging. A retargeted `softprops/action-gh-release@v3` or `release/v1` tag executes inside a job that can mint a PyPI OIDC token and push to the repo — i.e., a single third-party tag move away from a poisoned `givenergy-cli` release on PyPI.

**Recommendation:** Pin every action to a full commit SHA with a tag comment, e.g. `uses: softprops/action-gh-release@<sha> # v3`. Dependabot's `github-actions` ecosystem (already configured in [dependabot.yml](.github/dependabot.yml)) updates SHA pins automatically, so the maintenance cost is near zero. Prioritise `release.yml`.

### M-2 — No automated dependency vulnerability scanning; parsing trust concentrated upstream

**Where:** [prek.toml](prek.toml), `.github/workflows/`, [pyproject.toml](pyproject.toml).

**Issue:** The only security tooling is Bandit running as a prek hook over first-party code (with an empty skip list — good). Nothing audits the *dependency tree* for known vulnerabilities, and the dependency tree is where the real attack surface is: `givenergy-modbus` parses raw bytes from an unauthenticated network peer, and `textual`/`rich` render device-derived strings. The lockfile is fully hash-pinned (good), but a hash-pinned vulnerable version stays vulnerable. Note also the dependency floor is currently a prerelease (`givenergy-modbus>=2.2.0rc1`).

**Recommendation:**
- Add `pip-audit` (or `uv` audit tooling / GitHub's Dependency Review) as a scheduled or per-PR check.
- Since `givenergy-modbus` is the byte-level parser for hostile input, consider fuzzing its frame decoder in that repo (e.g. `pythonfuzz`/Atheris over `FrameDecoder`) — that's where a memory-of-logic bug from a malicious device would land.
- Move the floor off the `rc` once 2.2.0 finals.

### L-1 — `capture` overstates redaction guarantees for undecodable frames

**Where:** [capture.py:38-43](givenergy_cli/capture.py#L38); upstream `Client.capture_frames` docstring.

**Issue:** The command prints *"Capturing redacted frames"* and *"safe to attach to a GitHub issue"* unconditionally. The upstream redactor is genuinely frame-aware (envelope serials, `C.serial`-tagged registers, LAN-config IPs are zeroed and the CRC recomputed — verified in the installed 2.2.0rc source), **but** its documented behaviour for frames it cannot decode is to emit them *intact*, with only a log message. A capture taken against a device speaking an unknown function code or emitting malformed frames can therefore contain unredacted serials or LAN config, while the CLI still tells the user it's safe to publish.

**Impact:** Privacy leak (hardware serials, internal IP/netmask/gateway) in files users are explicitly told to post publicly. Likelihood is low (requires undecodable frames), but the failure mode is silent at the CLI layer.

**Recommendation:** Have the sink/CLI count passthrough frames (upstream would need to expose this — e.g. a flag on the sink callback or a counter on the client) and replace the "safe to attach" line with a warning when any frame passed through unredacted. Until that signal exists upstream, soften the wording: "serials redacted where frames were decodable".

### L-2 — Rich markup injection from device-controlled strings

**Where:**
- [registers.py](givenergy_cli/registers.py): `show_plant` prints serials and decoded model fields inside markup strings ([registers.py:288-289](givenergy_cli/registers.py#L288)); probe/error paths interpolate `{exc}` into `[red]…[/red]` strings ([registers.py:434-437](givenergy_cli/registers.py#L434)).
- [app.py:936-941](givenergy_cli/app.py#L936): `ModbusLogHandler.emit` writes `record.getMessage()` into a `RichLog(markup=True)` widget — log records routinely embed network-derived bytes (exception reprs, frame dumps).

**Issue:** Strings that originate from the device (serial registers decode arbitrary bytes to text), from a shared export JSON, or from exception messages wrapping network data are rendered with Rich markup enabled. An attacker controlling any of these can inject `[bold]`, `[reverse]`, or — most interestingly — `[link=https://…]` tags, producing clickable hyperlinks or spoofed UI text in the victim's terminal. Rich strips control characters, so classic ANSI escape injection is not in play; this is limited to display spoofing/phishing-adjacent output.

**Impact:** Low — no code execution, but "inspect this export I attached to my bug report" is a realistic social vector for this project, and spoofed output in a diagnostic tool undermines exactly what it's for.

**Recommendation:** Escape untrusted values with `rich.markup.escape()` before interpolation everywhere a device/file/exception-derived string meets a markup-enabled sink (`_model_table` values, serial prints, `{exc}` interpolations, the log handler). One small helper used consistently covers it.

### L-3 — Inline `${{ }}` interpolation in release workflow shell steps

**Where:** [release.yml:96-105](.github/workflows/release.yml#L96) ("Commit and tag" step).

**Issue:** `${{ steps.version.outputs.version }}` is substituted textually into the `run:` script. Today this value is effectively constrained: in the path where this step runs, it's the output of `scripts/release.py bump`, whose input is regex-validated ([release.py:238](scripts/release.py#L238)) and whose output is computed. So this is not currently exploitable. But the pattern is one refactor away from being so (e.g. if the republish path — where the version derives from the free-text `republish_tag` input — ever stops skipping this step). The rest of both workflows correctly use the `env:` indirection pattern, which makes this step the inconsistency.

**Recommendation:** Pass the version via `env:` here too, as every other step already does. Two-line change, eliminates the class.

### L-4 — Unvalidated version string accepted from `repository_dispatch`

**Where:** [bump-givenergy-modbus.yml:21-45](.github/workflows/bump-givenergy-modbus.yml#L21).

**Issue:** `client_payload.version` arrives from anyone holding a token with write access to this repo and is not format-validated in the "Determine" step. Downstream handling is in fact robust — every use goes through quoted env vars, `packaging.Version(new)` raises on garbage before anything touches `pyproject.toml`, and git rejects malicious branch names — so there is no injection today. But the safety is *emergent* from three separate downstream behaviours rather than asserted once.

**Recommendation:** Validate early in the `ver` step: `[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]] || exit 1`. Defense in depth, and it makes the trust assumption explicit.

### L-5 — Release job combines tagging rights, PyPI OIDC, and repo-code execution

**Where:** [release.yml:35-39](.github/workflows/release.yml#L35).

**Issue:** One job holds `contents: write` + `id-token: write` while executing repo code (`scripts/release.py`, the hatchling build backend via `uv build`). Anyone or anything that can modify repo code (a compromised dev machine, a malicious PR merged in haste, a compromised prek hook autofix) gets PyPI publishing transitively. For a solo-maintainer repo this is a reasonable trade-off, but it's worth knowing the boundary.

**Recommendation (optional hardening):** Split into a build job (no special permissions, uploads `dist/*` as an artifact) and a publish job (`id-token: write` only, downloads the artifact, runs only the pinned publish action). Add a GitHub *environment* with required reviewers on the publish job if the repo ever gains collaborators.

### L-6 — `inspect` and `mock-server` parse untrusted files without guards

**Where:** [registers.py:160-173](givenergy_cli/registers.py#L160) (`load_plant`), [mock.py](givenergy_cli/mock.py).

**Issue:** These commands exist precisely to consume files other people produced (bug-report attachments). `load_plant` does `json.loads(path.read_text())` then indexes `data["register_caches"]` / `data["inverter_serial_number"]` directly: a multi-GB file is read fully into memory, and a structurally-wrong file produces a raw `KeyError`/`ValueError` traceback rather than a clean error. JSON parsing itself is safe (no pickle/eval anywhere — verified), so this is robustness, memory exhaustion, and the L-2 markup path rather than code execution.

**Recommendation:** Cap input size (an honest export is tens of KB; reject > a few MB with a clear message), wrap deserialisation in a `try/except` that prints a "not a valid export file" error, and treat all string fields from the file as untrusted for rendering (L-2).

### I-1 — Modbus-TCP is unauthenticated and unencrypted (protocol-inherent)

The GivEnergy local interface has no authentication, authorization, or transport encryption; anyone who can reach port 8899 can read registers and (with other tools) write them. This CLI cannot fix that, and it correctly doesn't pretend to. Worth stating in the README's bug-report section so users understand that network segmentation (VLAN/firewall around the inverter) is *their* control, and that this tool's read-only design is a deliberate safety property — any future write-capable feature (e.g. re-enabling SOC calibration, charge-slot editing) should ship behind an explicit confirmation and ideally a `--allow-writes` style flag.

### I-2 — Exported files created with default permissions

`export`/`capture` write via `Path.write_text` / `open("w")` ([registers.py:144](givenergy_cli/registers.py#L144), [capture.py:20](givenergy_cli/capture.py#L20)) — i.e., default umask, typically world-readable. With `--no-redact` these contain hardware serials. Negligible on single-user machines; if you care, `output.chmod(0o600)` after writing (`touch(mode=…)` only applies the mode on creation). Note also `export` silently overwrites an existing file.

### I-3 — No vulnerability disclosure policy

There is no `SECURITY.md`. For a published PyPI package that parses network input, add one (even two lines: report privately to the maintainer email, expected response window) so a researcher who finds a frame-parsing bug in the stack has a route that isn't a public issue.

---

## What's already good (keep it this way)

- **Read-only by construction** — no Modbus write request is reachable from any command; the one write feature is commented out rather than hidden behind a flag.
- **No dangerous Python constructs** — zero `eval`/`exec`/`pickle`/`shell=True`/`os.system` in first-party code; the only `subprocess` use ([release.py:177-189](scripts/release.py#L177)) is literal arg lists, no shell.
- **Redaction on by default** — `export --redact` is the default with an explicit `--no-redact` opt-out, and redaction is type-driven (registers tagged `C.serial`) rather than pattern-guessing.
- **Mock server binds loopback by default**; LAN exposure (`0.0.0.0`) is an explicit, documented opt-in serving only replayed mock data.
- **CI input handling is mostly exemplary** — env-var indirection nearly everywhere, allowlisted base branches via `case`, upper-bound validation through `packaging.Version` before touching `pyproject.toml`.
- **Locked, hash-pinned dependencies** (`uv.lock`) with Dependabot on both pip and github-actions ecosystems; Bandit in the hook chain with an empty skip list; `python-no-eval` and blanket-noqa/type-ignore guards in prek.
- **Input validation at the CLI boundary** — register/device/port ranges are bounds-checked with tests covering hex parsing, range rejection, and clean error output.

## Prioritised action plan

1. **Now (release-pipeline integrity):** SHA-pin all actions in `release.yml` and `bump-givenergy-modbus.yml` (M-1); move the one inline `${{ }}` to `env:` (L-3); add the dispatch version regex (L-4). All three are mechanical.
2. **Soon (user privacy & robustness):** soften/instrument the `capture` "safe to attach" claim (L-1); add `rich.markup.escape()` at untrusted-string render sites (L-2); size-cap and error-wrap `load_plant` (L-6); add `SECURITY.md` (I-3).
3. **When convenient (depth):** add `pip-audit` to CI (M-2); consider the build/publish job split (L-5); document the LAN threat model in the README (I-1); fuzz the frame decoder upstream in `givenergy-modbus` (M-2).
