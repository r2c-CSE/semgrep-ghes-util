import argparse
import os
import sys
import time
from urllib.parse import urlparse

from dotenv import load_dotenv

from semgrep_ghes_util.clients.github_client import GithubClient
from semgrep_ghes_util.clients.semgrep_client import (
    Project,
    ProjectStatus,
    Repo,
    ScanType,
    ScmConfig,
    ScmTokenScopes,
    ScmType,
    SemgrepClient,
)


def parse_bool(value: str) -> bool:
    """Parse a boolean string value."""
    if value.lower() in ("true", "1", "yes"):
        return True
    elif value.lower() in ("false", "0", "no"):
        return False
    else:
        raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}. Use 'true' or 'false'.")


def parse_scopes(value: str) -> list[str]:
    """Parse and validate a comma-separated list of scope names."""
    scopes = [s.strip() for s in value.split(",") if s.strip()]
    invalid = [s for s in scopes if s not in ScmTokenScopes.ALL_SCOPES]
    if invalid:
        valid_list = ", ".join(ScmTokenScopes.ALL_SCOPES)
        raise argparse.ArgumentTypeError(
            f"Invalid scope(s): {', '.join(invalid)}. Valid scopes: {valid_list}"
        )
    return scopes


def get_env_or_exit(var_name: str) -> str:
    """Get an environment variable or exit with an error."""
    value = os.environ.get(var_name)
    if not value:
        print(f"Error: {var_name} environment variable is required", file=sys.stderr)
        sys.exit(1)
    return value


# SCM commands
def cmd_scm_list_configs(args: argparse.Namespace) -> None:
    """List all Semgrep SCM configs."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")
    client = SemgrepClient(semgrep_token)

    print(f"Deployment: {client.deployment.name} ({client.deployment.slug})\n")

    configs = client.list_scm_configs()

    # Filter to GHES configs only
    configs = [c for c in configs if c.type == ScmType.GITHUB_ENTERPRISE.value]

    # Filter by GHES URL if provided
    if args.ghes_url:
        normalized_ghes_url = args.ghes_url.rstrip("/").lower()
        configs = [
            c for c in configs
            if c.base_url and c.base_url.rstrip("/").lower() == normalized_ghes_url
        ]

    # Get required scopes (if any)
    required_scopes = getattr(args, "required_scopes", None)

    # Filter to unhealthy only if requested
    if args.unhealthy_only:
        configs = [c for c in configs if not c.meets_requirements(required_scopes)]

    if not configs:
        if args.unhealthy_only:
            print("No unhealthy SCM configs found.")
        else:
            print("No SCM configs found.")
        return

    label = "unhealthy " if args.unhealthy_only else ""
    print(f"Found {len(configs)} {label}SCM config(s):\n")
    for config in configs:
        meets_reqs = config.meets_requirements(required_scopes)
        status = "✓" if meets_reqs else "✗"
        print(f"  [{status}] {config.namespace}")
        print(f"      Type: {config.type}")
        if config.base_url:
            print(f"      URL: {config.base_url}")
        print(f"      ID: {config.id}")
        if config.scm_id is not None:
            print(f"      SCM ID: {config.scm_id}")
        if not meets_reqs:
            if config.status and config.status.error:
                print(f"      Error: {config.status.error}")
            elif not config.is_healthy:
                print(f"      Error: Connection unhealthy")
            if required_scopes and config.token_scopes:
                missing = config.token_scopes.missing_scopes(required_scopes)
                if missing:
                    print(f"      Missing scopes: {', '.join(missing)}")
        print()


def get_missing_orgs(
    ghes_url: str,
    ghes_token: str,
    semgrep_token: str,
) -> tuple[list, list]:
    """Get GHES orgs that don't have Semgrep SCM configs.

    Returns:
        Tuple of (missing_orgs, existing_configs_for_ghes)
    """
    # Normalize GHES URL for comparison
    normalized_ghes_url = ghes_url.rstrip("/").lower()

    github_client = GithubClient(ghes_url, ghes_token)
    semgrep_client = SemgrepClient(semgrep_token)

    # Get all GHES orgs
    ghes_orgs = github_client.list_organizations()
    ghes_org_names = {org.login.lower() for org in ghes_orgs}

    # Get all Semgrep SCM configs and filter to ones matching this GHES instance
    all_configs = semgrep_client.list_scm_configs()
    ghes_configs = [
        config for config in all_configs
        if config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
    ]

    # Find orgs that already have configs
    configured_orgs = {config.namespace.lower() for config in ghes_configs}

    # Find missing orgs
    missing_org_names = ghes_org_names - configured_orgs
    missing_orgs = [org for org in ghes_orgs if org.login.lower() in missing_org_names]

    return missing_orgs, ghes_configs


def cmd_scm_list_missing_configs(args: argparse.Namespace) -> None:
    """List GHES orgs not onboarded to Semgrep."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")
    ghes_token = get_env_or_exit("GHES_TOKEN")

    print(f"GHES: {args.ghes_url}\n")

    missing_orgs, existing_configs = get_missing_orgs(
        args.ghes_url, ghes_token, semgrep_token
    )

    print(f"Existing SCM configs for this GHES: {len(existing_configs)}")
    for config in existing_configs:
        status = "✓" if config.status and config.status.ok else "✗"
        print(f"  [{status}] {config.namespace}")

    print()

    if not missing_orgs:
        print("All GHES organizations are onboarded to Semgrep.")
        return

    print(f"Missing SCM configs ({len(missing_orgs)} org(s)):\n")
    for org in missing_orgs:
        print(f"  {org.login}")


