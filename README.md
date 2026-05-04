# semgrep-scm-util

CLI tool for syncing GitHub Enterprise Server (GHES) and GitLab Self-Managed (GLSM) organizations to Semgrep SCM configs. Can create, update, and delete SCM configurations.

For GHES, also discovers orgs/groups not yet onboarded to Semgrep and onboards them.

## Installation

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

For production use:

```bash
uv sync --no-dev --frozen
```

For development (includes `pytest` and other dev tooling):

```bash
uv sync
```

## Configuration

Set the following environment variables (or use a `.env` file):

| Variable | Required | Description |
|----------|----------|-------------|
| `SEMGREP_APP_TOKEN` | Yes | Semgrep API token |
| `GHES_TOKEN` | For GHES commands | GitHub Enterprise Server token |
| `GHES_URL` | No | GHES URL (can also use `--ghes-url` on commands) |
| `GLSM_TOKEN` | For GLSM create | GitLab Self-Managed token |
| `GLSM_URL` | No | GLSM URL (can also use `--glsm-url` on commands) |

> **Note:** The `scm` command group has been renamed to `ghes`. The old name still works but prints a deprecation warning.

## GHES commands

### Listing SCM configs

```bash
# List all GHES organizations
uv run semgrep-scm-util ghes list-orgs --ghes-url https://github.example.com

# List all Semgrep GHES SCM configs
uv run semgrep-scm-util ghes list-configs

# List only unhealthy GHES SCM configs
uv run semgrep-scm-util ghes list-configs --unhealthy-only

# List SCM configs for a specific GHES instance
uv run semgrep-scm-util ghes list-configs --ghes-url https://github.example.com

# List GHES orgs not yet onboarded to Semgrep
uv run semgrep-scm-util ghes list-missing-configs --ghes-url https://github.example.com
```

Example `list-configs` output:

```
Found 1 SCM config(s):

  [✓] my-org
      Type: SCM_TYPE_GITHUB_ENTERPRISE
      URL: https://github.example.com
      ID: 23456
      SCM ID: 138447
```

The `SCM ID` can be passed to `--scm-id` on `create-config` or `create-missing-configs` to reuse an existing config's token.

**list-configs flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--ghes-url` | all | Filter to configs for this GHES instance |
| `--unhealthy-only` | false | Only show unhealthy SCM configs |
| `--required-scopes` | - | Comma-separated scopes to require (see below) |

#### Health checks and token scopes

By default, health checks only verify **connection status** (whether Semgrep can reach the SCM). Use `--required-scopes` to also require specific token permissions:

```bash
# Basic health check (connection only)
uv run semgrep-scm-util ghes list-configs --unhealthy-only

# Require read access for scanning
uv run semgrep-scm-util ghes list-configs --unhealthy-only \
  --required-scopes read_metadata,read_contents

# Require full managed scanning capabilities
uv run semgrep-scm-util ghes list-configs --unhealthy-only \
  --required-scopes read_metadata,read_contents,read_pull_request,write_pull_request_comment,manage_webhooks
```

**Available scopes:**

| Scope | Description |
|-------|-------------|
| `read_metadata` | Read repo metadata |
| `read_contents` | Read file contents |
| `read_pull_request` | Read PR information |
| `write_pull_request_comment` | Post PR comments (for findings) |
| `read_members` | Read org membership |
| `manage_webhooks` | Create/manage webhooks |
| `write_contents` | Write file contents (optional, for autofix) |

### Creating SCM configs

SCM configs connect Semgrep to your GitHub organizations. There are two common use cases:

1. **Connection only** (default) - Establishes a connection to the GitHub org without enabling scanning. Useful when you want to set up the connection first and enable scanning later via the Semgrep UI or `update-configs`.

2. **Managed scanning** - Creates the connection AND enables Semgrep to automatically scan repos. This requires webhooks (`--subscribe`) and typically full scans (`--auto-scan`). Optionally enable PR/MR diff scanning (`--diff-enabled`).

#### Connection-only configs (default)

Create configs that only establish the connection, without enabling webhooks or scanning:

```bash
# Create for a single org
uv run semgrep-scm-util ghes create-config --ghes-url https://github.example.com --ghes-org my-org

# Create for all missing orgs
uv run semgrep-scm-util ghes create-missing-configs --ghes-url https://github.example.com
```

#### Managed scanning configs

Create configs with webhooks and scanning enabled for full Semgrep managed scanning:

```bash
# Create for a single org with managed scanning
uv run semgrep-scm-util ghes create-config --ghes-url https://github.example.com --ghes-org my-org \
  --subscribe --auto-scan --diff-enabled

