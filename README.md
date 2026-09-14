# pl-lti-push

Send PrairieLearn assessment grades to a linked LMS course on a cron schedule through PL's LTI 1.3 instructor form.

Link each assessment to its LMS assignment first. Use a browser session with course-instance editing access; API tokens cannot replace the session cookie. The service does not create LMS assignments and depends on PL's instructor UI remaining compatible.

## Quick start

Requires Docker with Docker Compose. Run commands from the project directory. For a published image, use [GHCR deployment](#deploy-from-ghcr) below and skip the build step.

1. Copy the example and edit `config.json` with your PL origin, target IDs, and schedules. Adjust or remove the example activation dates.

   ```sh
   cp config.example.json config.json
   ```

2. Build and validate the configuration offline:

   ```sh
   docker compose build
   docker compose run --rm scheduler validate
   ```

3. Import your browser session:

   ```sh
   docker compose run --rm scheduler import-cookies
   ```

   Log in to PL, then open browser developer tools → **Application/Storage → Cookies** → the PL site. Copy the value of `pl2_session` and paste it directly at the terminal prompt. Input is hidden.

4. Check access and start the scheduler:

   ```sh
   docker compose run --rm scheduler check-auth
   docker compose up -d
   docker compose logs -f scheduler
   ```

   `check-auth` reads enabled assignment forms without sending grades. **Starting the scheduler can send grades immediately when the current minute matches a schedule.**

## Deploy from GHCR

To use a published image instead of building locally, set these in the project's `.env` file:

```dotenv
COMPOSE_FILE=compose.ghcr.yaml
PL_LTI_PUSH_IMAGE=ghcr.io/<owner>/<repository>:<version>
```

Replace the image placeholder with a version or digest from a GitHub Release, then run `docker compose pull` and follow the quick start, skipping `docker compose build`.

To upgrade, update the image reference in `.env`, then run `docker compose pull` and `docker compose up -d`. Keep the same project directory and state volume.

## Configuration

See [config.example.json](config.example.json) for the complete structure. Unknown fields, duplicate names, and duplicate target triples are rejected.

| Global field              | Default  | Meaning                                                               |
| ------------------------- | -------- | --------------------------------------------------------------------- |
| `base_url`                | Required | PL origin without a path. HTTPS required except for loopback testing. |
| `assignments`             | Required | Non-empty list of assessment targets.                                 |
| `timezone`                | `UTC`    | IANA timezone for cron, e.g. `America/Vancouver`.                     |
| `workers`                 | `4`      | Concurrent runs, 1–32.                                                |
| `request_timeout_seconds` | `30`     | HTTP timeout, 1–300 seconds.                                          |
| `job_timeout_seconds`     | `600`    | Polling time limit per run, 1–86400 seconds.                          |
| `poll_interval_seconds`   | `5`      | Delay between polls, 1–300 seconds.                                   |

| Assignment field         | Meaning                                                                                 |
| ------------------------ | --------------------------------------------------------------------------------------- |
| `name`                   | Unique CLI name: 1–80 ASCII letters, digits, underscores, or hyphens.                   |
| `course_instance_id`     | PL course instance ID.                                                                  |
| `lti_course_instance_id` | LMS connection ID from the PL URL ending in `/instance_admin/lti13_instance/<id>`.      |
| `assessment_id`          | PL assessment ID, not an LMS assignment ID or course JSON UUID.                         |
| `cron`                   | Non-empty list of five-field expressions, e.g. `["0 */6 * * *"]`.                       |
| `enabled`                | Defaults to `true`; `false` disables scheduled runs.                                    |
| `starts_at`, `ends_at`   | Optional ISO 8601 timestamps with an offset or `Z`; omit or use `null` for no boundary. |

IDs must be positive decimal **strings**. Activation includes `starts_at` and excludes `ends_at`; start must precede end. These are absolute instants, independent of the cron timezone.

Scheduling rules:

- Any matching cron entry makes a target due, at most once per local minute. Fields are `minute hour day-of-month month day-of-week`; restricted day-of-month and day-of-week use OR.
- Missed minutes are not replayed. Spring DST gaps are skipped; repeated fall minutes run once. Moving the clock backward or changing the timezone can suppress runs until the saved local minute is exceeded.
- A target cannot overlap itself. Runs are skipped when all workers are busy; there is no catch-up queue.
- Manual `run` ignores `enabled` and cron, but respects activation dates for new submissions. It can resume a pending job outside the window. Ending a window does not cancel an existing PL job.

After configuration changes, run `docker compose up -d --force-recreate`.

## Operations

View state and logs while the scheduler runs:

```sh
docker compose exec scheduler status
docker compose logs --tail 100 scheduler
```

Stop the scheduler before `import-cookies`, `check-auth`, `run`, or `resolve`; these commands share its state lock. For example, to send or resume an assignment:

```sh
docker compose stop scheduler
docker compose run --rm scheduler run lab-1
docker compose up -d
```

To refresh an expired or revoked session, log in again and use `import-cookies` followed by `check-auth` in place of `run lab-1` above.

`run` exits with `0` only for verified job success with no reported delivery errors. JSON run events include assignment names, job paths, statuses, and aggregate counts. Tests use synthetic data; real PL/LMS delivery and session longevity remain unverified.

## State and recovery

The Docker named volume at `/state` stores session credentials and submission history. Keep it across upgrades: `docker compose down -v` deletes both. Losing or restoring old state can allow duplicate submissions. PL provides no idempotency key for this form, so exactly-once delivery is not guaranteed.

- `pending`: a job is known. The next run resumes polling; use manual `run` to resume immediately.
- `submitting`: the POST outcome is uncertain. New submissions are blocked until you inspect PL's job history and resolve the state.

For an uncertain submission, stop the scheduler and attach the confirmed job ID from its `/jobSequence/<id>` URL:

```sh
docker compose stop scheduler
docker compose run --rm scheduler resolve lab-1 --job-id 12345
docker compose run --rm scheduler run lab-1
docker compose up -d
```

If review confirms a new submission is appropriate, use `resolve lab-1 --clear` instead. It permits a future submission and can cause a duplicate if the original POST succeeded. Both recovery options only change local state; they do not cancel PL jobs.

## Development

Open the project with **Dev Containers: Reopen in Container** in VS Code for dependencies and Ruff/Prettier formatting on save. Rerun `sh .devcontainer/post-create.sh` after dependency changes. For local setup, use Python 3.13 and Node.js 24:

```sh
npm ci
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation -e '.[dev]'
python -m pip check
```

Checks:

```sh
npm run format:check
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m pytest -q
```

Format with `npm run format` and `python -m ruff format src tests scripts`. For local CLI use, put paths before the subcommand:

```sh
pl-lti-push --config config.example.json --state ./state validate
```

Record user-visible changes in [CHANGELOG.md](CHANGELOG.md). See [CI and releases](docs/releases.md) for publishing.