def cmd_scm_create_config(args: argparse.Namespace) -> None:
    """Create a single Semgrep SCM config for one GHES org."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")
    if not args.ghes_token and not args.scm_id:
        print("Error: You must provide a GHES token. Use --ghes-token (or GHES_TOKEN env var) or --scm-id.", file=sys.stderr)
        sys.exit(1)

    print(f"GHES: {args.ghes_url}")
    print(f"Org: {args.ghes_org}\n")

    if args.dry_run:
        print("Dry-run mode - the following SCM config would be created:\n")
        print(f"  {args.ghes_org}")
        print(f"      Type: {ScmType.GITHUB_ENTERPRISE.value}")
        print(f"      URL: {args.ghes_url}")
        if args.scm_id:
            print(f"      Token from SCM ID: {args.scm_id}")
        print(f"      Subscribe: {args.subscribe}")
        print(f"      Auto-scan: {args.auto_scan}")
        print(f"      Diff-enabled: {args.diff_enabled}")
        return

    semgrep_client = SemgrepClient(semgrep_token)

    try:
        if args.scm_id:
            config = semgrep_client.create_scm_config(
                scm_type=ScmType.GITHUB_ENTERPRISE,
                namespace=args.ghes_org,
                base_url=args.ghes_url,
                scm_config_id=args.scm_id,
                subscribe=args.subscribe,
                auto_scan=args.auto_scan,
                diff_enabled=args.diff_enabled,
            )
        else:
            config = semgrep_client.create_scm_config(
                scm_type=ScmType.GITHUB_ENTERPRISE,
                namespace=args.ghes_org,
                base_url=args.ghes_url,
                access_token=args.ghes_token,
                subscribe=args.subscribe,
                auto_scan=args.auto_scan,
                diff_enabled=args.diff_enabled,
            )
        print(f"Created SCM config for {args.ghes_org}")
        print(f"  SCM ID: {config.scm_id}")

        # Check health of the new config
        print("\nChecking SCM health...")
        try:
            result = semgrep_client.check_scm_config(config_id=config.id)
            if result.status.ok:
                print("  ✓ Connected")
            else:
                error_msg = result.status.error or "Unknown error"
                print(f"  ✗ Connection failed: {error_msg}")

            if result.token_scopes:
                available = [s for s in ScmTokenScopes.ALL_SCOPES if getattr(result.token_scopes, s, False)]
                if available:
                    print(f"  Token scopes: {', '.join(available)}")
                else:
                    print("  Token scopes: none")
        except Exception as e:
            print(f"  Warning: Could not check health: {e}")

        print()
        print(f"Use --scm-id {config.scm_id} to reuse this token with create-config or create-missing-configs.")

    except Exception as e:
        print(f"Failed to create config: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_scm_create_missing_configs(args: argparse.Namespace) -> None:
    """Create Semgrep SCM configs for GHES orgs not yet onboarded."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")
    if not args.ghes_token:
        print("Error: GHES token is required. Use --ghes-token or set GHES_TOKEN env var.", file=sys.stderr)
        sys.exit(1)

    print(f"GHES: {args.ghes_url}\n")

    # Fetch GHES orgs and Semgrep configs
    github_client = GithubClient(args.ghes_url, args.ghes_token)
    semgrep_client = SemgrepClient(semgrep_token)

    ghes_orgs = github_client.list_organizations()
    ghes_org_map = {org.login.lower(): org for org in ghes_orgs}

    all_configs = semgrep_client.list_scm_configs()
    normalized_ghes_url = args.ghes_url.rstrip("/").lower()
    existing_configs = [
        config for config in all_configs
        if config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
    ]
    configured_orgs = {config.namespace.lower() for config in existing_configs}

    # Determine which orgs to create configs for
    specified_org_names: list[str] | None = None
    if args.orgs:
        specified_org_names = args.orgs
    elif args.orgs_file:
        specified_org_names = [
            line.strip() for line in args.orgs_file
            if line.strip() and not line.strip().startswith("#")
        ]
        args.orgs_file.close()

    if specified_org_names:
        # User specified orgs - validate they exist on GHES
        orgs_to_create = []
        for org_name in specified_org_names:
            org = ghes_org_map.get(org_name.lower())
            if org:
                orgs_to_create.append(org)
            else:
                print(f"  ⚠ Org not found on GHES: {org_name}")

        if not orgs_to_create:
            print("\nNo valid orgs to create configs for.")
            return

        print(f"\nCreating configs for {len(orgs_to_create)} specified org(s)...\n")
    else:
        # Discover missing orgs
        orgs_to_create = [
            org for org in ghes_orgs
            if org.login.lower() not in configured_orgs
        ]

        if not orgs_to_create:
            print("All GHES organizations are already onboarded to Semgrep.")
            return

        print(f"Creating {len(orgs_to_create)} missing SCM config(s)...\n")

    # Dry-run mode: print what would be created and exit
    if args.dry_run:
        print("Dry-run mode - the following SCM configs would be created:\n")
        print(f"Settings: subscribe={args.subscribe}, auto_scan={args.auto_scan}, diff_enabled={args.diff_enabled}\n")
        for org in orgs_to_create:
            print(f"  {org.login}")
        print(f"\nTotal: {len(orgs_to_create)} config(s) would be created.")
        return

    if args.scm_id:
        print(f"Using token from SCM ID: {args.scm_id}\n")
    else:
        print("Using GHES token for each org\n")

    created = 0
    failed = 0
    unhealthy = 0

    for i, org in enumerate(orgs_to_create):
        try:
            if args.scm_id:
                # Reuse token from specified config
                config = semgrep_client.create_scm_config(
                    scm_type=ScmType.GITHUB_ENTERPRISE,
                    namespace=org.login,
                    base_url=args.ghes_url,
                    scm_config_id=args.scm_id,
                    subscribe=args.subscribe,
                    auto_scan=args.auto_scan,
                    diff_enabled=args.diff_enabled,
                )
            else:
                # Use the GHES token directly
                config = semgrep_client.create_scm_config(
                    scm_type=ScmType.GITHUB_ENTERPRISE,
                    namespace=org.login,
                    base_url=args.ghes_url,
                    access_token=args.ghes_token,
                    subscribe=args.subscribe,
                    auto_scan=args.auto_scan,
                    diff_enabled=args.diff_enabled,
                )

            # Check health
            try:
                result = semgrep_client.check_scm_config(config_id=config.id)
                if result.status.ok:
                    print(f"  ✓ Created: {org.login} (connected)")
                else:
                    error = result.status.error or "connection failed"
                    print(f"  ⚠ Created: {org.login} ({error})")
                    unhealthy += 1
            except Exception:
                print(f"  ✓ Created: {org.login} (health check failed)")

            created += 1

        except Exception as e:
            print(f"  ✗ Failed: {org.login} - {e}")
            failed += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(orgs_to_create) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Created: {created} ({unhealthy} not connected), Failed: {failed}")