# Create for all missing orgs with managed scanning
uv run semgrep-scm-util ghes create-missing-configs --ghes-url https://github.example.com \
  --subscribe --auto-scan --diff-enabled
```

| Flag | What it does |
|------|--------------|
| `--subscribe` | Creates webhooks so Semgrep receives events from GitHub |
| `--auto-scan` | Enables automatic full scans on push to default branch |
| `--diff-enabled` | Enables diff scans on pull requests |

#### Recommended workflow for multiple orgs

When onboarding many orgs, create one config first to verify the token works, then use its SCM ID to reuse the token for remaining orgs:

**Step 1: Create and verify a single config**

```bash
uv run semgrep-scm-util ghes create-config --ghes-url https://github.example.com --ghes-org my-first-org \
  --subscribe --auto-scan --diff-enabled
```

This outputs the SCM ID and health status:

```
Created SCM config for my-first-org
  SCM ID: 138447

Checking SCM health...
  ✓ Connected
  Token scopes: read_metadata, read_pull_request, write_pull_request_comment, read_contents, read_members, manage_webhooks, write_contents

Use --scm-id 138447 to reuse this token with create-config or create-missing-configs.
```

> **Tip:** If you've already created a config in a previous run, you can look up its SCM ID with `ghes list-configs` — the value is printed alongside each config.

**Step 2: Preview remaining orgs**

```bash
uv run semgrep-scm-util ghes create-missing-configs --ghes-url https://github.example.com --dry-run
```

**Step 3: Create configs for remaining orgs (reusing the token)**

```bash
uv run semgrep-scm-util ghes create-missing-configs --ghes-url https://github.example.com \
  --scm-id 138447 --subscribe --auto-scan --diff-enabled
```

You can also use `--scm-id` with `create-config` when adding individual orgs later:

```bash
uv run semgrep-scm-util ghes create-config --ghes-url https://github.example.com \
  --ghes-org another-org --scm-id 138447 --subscribe --auto-scan --diff-enabled
```

#### Creating configs for specific orgs

```bash
# By name
uv run semgrep-scm-util ghes create-missing-configs --ghes-url https://github.example.com \
  --orgs org1 org2 org3

# From file (one org per line)
uv run semgrep-scm-util ghes create-missing-configs --ghes-url https://github.example.com \
  --orgs-file orgs.txt
```

#### All create config flags

| Flag | Default | Description |
|------|---------|-------------|
| `--ghes-token` | `$GHES_TOKEN` | GitHub Enterprise Server token |
| `--subscribe` | disabled | Subscribe to webhooks |
| `--auto-scan` | disabled | Enable auto-scanning |
| `--diff-enabled` | disabled | Enable diff scanning |
| `--scm-id` | - | Reuse token from an existing SCM config (for creating configs only) |
| `--orgs` | all missing | Specific orgs to create (create-missing-configs only) |
| `--orgs-file` | - | File with org names, one per line (create-missing-configs only) |
| `--delay` | 1.0 | Seconds between creating each config (create-missing-configs only) |
| `--dry-run` | false | Preview without making changes |

### Updating SCM configs

Bulk update SCM configs matching a GHES URL. Only the properties you specify will be updated.

**Examples:**

```bash
# Preview what would be updated (dry-run)
uv run semgrep-scm-util ghes update-configs --ghes-url https://github.example.com --subscribe true --dry-run

# Update all configs for the GHES instance
uv run semgrep-scm-util ghes update-configs --ghes-url https://github.example.com --subscribe true

# Update specific orgs only
uv run semgrep-scm-util ghes update-configs --ghes-url https://github.example.com --orgs org1 org2 --auto-scan true

# Update multiple properties at once
uv run semgrep-scm-util ghes update-configs --ghes-url https://github.example.com \
  --subscribe true --auto-scan false --diff-enabled true

