# CLAUDE.md

## Project Overview
semgrep-scm-util is a CLI tool for managing Semgrep SCM configs across GitHub Enterprise Server (GHES) and GitLab Self-Managed (GLSM) instances. For GHES, it discovers orgs not yet onboarded to Semgrep and can create the necessary SCM configurations.

## Tech Stack
- Python 3.12+
- UV for package management
- argparse for CLI

## Project Structure
```
src/semgrep_ghes_util/
├── __init__.py
├── __main__.py      # Entry point for `python -m semgrep_ghes_util`
├── cli.py           # CLI argument parsing and command handlers
└── clients/
    ├── github_client.py   # GHES API client
    └── semgrep_client.py  # Semgrep API client
```

## Commands
```bash
# Install dependencies
uv sync

# Run CLI
uv run semgrep-scm-util --help

# GHES commands (GitHub Enterprise Server operations)
uv run semgrep-scm-util ghes list-orgs             # List all GHES organizations
uv run semgrep-scm-util ghes list-configs          # List Semgrep SCM configs for GHES
uv run semgrep-scm-util ghes list-missing-configs  # List GHES orgs not in Semgrep
uv run semgrep-scm-util ghes create-missing-configs # Create configs for missing orgs

# GLSM commands (GitLab Self-Managed operations)
uv run semgrep-scm-util glsm list-configs          # List Semgrep SCM configs for GLSM
uv run semgrep-scm-util glsm create-configs        # Create configs for specified groups
```

## Docker
```bash
# Build
docker build -t semgrep-scm-util .

# Run with .env file
docker run --rm --env-file .env semgrep-scm-util ghes list-configs

# Run with individual env vars
docker run --rm \
  -e SEMGREP_APP_TOKEN \
  -e GHES_TOKEN \
  -e GHES_URL \
  semgrep-scm-util ghes list-configs
```

## Environment Variables
- `SEMGREP_APP_TOKEN` (required) - Semgrep API token
- `GHES_TOKEN` (required for GHES commands) - GitHub Enterprise Server token
- `GHES_URL` (optional) - GHES URL, can also be passed via `--ghes-url`
- `GLSM_TOKEN` (required for GLSM create) - GitLab Self-Managed token
- `GLSM_URL` (optional) - GLSM URL, can also be passed via `--glsm-url`
