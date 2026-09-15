# Changelog

For entry format and release preparation, see [CI and releases](docs/releases.md#changelog-and-publishing).

## [Unreleased]

## [0.3.2] - 2026-09-15

- Build and smoke-test ARM64 release images on native ARM runners instead of QEMU emulation.
- Build each architecture separately with isolated caches, then merge image digests into the versioned multi-platform GHCR image.

## [0.3.1] - 2026-09-15

- Use historical Vancouver dates in logging timezone tests to avoid incorrect winter-offset expectations after the 2026 switch to year-round daylight time.

## [0.3.0] - 2026-09-15

- Replace mixed JSON and plain-text operational logs with a uniform, readable format using the configured timezone, severity, assignment, and event details.
- Log resumed polling and include job paths in pending events; distinguish warnings and failures from informational events.

## [0.2.2] - 2026-09-14

- Move the heartbeat check into the Dockerfile with an image-level `HEALTHCHECK`, replacing the CLI subcommand and Compose override.

## [0.2.1] - 2026-09-14

- Allow more time for container smoke-test commands and scheduler startup under ARM64 emulation.

## [0.2.0] - 2026-09-13

- Add a local `healthcheck` command used by Compose and log the next scheduled task while waiting.

## [0.1.1] - 2026-09-13

- Fix multi-architecture release smoke tests with separate matrix jobs.
- Update actions/setup-node to v7.

## [0.1.0] - 2026-09-13

- Initial release.