def cmd_scm_update_configs(args: argparse.Namespace) -> None:
    """Update Semgrep SCM configs matching the GHES URL."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    print(f"GHES: {args.ghes_url}\n")

    semgrep_client = SemgrepClient(semgrep_token)

    # Get all configs and filter by GHES URL
    all_configs = semgrep_client.list_scm_configs()
    normalized_ghes_url = args.ghes_url.rstrip("/").lower()
    matching_configs = [
        config for config in all_configs
        if config.type == ScmType.GITHUB_ENTERPRISE.value
        and config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
    ]

    # Optionally filter by org names
    org_names: list[str] | None = None
    if args.orgs:
        org_names = args.orgs
    elif args.orgs_file:
        org_names = [
            line.strip() for line in args.orgs_file
            if line.strip() and not line.strip().startswith("#")
        ]
        args.orgs_file.close()

    if org_names:
        org_names_lower = {org.lower() for org in org_names}
        matching_configs = [
            config for config in matching_configs
            if config.namespace.lower() in org_names_lower
        ]

    if not matching_configs:
        print("No matching SCM configs found.")
        return

    # Build update payload from provided flags
    updates: dict[str, bool | str | None] = {
        "subscribe": args.subscribe,
        "auto_scan": args.auto_scan,
        "use_network_broker": args.use_network_broker,
        "diff_enabled": args.diff_enabled,
        "access_token": args.ghes_token,
    }

    # Filter to only non-None values
    updates_to_apply = {k: v for k, v in updates.items() if v is not None}

    if not updates_to_apply:
        print("No updates specified. Use flags like --subscribe true or --ghes-token to specify updates.")
        return

    print(f"Found {len(matching_configs)} matching config(s).")
    display_updates = {k: ("***" if k == "access_token" else v) for k, v in updates_to_apply.items()}
    print(f"Updates to apply: {display_updates}\n")

    if args.dry_run:
        print("Dry-run mode - the following configs would be updated:\n")
        for config in matching_configs:
            print(f"  {config.namespace} (ID: {config.id})")
        print(f"\nTotal: {len(matching_configs)} config(s) would be updated.")
        return

    updated = 0
    failed = 0

    for i, config in enumerate(matching_configs):
        try:
            semgrep_client.patch_scm_config(
                config_id=config.id,
                access_token=args.ghes_token,
                subscribe=args.subscribe,
                auto_scan=args.auto_scan,
                use_network_broker=args.use_network_broker,
                diff_enabled=args.diff_enabled,
            )
            print(f"  ✓ Updated: {config.namespace}")
            updated += 1

        except Exception as e:
            print(f"  ✗ Failed: {config.namespace} - {e}")
            failed += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(matching_configs) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Updated: {updated}, Failed: {failed}")


def cmd_scm_check_configs(args: argparse.Namespace) -> None:
    """Check the health of SCM configs matching the GHES URL."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    print(f"GHES: {args.ghes_url}\n")

    semgrep_client = SemgrepClient(semgrep_token)

    # Get all configs and filter by GHES URL
    all_configs = semgrep_client.list_scm_configs()
    normalized_ghes_url = args.ghes_url.rstrip("/").lower()
    matching_configs = [
        config for config in all_configs
        if config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
    ]

    # Optionally filter by org names
    if args.orgs:
        org_names_lower = {org.lower() for org in args.orgs}
        matching_configs = [
            config for config in matching_configs
            if config.namespace.lower() in org_names_lower
        ]

    if not matching_configs:
        print("No matching SCM configs found.")
        return

    required_scopes = getattr(args, "required_scopes", None)
    if required_scopes:
        print(f"Required scopes: {', '.join(required_scopes)}\n")

    print(f"Checking {len(matching_configs)} config(s)...\n")

    healthy = 0
    unhealthy = 0

    for i, config in enumerate(matching_configs):
        try:
            result = semgrep_client.check_scm_config(config_id=config.id)

            # Determine if config meets requirements
            is_healthy = result.status.ok
            missing_scopes: list[str] = []
            if is_healthy and required_scopes and result.token_scopes:
                missing_scopes = result.token_scopes.missing_scopes(required_scopes)
                if missing_scopes:
                    is_healthy = False

            if is_healthy:
                print(f"  ✓ Healthy: {config.namespace}")
                healthy += 1
            else:
                if not result.status.ok:
                    error_msg = result.status.error or "Connection failed"
                    print(f"  ✗ Unhealthy: {config.namespace} - {error_msg}")
                elif missing_scopes:
                    print(f"  ✗ Unhealthy: {config.namespace} - Missing scopes: {', '.join(missing_scopes)}")
                else:
                    print(f"  ✗ Unhealthy: {config.namespace}")
                unhealthy += 1

            # Print details
            if result.status.checked:
                print(f"      Last checked: {result.status.checked.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            if result.token_scopes:
                scopes = result.token_scopes
                enabled_scopes = [s for s in ScmTokenScopes.ALL_SCOPES if getattr(scopes, s, False)]
                print(f"      Token scopes: {', '.join(enabled_scopes) if enabled_scopes else 'none'}")

        except Exception as e:
            print(f"  ✗ Failed: {config.namespace} - {e}")
            unhealthy += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(matching_configs) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Healthy: {healthy}, Unhealthy: {unhealthy}")


def cmd_scm_delete_configs(args: argparse.Namespace) -> None:
    """Delete SCM configs matching the GHES URL."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    print(f"GHES: {args.ghes_url}\n")

    semgrep_client = SemgrepClient(semgrep_token)

    # Get all configs and filter by GHES URL
    all_configs = semgrep_client.list_scm_configs()
    normalized_ghes_url = args.ghes_url.rstrip("/").lower()
    matching_configs = [
        config for config in all_configs
        if config.type == ScmType.GITHUB_ENTERPRISE.value
        and config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
    ]

    org_names_lower = {org.lower() for org in args.orgs}
    matching_configs = [
        config for config in matching_configs
        if config.namespace.lower() in org_names_lower
    ]

    if not matching_configs:
        print("No matching SCM configs found.")
        return

    # If --unhealthy-only, check health and filter out healthy configs
    if args.unhealthy_only:
        print(f"Checking health of {len(matching_configs)} config(s)...\n")
        configs_to_delete = []
        skipped = []
        for i, config in enumerate(matching_configs):
            try:
                result = semgrep_client.check_scm_config(config_id=config.id)
                if result.status.ok:
                    skipped.append(config)
                else:
                    configs_to_delete.append(config)
            except Exception:
                # be safe: if we couldn't check health, don't delete
                # TODO: does raising an exception definitely mean that we couldn't check health? 
                skipped.append(config)

            # Delay between requests (skip after last one)
            if args.delay > 0 and i < len(matching_configs) - 1:
                time.sleep(args.delay)

        if skipped:
            print(f"Skipping {len(skipped)} healthy or undetermined config(s):\n")
            for config in skipped:
                print(f"  - {config.namespace} (ID: {config.id})")
            print()
    else:
        configs_to_delete = matching_configs

    if not configs_to_delete:
        print("No unhealthy configs to delete.")
        return

    print(f"Found {len(configs_to_delete)} config(s) to delete:\n")
    for config in configs_to_delete:
        print(f"  - {config.namespace} (ID: {config.id})")

    if args.dry_run:
        print(f"\n[DRY RUN] Would delete {len(configs_to_delete)} config(s).")
        return

    deleted = 0
    failed = 0

    for i, config in enumerate(configs_to_delete):
        try:
            semgrep_client.delete_scm_config(config_id=config.id)
            print(f"  ✓ Deleted: {config.namespace}")
            deleted += 1
        except Exception as e:
            print(f"  ✗ Failed: {config.namespace} - {e}")
            failed += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(configs_to_delete) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Deleted: {deleted}, Failed: {failed}")


def get_namespace_from_url(url: str) -> tuple[str, str] | None:
    """Extract base URL and namespace from a project URL.

    Returns (base_url, namespace) or None if unable to parse.
    Example: "https://github.com/test-org/repo" -> ("https://github.com", "test-org")
    """
    try:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        path_parts = parsed.path.strip("/").split("/")
        if path_parts:
            return (base_url, path_parts[0])
    except Exception:
        pass
    return None


def filter_projects_by_healthy_scm(
    projects: list[Project],
    scm_configs: list[ScmConfig],
    required_scopes: list[str] | None = None,
) -> tuple[list[Project], list[Project]]:
    """Filter projects to only those with healthy SCM configs.

    Args:
        projects: List of projects to filter
        scm_configs: List of SCM configs to check against
        required_scopes: Optional list of scope names to require

    Returns (healthy_projects, skipped_projects).
    """
    # Build a set of healthy (base_url, namespace) tuples
    healthy_namespaces: set[tuple[str, str]] = set()
    for config in scm_configs:
        if config.meets_requirements(required_scopes) and config.base_url:
            healthy_namespaces.add((config.base_url.rstrip("/").lower(), config.namespace.lower()))

    healthy: list[Project] = []
    skipped: list[Project] = []

    for project in projects:
        if not project.url:
            skipped.append(project)
            continue

        parsed = get_namespace_from_url(project.url)
        if parsed:
            base_url, namespace = parsed
            if (base_url.lower(), namespace.lower()) in healthy_namespaces:
                healthy.append(project)
            else:
                skipped.append(project)
        else:
            skipped.append(project)

    return healthy, skipped


def filter_repos_by_healthy_scm(
    repos: list[Repo],
    scm_configs: list[ScmConfig],
    required_scopes: list[str] | None = None,
) -> tuple[list[Repo], list[Repo]]:
    """Filter repos to only those with healthy SCM configs.

    Args:
        repos: List of repos to filter
        scm_configs: List of SCM configs to check against
        required_scopes: Optional list of scope names to require

    Returns (healthy_repos, skipped_repos).
    """
    # Build a set of healthy (base_url, namespace) tuples
    healthy_namespaces: set[tuple[str, str]] = set()
    for config in scm_configs:
        if config.meets_requirements(required_scopes) and config.base_url:
            healthy_namespaces.add((config.base_url.rstrip("/").lower(), config.namespace.lower()))

    healthy: list[Repo] = []
    skipped: list[Repo] = []

    for repo in repos:
        if not repo.url:
            skipped.append(repo)
            continue

        parsed = get_namespace_from_url(repo.url)
        if parsed:
            base_url, namespace = parsed
            if (base_url.lower(), namespace.lower()) in healthy_namespaces:
                healthy.append(repo)
            else:
                skipped.append(repo)
        else:
            skipped.append(repo)

    return healthy, skipped


def cmd_scm_onboard_repos(args: argparse.Namespace) -> None:
    """Onboard uninitialized repos to Semgrep managed scans."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    semgrep_client = SemgrepClient(semgrep_token)
    print(f"Deployment: {semgrep_client.deployment.name}\n")

    if args.dry_run:
        print("[DRY RUN] No changes will be made\n")

    # Fetch SCM configs if checking is enabled
    scm_configs: list[ScmConfig] = []
    if args.check_scm:
        print("Fetching SCM configs...")
        scm_configs = semgrep_client.list_scm_configs()

        # Filter to GHES configs if --ghes-url is provided
        if args.ghes_url:
            normalized_ghes_url = args.ghes_url.rstrip("/").lower()
            scm_configs = [
                config for config in scm_configs
                if config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
            ]

        healthy_count = sum(1 for c in scm_configs if c.is_healthy)
        print(f"Found {len(scm_configs)} SCM configs ({healthy_count} healthy)")

    print("\nFetching uninitialized repos...")
    repos = semgrep_client.search_repos(setup=False)
    print(f"Found {len(repos)} uninitialized repos")

    if not repos:
        print("No repos to onboard")
        return

    # Filter out archived repos
    archived_repos = [r for r in repos if r.is_archived]
    repos = [r for r in repos if not r.is_archived]
    if archived_repos:
        print(f"Filtered out {len(archived_repos)} archived repos")

    if not repos:
        print("No non-archived repos to onboard")
        return

    # Filter by GHES URL if provided
    if args.ghes_url:
        normalized_ghes_url = args.ghes_url.rstrip("/").lower()
        repos = [
            r for r in repos
            if r.url and get_namespace_from_url(r.url) and
               get_namespace_from_url(r.url)[0].lower() == normalized_ghes_url
        ]
        print(f"Filtered to {len(repos)} repos matching GHES URL")

    if not repos:
        print("No repos matching criteria")
        return

    # Filter by healthy SCM configs if enabled
    skipped: list[Repo] = []
    if args.check_scm:
        required_scopes = getattr(args, "required_scopes", None)
        repos, skipped = filter_repos_by_healthy_scm(repos, scm_configs, required_scopes)
        if skipped:
            print(f"\nSkipping {len(skipped)} repos (no healthy SCM config):")
            for repo in skipped[:10]:  # Show first 10
                print(f"  - {repo.name}")
            if len(skipped) > 10:
                print(f"  ... and {len(skipped) - 10} more")

    if not repos:
        print("\nNo repos with healthy SCM configs to onboard")
        return

    print(f"\nRepos to onboard ({len(repos)}):")
    for repo in repos[:20]:  # Show first 20
        print(f"  - {repo.name}")
    if len(repos) > 20:
        print(f"  ... and {len(repos) - 20} more")

    repo_ids = [r.id for r in repos]
    num_batches = (len(repo_ids) + args.batch_size - 1) // args.batch_size

    if args.dry_run:
        print(f"\n[DRY RUN] Would enable managed scans for {len(repo_ids)} repos:")
        print(f"  - diffScan: {'enabled' if args.diff_scan else 'disabled'}")
        print(f"  - fullScan: {'enabled' if args.full_scan else 'disabled'}")
        print(f"  - batches: {num_batches} (batch size: {args.batch_size})")
        return

    print(f"\nEnabling managed scans for {len(repo_ids)} repos in {num_batches} batches...")

    all_updated: list[str] = []
    failed_batches: list[tuple[int, list[int], str]] = []
    failed_count = 0

    for i in range(0, len(repo_ids), args.batch_size):
        batch = repo_ids[i : i + args.batch_size]
        batch_num = (i // args.batch_size) + 1

        try:
            updated = semgrep_client.bulk_update_repos(
                repo_ids=batch,
                enable_diff_scan=args.diff_scan,
                enable_full_scan=args.full_scan,
            )
            all_updated.extend(updated)
            print(f"  Batch {batch_num}/{num_batches}: +{len(updated)} repos (total: {len(all_updated)}/{len(repo_ids)})")
        except Exception as e:
            failed_count += len(batch)
            print(f"  Batch {batch_num}/{num_batches}: ERROR - {e}")
            failed_batches.append((batch_num, batch, str(e)))

        # Delay between batches (skip after last one)
        if args.delay > 0 and i + args.batch_size < len(repo_ids):
            time.sleep(args.delay)

    print(f"\nDone. Successfully onboarded: {len(all_updated)}, Failed: {failed_count}")

    if failed_batches:
        print(f"\nFailed to update {len(failed_batches)} batches:")
        for batch_num, batch_ids, error in failed_batches:
            print(f"  Batch {batch_num} ({len(batch_ids)} repos): {error}")


def cmd_scm_trigger_scans(args: argparse.Namespace) -> None:
    """Trigger scans for repos that haven't had a full scan."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    semgrep_client = SemgrepClient(semgrep_token)
    print(f"Deployment: {semgrep_client.deployment.name}\n")

    if args.dry_run:
        print("[DRY RUN] No scans will be triggered\n")

    # Fetch SCM configs if checking is enabled
    scm_configs: list[ScmConfig] = []
    if args.check_scm:
        print("Fetching SCM configs...")
        scm_configs = semgrep_client.list_scm_configs()

        # Filter to GHES configs if --ghes-url is provided
        if args.ghes_url:
            normalized_ghes_url = args.ghes_url.rstrip("/").lower()
            scm_configs = [
                config for config in scm_configs
                if config.base_url and config.base_url.rstrip("/").lower() == normalized_ghes_url
            ]

        healthy_count = sum(1 for c in scm_configs if c.is_healthy)
        print(f"Found {len(scm_configs)} SCM configs ({healthy_count} healthy)")

    # Fetch initialized repos (setup=True means they've been onboarded)
    print("\nFetching initialized repos...")
    repos = semgrep_client.search_repos(setup=True)
    print(f"Found {len(repos)} initialized repos")

    if not repos:
        print("No repos to scan")
        return

    # Filter out archived repos
    archived_repos = [r for r in repos if r.is_archived]
    repos = [r for r in repos if not r.is_archived]
    if archived_repos:
        print(f"Filtered out {len(archived_repos)} archived repos")

    if not repos:
        print("No non-archived repos to scan")
        return

    # Filter by GHES URL if provided
    if args.ghes_url:
        normalized_ghes_url = args.ghes_url.rstrip("/").lower()
        repos = [
            r for r in repos
            if r.url and get_namespace_from_url(r.url) and
               get_namespace_from_url(r.url)[0].lower() == normalized_ghes_url
        ]
        print(f"Filtered to {len(repos)} repos matching GHES URL")

    if not repos:
        print("No repos matching criteria")
        return

    # Filter by healthy SCM configs if enabled
    skipped: list[Repo] = []
    if args.check_scm:
        required_scopes = getattr(args, "required_scopes", None)
        repos, skipped = filter_repos_by_healthy_scm(repos, scm_configs, required_scopes)
        if skipped:
            print(f"\nSkipping {len(skipped)} repos (no healthy SCM config):")
            for repo in skipped[:10]:  # Show first 10
                print(f"  - {repo.name}")
            if len(skipped) > 10:
                print(f"  ... and {len(skipped) - 10} more")

    if not repos:
        print("\nNo repos with healthy SCM configs to scan")
        return

    if args.dry_run:
        if args.skip_scan_check:
            print(f"\n[DRY RUN] Would trigger scans for all {len(repos)} repos (--skip-scan-check)")
        else:
            print(f"\n[DRY RUN] Would check {len(repos)} repos and trigger scans for those without full scans")
        return

    # Process repos - either skip check or check-and-trigger as we go
    if args.skip_scan_check:
        print(f"\nSkipping scan check (--skip-scan-check), triggering for all {len(repos)} repos...")
        repo_ids = [r.id for r in repos]
        num_batches = (len(repo_ids) + args.batch_size - 1) // args.batch_size

        triggered_count = 0
        failed_count = 0

        for i in range(0, len(repo_ids), args.batch_size):
            batch = repo_ids[i : i + args.batch_size]
            batch_num = (i // args.batch_size) + 1

            try:
                semgrep_client.trigger_scans(repo_ids=batch)
                triggered_count += len(batch)
                print(f"  Batch {batch_num}/{num_batches}: +{len(batch)} scans (total: {triggered_count}/{len(repo_ids)})")
            except Exception as e:
                failed_count += len(batch)
                print(f"  Batch {batch_num}/{num_batches}: ERROR - {e}")

            # Delay between batches (skip after last one)
            if args.delay > 0 and i + args.batch_size < len(repo_ids):
                time.sleep(args.delay)

        print(f"\nDone. Successfully triggered: {triggered_count}, Failed: {failed_count}")

    else:
        # Check and trigger as we go
        print(f"\nChecking repos and triggering scans as we go...")
        print(f"  Batch size: {args.batch_size}, Delay between batches: {args.delay}s\n")

        pending_batch: list[int] = []
        checked_count = 0
        skipped_count = 0
        triggered_count = 0
        failed_count = 0
        batch_num = 0

        def trigger_batch():
            nonlocal pending_batch, triggered_count, failed_count, batch_num
            if not pending_batch:
                return
            batch_num += 1
            try:
                semgrep_client.trigger_scans(repo_ids=pending_batch)
                triggered_count += len(pending_batch)
                print(f"  Triggered batch {batch_num}: +{len(pending_batch)} scans (total triggered: {triggered_count}, checked: {checked_count}/{len(repos)})")
            except Exception as e:
                failed_count += len(pending_batch)
                print(f"  Batch {batch_num} ERROR: {e}")
            pending_batch = []
            if args.delay > 0:
                time.sleep(args.delay)

        for i, repo in enumerate(repos):
            checked_count = i + 1

            try:
                if semgrep_client.has_full_scan(repo.id):
                    skipped_count += 1
                else:
                    pending_batch.append(repo.id)
            except Exception as e:
                print(f"  Warning: Could not check {repo.name}: {e}, including anyway")
                pending_batch.append(repo.id)

            # Trigger when batch is full
            if len(pending_batch) >= args.batch_size:
                trigger_batch()

            # Progress update every 100 repos
            if checked_count % 100 == 0:
                print(f"  Progress: checked {checked_count}/{len(repos)}, triggered: {triggered_count}, skipped: {skipped_count}")

            # Delay between checks
            if args.check_delay > 0 and i < len(repos) - 1:
                time.sleep(args.check_delay)

        # Trigger any remaining
        trigger_batch()

        print(f"\nDone. Checked: {checked_count}, Triggered: {triggered_count}, Skipped: {skipped_count}, Failed: {failed_count}")


# GitLab Self-Managed commands
def cmd_glsm_list_configs(args: argparse.Namespace) -> None:
    """List Semgrep SCM configs for GitLab Self-Managed."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")
    client = SemgrepClient(semgrep_token)

    print(f"Deployment: {client.deployment.name} ({client.deployment.slug})\n")

    configs = client.list_scm_configs()

    # Filter to GLSM configs only
    configs = [c for c in configs if c.type == ScmType.GITLAB_SELFMANAGED.value]

    # Filter by GLSM URL if provided
    if args.glsm_url:
        normalized_glsm_url = args.glsm_url.rstrip("/").lower()
        configs = [
            c for c in configs
            if c.base_url and c.base_url.rstrip("/").lower() == normalized_glsm_url
        ]

    required_scopes = getattr(args, "required_scopes", None)

    if args.unhealthy_only:
        configs = [c for c in configs if not c.meets_requirements(required_scopes)]

    if not configs:
        if args.unhealthy_only:
            print("No unhealthy GLSM SCM configs found.")
        else:
            print("No GLSM SCM configs found.")
        return

    label = "unhealthy " if args.unhealthy_only else ""
    print(f"Found {len(configs)} {label}GLSM SCM config(s):\n")
    for config in configs:
        meets_reqs = config.meets_requirements(required_scopes)
        status = "✓" if meets_reqs else "✗"
        print(f"  [{status}] {config.namespace}")
        if config.base_url:
            print(f"      URL: {config.base_url}")
        print(f"      ID: {config.id}")
        if not meets_reqs:
            if config.status and config.status.error:
                print(f"      Error: {config.status.error}")
            elif not config.is_healthy:
                print(f"      Error: Connection unhealthy")
            if required_scopes and config.token_scopes:
                missing = config.token_scopes.missing_scopes(required_scopes)
                if missing:
                    print(f"      Missing scopes: {', '.join(missing)}")
        print()


def cmd_glsm_create_configs(args: argparse.Namespace) -> None:
    """Create Semgrep SCM configs for GitLab Self-Managed groups."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")
    if not args.glsm_token:
        print("Error: GitLab token is required. Use --glsm-token or set GLSM_TOKEN env var.", file=sys.stderr)
        sys.exit(1)

    print(f"GitLab Self-Managed: {args.glsm_url}\n")

    # Read group names from args or file
    if args.groups:
        group_names = args.groups
    else:
        group_names = [
            line.strip() for line in args.groups_file
            if line.strip() and not line.strip().startswith("#")
        ]
        args.groups_file.close()

    if not group_names:
        print("No group names provided.")
        return

    if args.dry_run:
        print("Dry-run mode - the following SCM configs would be created:\n")
        print(f"Settings: subscribe={args.subscribe}, auto_scan={args.auto_scan}, diff_enabled={args.diff_enabled}\n")
        for group in group_names:
            print(f"  {group}")
        print(f"\nTotal: {len(group_names)} config(s) would be created.")
        return

    semgrep_client = SemgrepClient(semgrep_token)

    created = 0
    failed = 0
    unhealthy = 0

    for i, group_name in enumerate(group_names):
        try:
            config = semgrep_client.create_scm_config(
                scm_type=ScmType.GITLAB_SELFMANAGED,
                namespace=group_name,
                base_url=args.glsm_url,
                access_token=args.glsm_token,
                subscribe=args.subscribe,
                auto_scan=args.auto_scan,
                diff_enabled=args.diff_enabled,
            )

            try:
                result = semgrep_client.check_scm_config(config_id=config.id)
                if result.status.ok:
                    print(f"  ✓ Created: {group_name} (connected)")
                else:
                    error = result.status.error or "connection failed"
                    print(f"  ⚠ Created: {group_name} ({error})")
                    unhealthy += 1
            except Exception:
                print(f"  ✓ Created: {group_name} (health check failed)")

            created += 1

        except Exception as e:
            print(f"  ✗ Failed: {group_name} - {e}")
            failed += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(group_names) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Created: {created} ({unhealthy} not connected), Failed: {failed}")


def cmd_glsm_update_configs(args: argparse.Namespace) -> None:
    """Update Semgrep SCM configs for GitLab Self-Managed groups."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    print(f"GitLab Self-Managed: {args.glsm_url}\n")

    semgrep_client = SemgrepClient(semgrep_token)

    # Get all configs and filter by type + URL
    all_configs = semgrep_client.list_scm_configs()
    normalized_glsm_url = args.glsm_url.rstrip("/").lower()
    matching_configs = [
        config for config in all_configs
        if config.type == ScmType.GITLAB_SELFMANAGED.value
        and config.base_url and config.base_url.rstrip("/").lower() == normalized_glsm_url
    ]

    # Optionally filter by group names
    group_names: list[str] | None = None
    if args.groups:
        group_names = args.groups
    elif args.groups_file:
        group_names = [
            line.strip() for line in args.groups_file
            if line.strip() and not line.strip().startswith("#")
        ]
        args.groups_file.close()

    if group_names:
        group_names_lower = {g.lower() for g in group_names}
        matching_configs = [
            config for config in matching_configs
            if config.namespace.lower() in group_names_lower
        ]

    if not matching_configs:
        print("No matching SCM configs found.")
        return

    updates: dict[str, bool | str | None] = {
        "subscribe": args.subscribe,
        "auto_scan": args.auto_scan,
        "use_network_broker": args.use_network_broker,
        "diff_enabled": args.diff_enabled,
        "access_token": args.glsm_token,
    }
    updates_to_apply = {k: v for k, v in updates.items() if v is not None}

    if not updates_to_apply:
        print("No updates specified. Use flags like --subscribe true or --glsm-token to specify updates.")
        return

    print(f"Found {len(matching_configs)} matching config(s).")
    display_updates = {k: ("***" if k == "access_token" else v) for k, v in updates_to_apply.items()}
    print(f"Updates to apply: {display_updates}\n")

    if args.dry_run:
        print("Dry-run mode - the following configs would be updated:\n")
        for config in matching_configs:
            print(f"  {config.namespace} (ID: {config.id})")
        print(f"\nTotal: {len(matching_configs)} config(s) would be updated.")
        return

    updated = 0
    failed = 0

    for i, config in enumerate(matching_configs):
        try:
            semgrep_client.patch_scm_config(
                config_id=config.id,
                access_token=args.glsm_token,
                subscribe=args.subscribe,
                auto_scan=args.auto_scan,
                use_network_broker=args.use_network_broker,
                diff_enabled=args.diff_enabled,
            )
            print(f"  ✓ Updated: {config.namespace}")
            updated += 1

        except Exception as e:
            print(f"  ✗ Failed: {config.namespace} - {e}")
            failed += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(matching_configs) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Updated: {updated}, Failed: {failed}")


def cmd_glsm_delete_configs(args: argparse.Namespace) -> None:
    """Delete Semgrep SCM configs for GitLab Self-Managed groups."""
    semgrep_token = get_env_or_exit("SEMGREP_APP_TOKEN")

    print(f"GitLab Self-Managed: {args.glsm_url}\n")

    semgrep_client = SemgrepClient(semgrep_token)

    # Get all configs and filter by type + URL
    all_configs = semgrep_client.list_scm_configs()
    normalized_glsm_url = args.glsm_url.rstrip("/").lower()
    matching_configs = [
        config for config in all_configs
        if config.type == ScmType.GITLAB_SELFMANAGED.value
        and config.base_url and config.base_url.rstrip("/").lower() == normalized_glsm_url
    ]

    # Filter by group names (required to prevent accidents)
    group_names_lower = {g.lower() for g in args.groups}
    matching_configs = [
        config for config in matching_configs
        if config.namespace.lower() in group_names_lower
    ]

    if not matching_configs:
        print("No matching SCM configs found.")
        return

    # If --unhealthy-only, check health and filter out healthy configs
    if args.unhealthy_only:
        print(f"Checking health of {len(matching_configs)} config(s)...\n")
        configs_to_delete = []
        skipped = []
        for i, config in enumerate(matching_configs):
            try:
                result = semgrep_client.check_scm_config(config_id=config.id)
                if result.status.ok:
                    skipped.append(config)
                else:
                    configs_to_delete.append(config)
            except Exception:
                # be safe: if we couldn't check health, don't delete
                # TODO: does raising an exception definitely mean that we couldn't check health? 

                skipped.append(config)

            # Delay between requests (skip after last one)
            if args.delay > 0 and i < len(matching_configs) - 1:
                time.sleep(args.delay)

        if skipped:
            print(f"Skipping {len(skipped)} healthy or undetermined config(s):\n")
            for config in skipped:
                print(f"  - {config.namespace} (ID: {config.id})")
            print()
    else:
        configs_to_delete = matching_configs

    if not configs_to_delete:
        print("No unhealthy configs to delete.")
        return

    print(f"Found {len(configs_to_delete)} config(s) to delete:\n")
    for config in configs_to_delete:
        print(f"  - {config.namespace} (ID: {config.id})")

    if args.dry_run:
        print(f"\n[DRY RUN] Would delete {len(configs_to_delete)} config(s).")
        return

    deleted = 0
    failed = 0

    for i, config in enumerate(configs_to_delete):
        try:
            semgrep_client.delete_scm_config(config_id=config.id)
            print(f"  ✓ Deleted: {config.namespace}")
            deleted += 1
        except Exception as e:
            print(f"  ✗ Failed: {config.namespace} - {e}")
            failed += 1

        # Delay between requests (skip after last one)
        if args.delay > 0 and i < len(configs_to_delete) - 1:
            time.sleep(args.delay)

    print()
    print(f"Done. Deleted: {deleted}, Failed: {failed}")


# GHES commands
def cmd_ghes_list_orgs(args: argparse.Namespace) -> None:
    """List all organizations on GHES."""
    ghes_token = get_env_or_exit("GHES_TOKEN")
    client = GithubClient(args.ghes_url, ghes_token)

    print(f"GHES: {args.ghes_url}\n")

    orgs = client.list_organizations()
    if not orgs:
        print("No organizations found.")
        return

    print(f"Found {len(orgs)} organization(s):\n")
    for org in orgs:
        print(f"  {org.login}")
        if org.description:
            print(f"      {org.description}")


def main():
    load_dotenv()

    # Deprecated alias: redirect `scm` to `ghes`
    if len(sys.argv) > 1 and sys.argv[1] == "scm":
        print("Warning: 'scm' is deprecated, use 'ghes' instead.", file=sys.stderr)
        sys.argv[1] = "ghes"

    parser = argparse.ArgumentParser(
        prog="semgrep-scm-util",
        description="Tools for managing Semgrep SCM configs across GitHub Enterprise Server and GitLab Self-Managed instances.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Helper to add --ghes-url argument to subcommands
    def add_ghes_url_arg(subparser: argparse.ArgumentParser, required: bool = True) -> None:
        subparser.add_argument(
            "--ghes-url",
            default=os.environ.get("GHES_URL"),
            required=required and not os.environ.get("GHES_URL"),
            metavar="URL",
            help="GitHub Enterprise Server URL (e.g., https://github.example.com). Can also be set via GHES_URL env var.",
        )

    # GHES command group
    ghes_parser = subparsers.add_parser("ghes", help="GitHub Enterprise Server operations")
    ghes_subparsers = ghes_parser.add_subparsers(dest="ghes_command", required=True)

    ghes_list_orgs = ghes_subparsers.add_parser(
        "list-orgs",
        help="List all organizations on GHES",
    )
    add_ghes_url_arg(ghes_list_orgs, required=True)
    ghes_list_orgs.set_defaults(func=cmd_ghes_list_orgs)

    ghes_list_configs = ghes_subparsers.add_parser(
        "list-configs",
        help="List all Semgrep SCM configs",
    )
    add_ghes_url_arg(ghes_list_configs, required=False)
    ghes_list_configs.add_argument(
        "--unhealthy-only",
        action="store_true",
        help="Only show unhealthy SCM configs.",
    )
    ghes_list_configs.add_argument(
        "--required-scopes",
        type=parse_scopes,
        metavar="SCOPES",
        help="Comma-separated list of required token scopes for health check. "
             "If not specified, only connection status is checked. "
             f"Valid scopes: {', '.join(ScmTokenScopes.ALL_SCOPES)}",
    )
    ghes_list_configs.set_defaults(func=cmd_scm_list_configs)

    ghes_list_missing = ghes_subparsers.add_parser(
        "list-missing-configs",
        help="List GHES orgs not onboarded to Semgrep",
    )
    add_ghes_url_arg(ghes_list_missing, required=True)
    ghes_list_missing.set_defaults(func=cmd_scm_list_missing_configs)

    ghes_create_config = ghes_subparsers.add_parser(
        "create-config",
        help="Create a single SCM config for one GHES org",
    )
    add_ghes_url_arg(ghes_create_config, required=True)
    ghes_create_config.add_argument(
        "--ghes-token",
        default=os.environ.get("GHES_TOKEN"),
        metavar="TOKEN",
        help="GitHub Enterprise Server personal access token. Can also be set via GHES_TOKEN env var.",
    )
    ghes_create_config.add_argument(
        "--ghes-org",
        required=True,
        metavar="ORG",
        help="Organization name to create config for.",
    )
    ghes_create_config.add_argument(
        "--scm-id",
        type=int,
        metavar="ID",
        help="SCM ID of an existing config to reuse token from (alternative to --ghes-token).",
    )
    ghes_create_config.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making any changes.",
    )
    ghes_create_config.add_argument(
        "--subscribe",
        action="store_true",
        help="Subscribe to webhooks (default: disabled).",
    )
    ghes_create_config.add_argument(
        "--auto-scan",
        action="store_true",
        help="Enable scanning of new repos in the org automatically (default: disabled).",
    )
    ghes_create_config.add_argument(
        "--diff-enabled",
        action="store_true",
        help="Enable diff scanning (default: disabled).",
    )
    ghes_create_config.set_defaults(func=cmd_scm_create_config)

    ghes_create_missing = ghes_subparsers.add_parser(
        "create-missing-configs",
        help="Create SCM configs for GHES orgs not yet onboarded",
    )
    add_ghes_url_arg(ghes_create_missing, required=True)
    ghes_create_missing.add_argument(
        "--ghes-token",
        default=os.environ.get("GHES_TOKEN"),
        metavar="TOKEN",
        help="GitHub Enterprise Server personal access token. Can also be set via GHES_TOKEN env var.",
    )
    orgs_group = ghes_create_missing.add_mutually_exclusive_group()
    orgs_group.add_argument(
        "--orgs",
        nargs="+",
        metavar="ORG",
        help="Specific org names to create configs for.",
    )
    orgs_group.add_argument(
        "--orgs-file",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="File containing org names (one per line).",
    )
    ghes_create_missing.add_argument(
        "--scm-id",
        type=int,
        metavar="ID",
        help="SCM ID of an existing config to reuse token from. Get this from 'ghes list-configs' or 'ghes create-config'.",
    )
    ghes_create_missing.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making any changes.",
    )
    ghes_create_missing.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between creating each config (default: 1.0 seconds).",
    )
    ghes_create_missing.add_argument(
        "--subscribe",
        action="store_true",
        help="Subscribe to webhooks (default: disabled).",
    )
    ghes_create_missing.add_argument(
        "--auto-scan",
        action="store_true",
        help="Enable scanning of new repos in the org automatically (default: disabled).",
    )
    ghes_create_missing.add_argument(
        "--diff-enabled",
        action="store_true",
        help="Enable diff scanning (default: disabled).",
    )
    ghes_create_missing.set_defaults(func=cmd_scm_create_missing_configs)

    ghes_update_configs = ghes_subparsers.add_parser(
        "update-configs",
        help="Update SCM configs matching the GHES URL",
    )
    add_ghes_url_arg(ghes_update_configs, required=True)
    ghes_update_orgs_group = ghes_update_configs.add_mutually_exclusive_group()
    ghes_update_orgs_group.add_argument(
        "--orgs",
        nargs="+",
        metavar="ORG",
        help="Specific org names to update (if not provided, updates all matching GHES URL).",
    )
    ghes_update_orgs_group.add_argument(
        "--orgs-file",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="File containing org names to update (one per line).",
    )
    ghes_update_configs.add_argument(
        "--subscribe",
        type=parse_bool,
        metavar="BOOL",
        help="Set subscribe to webhooks (true/false).",
    )
    ghes_update_configs.add_argument(
        "--auto-scan",
        type=parse_bool,
        metavar="BOOL",
        help="Enable scanning of new repos in the org automatically (true/false).",
    )
    ghes_update_configs.add_argument(
        "--use-network-broker",
        type=parse_bool,
        metavar="BOOL",
        help="Set use network broker (true/false).",
    )
    ghes_update_configs.add_argument(
        "--diff-enabled",
        type=parse_bool,
        metavar="BOOL",
        help="Set diff scanning enabled (true/false).",
    )
    ghes_update_configs.add_argument(
        "--ghes-token",
        nargs="?",
        const=os.environ.get("GHES_TOKEN"),
        default=None,
        metavar="TOKEN",
        help="Update the access token. If no value is given, uses GHES_TOKEN env var.",
    )
    ghes_update_configs.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without making any changes.",
    )
    ghes_update_configs.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between updating each config (default: 1.0 seconds).",
    )
    ghes_update_configs.set_defaults(func=cmd_scm_update_configs)

    ghes_check_configs = ghes_subparsers.add_parser(
        "check-configs",
        help="Check the health of SCM configs matching the GHES URL",
    )
    add_ghes_url_arg(ghes_check_configs, required=True)
    ghes_check_configs.add_argument(
        "--orgs",
        nargs="+",
        metavar="ORG",
        help="Specific org names to check (if not provided, checks all matching GHES URL).",
    )
    ghes_check_configs.add_argument(
        "--required-scopes",
        type=parse_scopes,
        metavar="SCOPES",
        help="Comma-separated list of required token scopes for health check. "
             "If not specified, only connection status is checked. "
             f"Valid scopes: {', '.join(ScmTokenScopes.ALL_SCOPES)}",
    )
    ghes_check_configs.add_argument(
        "--delay",
        type=float,
        default=0.25,
        metavar="SECONDS",
        help="Delay between checking each config (default: 0.25 seconds).",
    )
    ghes_check_configs.set_defaults(func=cmd_scm_check_configs)

    ghes_delete_configs = ghes_subparsers.add_parser(
        "delete-configs",
        help="Delete SCM configs matching the GHES URL",
    )
    add_ghes_url_arg(ghes_delete_configs, required=True)
    ghes_delete_configs.add_argument(
        "--orgs",
        nargs="+",
        metavar="ORG",
        required=True,
        help="Org names to delete (required to prevent accidental deletion).",
    )
    ghes_delete_configs.add_argument(
        "--unhealthy-only",
        action="store_true",
        help="Only delete configs that are unhealthy; skip healthy ones.",
    )
    ghes_delete_configs.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making any changes.",
    )
    ghes_delete_configs.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Delay between each request to check or delete a config (default: 0.5 seconds).",
    )
    ghes_delete_configs.set_defaults(func=cmd_scm_delete_configs)

    ghes_onboard_repos = ghes_subparsers.add_parser(
        "onboard-repos",
        help="Onboard uninitialized repos to Semgrep managed scans",
    )
    add_ghes_url_arg(ghes_onboard_repos, required=False)
    ghes_onboard_repos.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making any updates.",
    )
    ghes_onboard_repos.add_argument(
        "--diff-scan",
        type=parse_bool,
        default=True,
        metavar="BOOL",
        help="Enable diff scanning (true/false, default: true).",
    )
    ghes_onboard_repos.add_argument(
        "--full-scan",
        type=parse_bool,
        default=True,
        metavar="BOOL",
        help="Enable full scanning (true/false, default: true).",
    )
    ghes_onboard_repos.add_argument(
        "--batch-size",
        type=int,
        default=250,
        metavar="N",
        help="Number of repos to update per batch (default: 250).",
    )
    ghes_onboard_repos.add_argument(
        "--check-scm",
        type=parse_bool,
        default=True,
        metavar="BOOL",
        help="Only onboard repos with healthy SCM configs (true/false, default: true).",
    )
    ghes_onboard_repos.add_argument(
        "--required-scopes",
        type=parse_scopes,
        metavar="SCOPES",
        help="Comma-separated list of required token scopes when --check-scm is true. "
             "If not specified, only connection status is checked. "
             f"Valid scopes: {', '.join(ScmTokenScopes.ALL_SCOPES)}",
    )
    ghes_onboard_repos.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between batches (default: 1.0 seconds).",
    )
    ghes_onboard_repos.set_defaults(func=cmd_scm_onboard_repos)

    ghes_trigger_scans = ghes_subparsers.add_parser(
        "trigger-scans",
        help="Trigger scans for repos that haven't had a full scan",
    )
    add_ghes_url_arg(ghes_trigger_scans, required=False)
    ghes_trigger_scans.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be triggered without making any changes.",
    )
    ghes_trigger_scans.add_argument(
        "--batch-size",
        type=int,
        default=10,
        metavar="N",
        help="Number of scans to trigger per batch (default: 10).",
    )
    ghes_trigger_scans.add_argument(
        "--check-scm",
        type=parse_bool,
        default=True,
        metavar="BOOL",
        help="Only scan repos with healthy SCM configs (true/false, default: true).",
    )
    ghes_trigger_scans.add_argument(
        "--required-scopes",
        type=parse_scopes,
        metavar="SCOPES",
        help="Comma-separated list of required token scopes when --check-scm is true. "
             "If not specified, only connection status is checked. "
             f"Valid scopes: {', '.join(ScmTokenScopes.ALL_SCOPES)}",
    )
    ghes_trigger_scans.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between batches (default: 1.0 seconds).",
    )
    ghes_trigger_scans.add_argument(
        "--check-delay",
        type=float,
        default=0.1,
        metavar="SECONDS",
        help="Delay between checking each repo for existing scans (default: 0.1 seconds).",
    )
    ghes_trigger_scans.add_argument(
        "--skip-scan-check",
        action="store_true",
        help="Skip checking for existing scans and trigger for all repos.",
    )
    ghes_trigger_scans.set_defaults(func=cmd_scm_trigger_scans)

    # GitLab Self-Managed command group
    def add_glsm_url_arg(subparser: argparse.ArgumentParser, required: bool = True) -> None:
        subparser.add_argument(
            "--glsm-url",
            default=os.environ.get("GLSM_URL"),
            required=required and not os.environ.get("GLSM_URL"),
            metavar="URL",
            help="GitLab Self-Managed URL (e.g., https://gitlab.example.com). Can also be set via GLSM_URL env var.",
        )

    glsm_parser = subparsers.add_parser("glsm", help="GitLab Self-Managed SCM config operations")
    glsm_subparsers = glsm_parser.add_subparsers(dest="glsm_command", required=True)

    glsm_list_configs = glsm_subparsers.add_parser(
        "list-configs",
        help="List Semgrep SCM configs for GitLab Self-Managed",
    )
    add_glsm_url_arg(glsm_list_configs, required=False)
    glsm_list_configs.add_argument(
        "--unhealthy-only",
        action="store_true",
        help="Only show unhealthy SCM configs.",
    )
    glsm_list_configs.add_argument(
        "--required-scopes",
        type=parse_scopes,
        metavar="SCOPES",
        help="Comma-separated list of required token scopes for health check. "
             "If not specified, only connection status is checked. "
             f"Valid scopes: {', '.join(ScmTokenScopes.ALL_SCOPES)}",
    )
    glsm_list_configs.set_defaults(func=cmd_glsm_list_configs)

    glsm_create_configs = glsm_subparsers.add_parser(
        "create-configs",
        help="Create SCM configs for GitLab Self-Managed groups",
    )
    add_glsm_url_arg(glsm_create_configs)
    glsm_create_configs.add_argument(
        "--glsm-token",
        default=os.environ.get("GLSM_TOKEN"),
        metavar="TOKEN",
        help="GitLab personal access token. Can also be set via GLSM_TOKEN env var.",
    )
    glsm_groups_group = glsm_create_configs.add_mutually_exclusive_group(required=True)
    glsm_groups_group.add_argument(
        "--groups",
        nargs="+",
        metavar="GROUP",
        help="GitLab group names to create configs for.",
    )
    glsm_groups_group.add_argument(
        "--groups-file",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="File containing group names (one per line).",
    )
    glsm_create_configs.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making any changes.",
    )
    glsm_create_configs.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between creating each config (default: 1.0 seconds).",
    )
    glsm_create_configs.add_argument(
        "--subscribe",
        action="store_true",
        help="Subscribe to webhooks (default: disabled).",
    )
    glsm_create_configs.add_argument(
        "--auto-scan",
        action="store_true",
        help="Enable scanning of new repos in the org automatically (default: disabled).",
    )
    glsm_create_configs.add_argument(
        "--diff-enabled",
        action="store_true",
        help="Enable diff scanning (default: disabled).",
    )
    glsm_create_configs.set_defaults(func=cmd_glsm_create_configs)

    glsm_update_configs = glsm_subparsers.add_parser(
        "update-configs",
        help="Update SCM configs for GitLab Self-Managed groups",
    )
    add_glsm_url_arg(glsm_update_configs)
    glsm_update_configs.add_argument(
        "--glsm-token",
        nargs="?",
        const=os.environ.get("GLSM_TOKEN"),
        default=None,
        metavar="TOKEN",
        help="Update the access token. If no value is given, uses GLSM_TOKEN env var.",
    )
    glsm_update_groups = glsm_update_configs.add_mutually_exclusive_group()
    glsm_update_groups.add_argument(
        "--groups",
        nargs="+",
        metavar="GROUP",
        help="Specific group names to update (if not provided, updates all matching GLSM URL).",
    )
    glsm_update_groups.add_argument(
        "--groups-file",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="File containing group names to update (one per line).",
    )
    glsm_update_configs.add_argument(
        "--subscribe",
        type=parse_bool,
        metavar="BOOL",
        help="Set subscribe to webhooks (true/false).",
    )
    glsm_update_configs.add_argument(
        "--auto-scan",
        type=parse_bool,
        metavar="BOOL",
        help="Enable scanning of new repos in the org automatically (true/false).",
    )
    glsm_update_configs.add_argument(
        "--use-network-broker",
        type=parse_bool,
        metavar="BOOL",
        help="Set use network broker (true/false).",
    )
    glsm_update_configs.add_argument(
        "--diff-enabled",
        type=parse_bool,
        metavar="BOOL",
        help="Set diff scanning enabled (true/false).",
    )
    glsm_update_configs.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without making any changes.",
    )
    glsm_update_configs.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between updating each config (default: 1.0 seconds).",
    )
    glsm_update_configs.set_defaults(func=cmd_glsm_update_configs)

    glsm_delete_configs = glsm_subparsers.add_parser(
        "delete-configs",
        help="Delete SCM configs for GitLab Self-Managed groups",
    )
    add_glsm_url_arg(glsm_delete_configs)
    glsm_delete_configs.add_argument(
        "--groups",
        nargs="+",
        metavar="GROUP",
        required=True,
        help="Group names to delete (required to prevent accidental deletion).",
    )
    glsm_delete_configs.add_argument(
        "--unhealthy-only",
        action="store_true",
        help="Only delete configs that are unhealthy; skip healthy ones.",
    )
    glsm_delete_configs.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making any changes.",
    )
    glsm_delete_configs.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Delay between each request to check or delete a config (default: 0.5 seconds).",
    )
    glsm_delete_configs.set_defaults(func=cmd_glsm_delete_configs)

    args = parser.parse_args()
    args.func(args)