# Rotate the access token on all configs for this GHES instance
uv run semgrep-scm-util ghes update-configs --ghes-url https://github.example.com \
  --ghes-token <new-token>
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--orgs` | all | Specific org names to update (mutually exclusive with --orgs-file) |
| `--orgs-file` | - | File containing org names to update, one per line (mutually exclusive with --orgs) |
| `--subscribe` | - | Subscribe to webhooks (true/false) |
| `--auto-scan` | - | Enable auto-scanning (true/false) |
| `--use-network-broker` | - | Use network broker (true/false) |
| `--diff-enabled` | - | Enable diff scanning (true/false) |
| `--ghes-token` | - | New access token to set on matching configs |
| `--dry-run` | false | Preview without making changes |
| `--delay` | 1.0 | Seconds between updates |

### Checking SCM config health

Check the health status of SCM configs, including connection status and token scopes.

```bash
# Check all configs for a GHES instance (connection only)
uv run semgrep-scm-util ghes check-configs --ghes-url https://github.example.com

# Check specific orgs only
uv run semgrep-scm-util ghes check-configs --ghes-url https://github.example.com --orgs org1 org2

# Check with specific scope requirements
uv run semgrep-scm-util ghes check-configs --ghes-url https://github.example.com \
  --required-scopes read_metadata,read_contents
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--orgs` | all | Specific org names to check |
| `--required-scopes` | - | Comma-separated scopes to require for health |
| `--delay` | 0.25 | Seconds between checks |

### Deleting SCM configs

Delete SCM configs for specific orgs. The `--orgs` flag is required to prevent accidental deletion.

```bash
# Preview what would be deleted (dry-run)
uv run semgrep-scm-util ghes delete-configs --ghes-url https://github.example.com --orgs org1 org2 --dry-run

# Delete specific orgs
uv run semgrep-scm-util ghes delete-configs --ghes-url https://github.example.com --orgs org1 org2

# Only delete configs that are unhealthy (skip healthy ones)
uv run semgrep-scm-util ghes delete-configs --ghes-url https://github.example.com \
  --orgs org1 org2 --unhealthy-only
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--orgs` | required | Org names to delete |
| `--unhealthy-only` | false | Only delete unhealthy configs; skip healthy ones |
| `--dry-run` | false | Preview without deleting |
| `--delay` | 0.5 | Seconds between requests |

### Onboarding repos to managed scans

Bulk onboard uninitialized repos to Semgrep managed scans. This command:
- Fetches repos that haven't been scanned yet
- Filters out archived repos automatically
- Optionally filters to only repos with healthy SCM configs

**Examples:**

```bash
# Preview what would be onboarded (dry-run)
uv run semgrep-scm-util ghes onboard-repos --dry-run

# Onboard all uninitialized repos
uv run semgrep-scm-util ghes onboard-repos

# Onboard repos for a specific GHES instance only
uv run semgrep-scm-util ghes onboard-repos --ghes-url https://github.example.com

# Onboard without checking SCM health
uv run semgrep-scm-util ghes onboard-repos --check-scm false

# Customize batch size
uv run semgrep-scm-util ghes onboard-repos --batch-size 100
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--ghes-url` | all | Filter to repos from this GHES instance |
| `--dry-run` | false | Preview without making changes |
| `--full-scan` | true | Enable full scanning (true/false) |
| `--diff-scan` | true | Enable diff scanning (true/false) |
| `--batch-size` | 250 | Repos per batch |
| `--check-scm` | true | Only onboard repos with healthy SCM configs (true/false) |
| `--required-scopes` | - | Comma-separated scopes to require when --check-scm is true |
| `--delay` | 1.0 | Seconds between batches |

### Triggering scans

Trigger scans for repos that haven't had a full scan yet. This command:
- Fetches initialized repos (already onboarded)
- Filters out archived repos automatically
- Checks each repo for existing full scans (can be skipped)
- Triggers scans in batches with configurable delays

**Examples:**

```bash
# Preview what would be triggered (dry-run)
uv run semgrep-scm-util ghes trigger-scans --dry-run

# Trigger scans, checking for existing scans first
uv run semgrep-scm-util ghes trigger-scans

# Skip the existing scan check (faster for large repos)
uv run semgrep-scm-util ghes trigger-scans --skip-scan-check

# Trigger for a specific GHES instance
uv run semgrep-scm-util ghes trigger-scans --ghes-url https://github.example.com

