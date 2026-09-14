# CI and releases

CI runs on pull requests and branch pushes: Prettier; Python 3.13 dependency checks, Ruff, and pytest; an image build, offline container smoke test, and validation of both Compose files. See [local checks](../README.md#development) and [GHCR deployment](../README.md#deploy-from-ghcr).

## Changelog and publishing

1. Move the changes from `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md) into `## [X.Y.Z] - YYYY-MM-DD` and set `project.version` in `pyproject.toml` to match.
2. Preview the release notes (substitute your version), then merge and wait for CI:

   ```sh
   python3 scripts/changelog.py --tag v0.1.0
   ```

3. Tag the tested commit on the default branch and push:

   ```sh
   git tag -a v0.1.0 -m 'Release 0.1.0'
   git push origin v0.1.0
   ```

The release workflow does not rerun CI. It checks the tag, project version, and matching nonempty changelog entry; publishes a versioned GHCR image for `linux/amd64` and `linux/arm64`; smoke-tests its digest on both architectures; and creates a GitHub Release with notes and image references. Reruns preserve existing release notes.
