# Changelog

For entry format and release preparation, see [CI and releases](docs/releases.md#changelog-and-publishing).

## [Unreleased]

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
