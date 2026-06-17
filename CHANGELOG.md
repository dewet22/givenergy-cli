# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project adheres to [Semantic Versioning](https://semver.org/). Sections are
generated at release time by `scripts/release.py generate`, which walks
`git log <last-v-tag>..HEAD` and buckets entries by conventional-commit
prefix. A per-commit `Changelog: <section>` or `Changelog: skip` git
trailer overrides the automatic bucketing.

## [1.6.0] - 2026-06-17

### ✨ Added

- interactive REPL with a reconstructed plant (#70) ([0820097](https://github.com/dewet22/givenergy-cli/commit/082009762986bbd744dd021352754461c4bc3420))

## [1.5.2] - 2026-06-17

### 🐛 Fixed

- keep compact hex lines unwrapped (#69) ([6818f1b](https://github.com/dewet22/givenergy-cli/commit/6818f1bb72d2dde254f9199593d28d3cb7434491))

## [1.5.1] - 2026-06-17

### ✨ Added

- add --compact/--terse hex-dump output (#68) ([1a3b738](https://github.com/dewet22/givenergy-cli/commit/1a3b7385aba3dd5383855e63e3a5206f4f752ee7))

### 🐛 Fixed

- fetch full history in release checkout; backfill changelog (#67) ([5e68891](https://github.com/dewet22/givenergy-cli/commit/5e688912e770c94b5aa2c9d4c971b13aa08b51ab))

## [1.5.0] - 2026-06-10

### ✨ Added

- reconnect across dropped and quiet streams (#60) ([a9e14f5](https://github.com/dewet22/givenergy-cli/commit/a9e14f5684d6294998a3c95ee2faaf3d08c02590))
- Glance/Flow/Analyst views mirroring the HASS dashboards (#61) ([f9ba267](https://github.com/dewet22/givenergy-cli/commit/f9ba267f846691808964900ba8d3f05bccd4d89b))
- cache PlantCapabilities per host for faster startup (#62) ([2ef6361](https://github.com/dewet22/givenergy-cli/commit/2ef636100cbea81bc46dc7b7b4e1ab4b4b8d0466))
- Controls view — write inverter commands behind --allow-writes (#64) ([ae7fc5d](https://github.com/dewet22/givenergy-cli/commit/ae7fc5d5657a9a565265a9d97fa4f9f6cea1f97d))

### 🔧 Maintenance

- de-advertise the coordination inbox surface (#224 H3) (#63) ([85661f7](https://github.com/dewet22/givenergy-cli/commit/85661f7467612eed824d6e931d739595c9d86ce5))
- bump givenergy-modbus lock to 2.2.0rc6 (#65) ([04c9b90](https://github.com/dewet22/givenergy-cli/commit/04c9b9073d450f78a386eed87750939b3ac8deac))
- require givenergy-modbus 2.2.0 final (#66) ([9a79126](https://github.com/dewet22/givenergy-cli/commit/9a791260156fa58db618d9baa0c1178f931d7e83))

## [1.5.0rc1] - 2026-06-10

### ✨ Added

- redact serial numbers by default for share-safe output (#50) ([252806c](https://github.com/dewet22/givenergy-cli/commit/252806c475b12c3a73e7e019e98fc1f174eb598f))

### 🐛 Fixed

- bump givenergy-modbus to 2.1.5, widen typer to <0.27.0 (#49) ([3f227d4](https://github.com/dewet22/givenergy-cli/commit/3f227d4e371520bb7cb5844efe2184b92cbdfef4))
- escape untrusted strings, guard imports, honest capture wording (#54) ([b042611](https://github.com/dewet22/givenergy-cli/commit/b0426117c90d9363a88ff2195dc4083f3be2ae3d))
- create export/capture output files owner-only (0o600) (#56) ([5ca9995](https://github.com/dewet22/givenergy-cli/commit/5ca9995a1b2f06455d81b2c2d307d63e1b0e6b66))
- track givenergy-modbus 2.2.0rc4 (#59) ([ec7bc8f](https://github.com/dewet22/givenergy-cli/commit/ec7bc8f293dc94a1c18eea230850e5df89115034))

### 🔧 Maintenance

- formalise agent coordination inbox protocol ([2d766f8](https://github.com/dewet22/givenergy-cli/commit/2d766f831564349c5d8132cc83cbf964b8be558c))
- move check-inbox.sh to shared ~/.claude-personal/scripts/ ([3606b91](https://github.com/dewet22/givenergy-cli/commit/3606b914b46413be79ba102a0c5e20c0af920d39))
- document GitHub bot-vs-user identity split in AGENTS.md ([d0be185](https://github.com/dewet22/givenergy-cli/commit/d0be185d600b6d1a678a6058ae3c468a2a00b211))
- point bot gh actions at $CLAUDE_CONFIG_DIR/gh-env ([28a94ab](https://github.com/dewet22/givenergy-cli/commit/28a94ab18619e672e0e59e81dd6401e87f67e9b0))
- document prek hooks, testing patterns, and release preview in AGENTS.md ([f4502ae](https://github.com/dewet22/givenergy-cli/commit/f4502ae8cb2a935f26c85e192e16fd601f04cb12))
- add 1.4.0 release notes ([de57da7](https://github.com/dewet22/givenergy-cli/commit/de57da7f84f74f88c00ed59c9efef6faba079dbb))
- publish audit document and add disclosure policy (#52) ([f65ede1](https://github.com/dewet22/givenergy-cli/commit/f65ede1e6156f5700176befc8be99bdf8cde21e9))
- SHA-pin actions, env-indirect version, validate dispatch input (#53) ([b5f5145](https://github.com/dewet22/givenergy-cli/commit/b5f51450cdf3f98ede2aafc62f08cdd8cffbaae6))
- document threat model and fix stale capture wording (#55) ([a060953](https://github.com/dewet22/givenergy-cli/commit/a060953301d74bd67073f17fce9eea6b1be597a3))
- add scheduled + per-PR dependency vulnerability audit (#57) ([d757563](https://github.com/dewet22/givenergy-cli/commit/d75756396b5ff4c8bc3d97beae523e676e5a2269))
- split release into build and publish jobs (#58) ([5b3e301](https://github.com/dewet22/givenergy-cli/commit/5b3e30159cd914013c5e5bd0f43ba45c9e198667))

## [1.4.0] - 2026-06-02

### ✨ Added

- add mock-server command to replay captures as a fake plant ([cafbd50](https://github.com/dewet22/givenergy-cli/commit/cafbd5066506209165dbf66064ab0a34d5062c2e))
- add --log-level to mock-server (shared with tui via GIVENERGY_LOG_LEVEL) ([a2f5107](https://github.com/dewet22/givenergy-cli/commit/a2f510794adf172f8cbfe11f20f39722e87293f5))

### 🐛 Fixed

- harden mock-server per review feedback ([ab658d3](https://github.com/dewet22/givenergy-cli/commit/ab658d3dbb17c5b94b2011e4e3b9950df715f4ee))

### 🔧 Maintenance

- bump givenergy-modbus to >=2.1.0b12 ([a429d04](https://github.com/dewet22/givenergy-cli/commit/a429d0430812c94f7b9c85632ed881639d87ee0a))

## [1.3.0] - 2026-06-01

### ✨ Added

- accept hex addresses for probe --base and --device ([127ec91](https://github.com/dewet22/givenergy-cli/commit/127ec919b808b41539d09ea583d2b00cde5809ba))

### 🐛 Fixed

- handle pre-parsed int in _parse_int; address review feedback ([79d36c7](https://github.com/dewet22/givenergy-cli/commit/79d36c72072b13793b5bc63c709cb106958f1159))

### 🔧 Maintenance

- use 0x10000/0xffff hex literals in probe range check ([a0371ac](https://github.com/dewet22/givenergy-cli/commit/a0371ac4c132a55b95f30a597c5181e38de9ed43))

## [1.2.0] - 2026-06-01

### ✨ Added

- bump to givenergy-modbus 2.1, add AGENTS.md ([d3a2184](https://github.com/dewet22/givenergy-cli/commit/d3a21849370af6e6b50597943195684570729a15))

## [1.1.0] - 2026-06-01

### 🔧 Maintenance

- update README — add capture/probe commands, reorder usage section ([34e58c7](https://github.com/dewet22/givenergy-cli/commit/34e58c72ef9ece9b3224addab2e375922e7e9130))

## [1.0.0] - 2026-05-22

### 🔧 Maintenance

- align README Python requirement with pyproject (3.14+) (#11) ([238b87c](https://github.com/dewet22/givenergy-cli/commit/238b87c2da52ee24acc178088a4467be47934f76))