# Customize batch size and delays (for reducing system load)
uv run semgrep-scm-util ghes trigger-scans --batch-size 10 --delay 5
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--ghes-url` | all | Filter to repos from this GHES instance |
| `--dry-run` | false | Preview without triggering |
| `--batch-size` | 10 | Scans to trigger per batch |
| `--check-scm` | true | Only scan repos with healthy SCM configs (true/false) |
| `--required-scopes` | - | Comma-separated scopes to require when --check-scm is true |
| `--delay` | 1.0 | Seconds between trigger batches |
| `--check-delay` | 0.1 | Seconds between checking each repo for existing scans |
| `--skip-scan-check` | false | Skip checking for existing scans, trigger for all repos |

## GLSM commands

> **Note:** GLSM commands do not support automatic discovery of groups — group names must always be specified explicitly. There is no `create-missing-configs` equivalent for GitLab Self-Managed.

### Creating SCM configs

Create Semgrep SCM configs for GitLab Self-Managed groups. Groups can be specified directly or read from a file.

```bash
# Create configs for specific groups
uv run semgrep-scm-util glsm create-configs \
  --glsm-url https://gitlab.example.com \
  --glsm-token <token> \
  --groups my-group another-group

# Create configs from a file (one group per line, # comments supported)
uv run semgrep-scm-util glsm create-configs \
  --glsm-url https://gitlab.example.com \
  --groups-file groups.txt

# Create with managed scanning enabled
uv run semgrep-scm-util glsm create-configs \
  --glsm-url https://gitlab.example.com \
  --groups my-group \
  --subscribe --auto-scan --diff-enabled

# Preview without making changes
uv run semgrep-scm-util glsm create-configs \
  --glsm-url https://gitlab.example.com \
  --groups my-group \
  --dry-run
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--glsm-url` | `$GLSM_URL` | GitLab Self-Managed URL |
| `--glsm-token` | `$GLSM_TOKEN` | GitLab personal access token |
| `--groups` | - | Group names to create configs for (mutually exclusive with --groups-file) |
| `--groups-file` | - | File with group names, one per line (mutually exclusive with --groups) |
| `--subscribe` | disabled | Subscribe to webhooks |
| `--auto-scan` | disabled | Enable auto-scanning |
| `--diff-enabled` | disabled | Enable diff scanning |
| `--dry-run` | false | Preview without making changes |
| `--delay` | 1.0 | Seconds between creating each config |

### Updating SCM configs

Bulk update SCM configs for a GitLab Self-Managed instance. Only the properties you specify will be updated.

```bash
# Update all configs for the GLSM instance
uv run semgrep-scm-util glsm update-configs \
  --glsm-url https://gitlab.example.com \
  --subscribe true

# Update specific groups only
uv run semgrep-scm-util glsm update-configs \
  --glsm-url https://gitlab.example.com \
  --groups my-group another-group \
  --auto-scan true

# Rotate the access token on all configs for this GLSM instance
uv run semgrep-scm-util glsm update-configs \
  --glsm-url https://gitlab.example.com \
  --glsm-token <new-token>

# Preview without making changes
uv run semgrep-scm-util glsm update-configs \
  --glsm-url https://gitlab.example.com \
  --subscribe true --dry-run
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--glsm-url` | `$GLSM_URL` | GitLab Self-Managed URL |
| `--groups` | all | Specific group names to update |
| `--groups-file` | - | File with group names to update, one per line |
| `--glsm-token` | - | New access token to set on matching configs |
| `--subscribe` | - | Subscribe to webhooks (true/false) |
| `--auto-scan` | - | Enable auto-scanning (true/false) |
| `--use-network-broker` | - | Use network broker (true/false) |
| `--diff-enabled` | - | Enable diff scanning (true/false) |
| `--dry-run` | false | Preview without making changes |
| `--delay` | 1.0 | Seconds between updates |

### Deleting SCM configs

Delete SCM configs for specific groups. The `--groups` flag is required to prevent accidental deletion.

```bash
# Preview what would be deleted (dry-run)
uv run semgrep-scm-util glsm delete-configs \
  --glsm-url https://gitlab.example.com \
  --groups my-group another-group \
  --dry-run

# Delete specific groups
uv run semgrep-scm-util glsm delete-configs \
  --glsm-url https://gitlab.example.com \
  --groups my-group another-group

# Only delete configs that are unhealthy (skip healthy ones)
uv run semgrep-scm-util glsm delete-configs \
  --glsm-url https://gitlab.example.com \
  --groups my-group another-group \
  --unhealthy-only
```

**Available flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--glsm-url` | `$GLSM_URL` | GitLab Self-Managed URL |
| `--groups` | required | Group names to delete |
| `--unhealthy-only` | false | Only delete unhealthy configs; skip healthy ones |
| `--dry-run` | false | Preview without deleting |
| `--delay` | 0.5 | Seconds between requests |

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
