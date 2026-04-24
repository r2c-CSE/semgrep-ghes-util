from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SemgrepApiError(Exception):
    """Exception raised for Semgrep API errors."""

    def __init__(self, message: str, status_code: int | None = None, response: requests.Response | None = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


def create_retry_session(
    retries: int = 5,
    backoff_factor: float = 0.5,
    status_forcelist: tuple[int, ...] = (500, 502, 503, 504),
) -> requests.Session:
    """Create a requests session with retry logic.

    Args:
        retries: Number of retries to attempt
        backoff_factor: Factor for exponential backoff (0.5 = 0.5s, 1s, 2s, 4s, 8s)
        status_forcelist: HTTP status codes to retry on
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST", "PATCH", "DELETE"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


class ScmType(Enum):
    """SCM provider types."""

    GITHUB = "SCM_TYPE_GITHUB"
    GITHUB_ENTERPRISE = "SCM_TYPE_GITHUB_ENTERPRISE"
    GITLAB = "SCM_TYPE_GITLAB"
    GITLAB_SELFMANAGED = "SCM_TYPE_GITLAB_SELFMANAGED"
    BITBUCKET = "SCM_TYPE_BITBUCKET"
    BITBUCKET_DATACENTER = "SCM_TYPE_BITBUCKET_DATACENTER"
    AZURE_DEVOPS = "SCM_TYPE_AZURE_DEVOPS"
    UNKNOWN = "SCM_TYPE_UNKNOWN"


class ProjectStatus(Enum):
    """Project status types."""

    UNINITIALIZED = "PROJECT_STATUS_UNINITIALIZED"
    ARCHIVED = "PROJECT_STATUS_ARCHIVED"
    ACTIVE = "PROJECT_STATUS_ACTIVE"


class GithubEntityType(Enum):
    """GitHub entity types."""

    ORG = "GITHUB_ENTITY_TYPE_ORG"
    USER = "GITHUB_ENTITY_TYPE_USER"


@dataclass
class Deployment:
    """Semgrep deployment info."""

    id: int
    name: str
    slug: str
    display_name: str | None = None


@dataclass
class ScmStatus:
    """SCM config status."""

    checked: datetime | None = None
    ok: bool = False
    error: str | None = None


@dataclass
class ScmTokenScopes:
    """Token permission scopes."""

    read_metadata: bool = False
    read_pull_request: bool = False
    write_pull_request_comment: bool = False
    read_contents: bool = False
    read_members: bool = False
    manage_webhooks: bool = False
    write_contents: bool = False

    # All available scope names for validation
    ALL_SCOPES = [
        "read_metadata",
        "read_pull_request",
        "write_pull_request_comment",
        "read_contents",
        "read_members",
        "manage_webhooks",
        "write_contents",
    ]

    def has_scopes(self, required: list[str]) -> bool:
        """Check if all specified scopes are present.

        Args:
            required: List of scope names to check (e.g., ["read_metadata", "read_contents"])

        Returns:
            True if all specified scopes are present
        """
        for scope in required:
            if not getattr(self, scope, False):
                return False
        return True

    def missing_scopes(self, required: list[str]) -> list[str]:
        """Get list of missing scopes from the required list.

        Args:
            required: List of scope names to check

        Returns:
            List of scope names that are missing
        """
        return [scope for scope in required if not getattr(self, scope, False)]

    @property
    def has_required_scopes(self) -> bool:
        """Check if all required scopes for full Semgrep functionality are present.

        This checks for scopes needed for webhooks, PR comments, and scanning.
        write_contents is optional.
        """
        return self.has_scopes([
            "read_metadata",
            "read_pull_request",
            "write_pull_request_comment",
            "read_contents",
            "read_members",
            "manage_webhooks",
        ])


@dataclass
class ScmCheckResult:
    """Result from checking an SCM config's health."""

    status: ScmStatus
    token_scopes: ScmTokenScopes | None = None


@dataclass
class ScmConfig:
    """SCM configuration."""

    id: str
    type: str
    namespace: str
    source_id: str | None = None
    base_url: str | None = None
    status: ScmStatus | None = None
    installed: bool = False
    suspended: bool = False
    github_entity_type: str | None = None
    auto_scan: bool = False
    use_network_broker: bool = False
    token_scopes: ScmTokenScopes | None = None
    last_successful_sync_at: datetime | None = None
    scm_id: str | None = None

    @property
    def is_healthy(self) -> bool:
        """Check if SCM config has a healthy connection (status.ok only).

        This is a basic health check that only verifies connectivity.
        Use meets_requirements() to also check for specific token scopes.
        """
        return self.status is not None and self.status.ok

    def meets_requirements(self, required_scopes: list[str] | None = None) -> bool:
        """Check if SCM config meets health and optional scope requirements.

        Args:
            required_scopes: Optional list of scope names to require.
                            If None, only checks basic health (status.ok).

        Returns:
            True if healthy and all required scopes are present (if specified)
        """
        if not self.is_healthy:
            return False

        if required_scopes and self.token_scopes:
            return self.token_scopes.has_scopes(required_scopes)

        return True


@dataclass
class Project:
    """Semgrep project info."""

    id: int
    name: str
    url: str | None = None
    create_time: datetime | None = None
    tags: list[str] | None = None
    latest_scan_id: int | None = None
    primary_branch_id: int | None = None
    default_branch_id: int | None = None


@dataclass
class Repo:
    """Semgrep repo info from search endpoint."""

    id: int
    name: str
    url: str | None = None
    is_archived: bool = False
    is_setup: bool = False
    is_disconnected: bool = False
    scm_type: str | None = None


class ScanType(Enum):
    """Scan type."""

    FULL = "SCAN_TYPE_FULL"
    DIFF = "SCAN_TYPE_DIFF"
    UNKNOWN = "SCAN_TYPE_UNKNOWN"


class ScanStatus(Enum):
    """Scan status."""

    RUNNING = "SCAN_STATUS_RUNNING"
    COMPLETED = "SCAN_STATUS_COMPLETED"
    FAILED = "SCAN_STATUS_FAILED"
    UNKNOWN = "SCAN_STATUS_UNKNOWN"


@dataclass
class Scan:
    """Semgrep scan info."""

    id: int
    status: str
    scan_type: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SemgrepClient:
    """Client for Semgrep API v2."""

    BASE_URL = "https://semgrep.dev/api"

    def __init__(self, token: str):
        self.token = token
        self.session = create_retry_session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
        })
        self._deployment: Deployment | None = None

    def _make_request(
        self,
        method: str,
        url: str,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict | None:
        """Make an HTTP request with appropriate headers.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            url: Full URL to request
            json: Optional JSON body (Content-Type will be set automatically)
            params: Optional query parameters

        Returns:
            Parsed JSON response, or None for empty responses (e.g., 204)
        """
        headers = {}
        if json is not None:
            headers["Content-Type"] = "application/json"

        response = self.session.request(
            method=method,
            url=url,
            json=json,
            params=params,
            headers=headers if headers else None,
        )

        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> dict | None:
        """Handle API response and raise appropriate errors.

        Returns:
            Parsed JSON response, or None for empty responses (e.g., 204)
        """
        if response.status_code >= 400:
            try:
                error_body = response.json()
                message = error_body.get("message", error_body.get("error", response.text))
            except Exception:
                message = response.text or f"HTTP {response.status_code}"

            raise SemgrepApiError(
                f"Semgrep API error: {message}",
                status_code=response.status_code,
                response=response,
            )

        # Handle empty responses (e.g., 204 No Content)
        if response.status_code == 204 or not response.content:
            return None

        return response.json()

    @property
    def deployment(self) -> Deployment:
        """Get deployment, fetching from API if not cached."""
        if self._deployment is None:
            self._deployment = self.get_deployment()
        return self._deployment

    def get_deployment(self) -> Deployment:
        """Get deployment info for the current token.

        GET /api/agent/deployment
        """
        data = self._make_request("GET", f"{self.BASE_URL}/agent/deployment")
        data = data["deployment"]
        return Deployment(
            id=data["id"],
            name=data["name"],
            slug=data["slug"],
            display_name=data.get("display_name"),
        )

    def _parse_scm_config(self, data: dict) -> ScmConfig:
        """Parse SCM config from API response."""
        status = None
        if "status" in data:
            status_data = data["status"]
            checked = None
            if status_data.get("checked"):
                checked = datetime.fromisoformat(
                    status_data["checked"].replace("Z", "+00:00")
                )
            status = ScmStatus(
                checked=checked,
                ok=status_data.get("ok", False),
                error=status_data.get("error"),
            )

        token_scopes = None
        if "tokenScopes" in data:
            scopes = data["tokenScopes"]
            token_scopes = ScmTokenScopes(
                read_metadata=scopes.get("readMetadata", False),
                read_pull_request=scopes.get("readPullRequest", False),
                write_pull_request_comment=scopes.get("writePullRequestComment", False),
                read_contents=scopes.get("readContents", False),
                read_members=scopes.get("readMembers", False),
                manage_webhooks=scopes.get("manageWebhooks", False),
                write_contents=scopes.get("writeContents", False),
            )

        last_sync = None
        if data.get("lastSuccessfulSyncAt"):
            last_sync = datetime.fromisoformat(
                data["lastSuccessfulSyncAt"].replace("Z", "+00:00")
            )

        return ScmConfig(
            id=data["id"],
            type=data["type"],
            namespace=data["namespace"],
            source_id=data.get("sourceId"),
            base_url=data.get("baseUrl"),
            status=status,
            installed=data.get("installed", False),
            suspended=data.get("suspended", False),
            github_entity_type=data.get("githubEntityType"),
            auto_scan=data.get("autoScan", False),
            use_network_broker=data.get("useNetworkBroker", False),
            token_scopes=token_scopes,
            last_successful_sync_at=last_sync,
            scm_id=data.get("scmId"),
        )

    def list_scm_configs(self) -> list[ScmConfig]:
        """List all SCM configs for the deployment.

        GET /api/scm/deployments/{deploymentId}/configs
        """
        configs: list[ScmConfig] = []
        cursor: str | None = None

        while True:
            params = {"cursor": cursor} if cursor else None
            data = self._make_request(
                "GET",
                f"{self.BASE_URL}/scm/deployments/{self.deployment.id}/configs",
                params=params,
            )

            for config in data.get("configs", []):
                configs.append(self._parse_scm_config(config))

            cursor = data.get("cursor")
            if not cursor:
                break

        return configs

    def create_scm_config(
        self,
        scm_type: ScmType,
        namespace: str,
        base_url: str,
        access_token: str | None = None,
        source_id: str | None = None,
        scm_config_id: int | None = None,
        subscribe: bool = False,
        auto_scan: bool = False,
        diff_enabled: bool = False,
    ) -> ScmConfig:
        """Create a new SCM config.

        POST /api/scm/deployments/{deploymentId}/configs

        Args:
            scm_type: Type of SCM (e.g., GITHUB_ENTERPRISE)
            namespace: Organization/group name
            base_url: Base URL of the SCM instance (e.g., https://github.example.com)
            access_token: Access token for the SCM (optional if reusing token via scm_config_id)
            source_id: Optional source identifier
            scm_config_id: Optional ID of existing config to reuse token from
            subscribe: Whether to auto-subscribe to webhooks
            auto_scan: Whether to enable auto-scanning
            diff_enabled: Whether to enable diff scanning (within auto_scan_settings)
        """
        body: dict = {
            "type": scm_type.value,
            "namespace": namespace,
            "baseUrl": base_url,
            "subscribe": subscribe,
            "autoScan": auto_scan,
            "autoScanSettings": {
                "diffEnabled": diff_enabled,
            },
        }

        if access_token:
            body["accessToken"] = access_token
        if source_id:
            body["sourceId"] = source_id
        if scm_config_id:
            body["scmConfigId"] = scm_config_id

        data = self._make_request(
            "POST",
            f"{self.BASE_URL}/scm/deployments/{self.deployment.id}/configs",
            json=body,
        )
        return self._parse_scm_config(data["config"])

    def patch_scm_config(
        self,
        config_id: str,
        access_token: str | None = None,
        subscribe: bool | None = None,
        auto_scan: bool | None = None,
        use_network_broker: bool | None = None,
        diff_enabled: bool | None = None,
    ) -> ScmConfig:
        """Update an existing SCM config.

        PATCH /api/scm/deployments/{deploymentId}/configs/{configId}

        Args:
            config_id: The config ID to update
            access_token: New access token for the SCM
            subscribe: Whether to auto-subscribe to webhooks
            auto_scan: Whether to enable auto-scanning
            use_network_broker: Whether to use network broker
            diff_enabled: Whether to enable diff scanning (within auto_scan_settings)

        Only fields that are not None will be included in the update.
        """
        body: dict = {}

        if access_token is not None:
            body["accessToken"] = access_token
        if subscribe is not None:
            body["subscribe"] = subscribe
        if auto_scan is not None:
            body["autoScan"] = auto_scan
        if use_network_broker is not None:
            body["useNetworkBroker"] = use_network_broker
        if diff_enabled is not None:
            body["autoScanSettings"] = {"diffEnabled": diff_enabled}

        data = self._make_request(
            "PATCH",
            f"{self.BASE_URL}/scm/deployments/{self.deployment.id}/configs/{config_id}",
            json=body,
        )
        return self._parse_scm_config(data["config"])

    def delete_scm_config(self, config_id: str) -> None:
        """Delete an SCM config.

        DELETE /api/scm/deployments/{deploymentId}/configs/{configId}

        Args:
            config_id: The config ID to delete
        """
        self._make_request(
            "DELETE",
            f"{self.BASE_URL}/scm/deployments/{self.deployment.id}/configs/{config_id}",
        )

    def check_scm_config(self, config_id: str) -> ScmCheckResult:
        """Check the health of an SCM config.

        GET /api/scm/deployments/{deploymentId}/configs/{configId}/check

        Args:
            config_id: The config ID to check

        Returns:
            ScmCheckResult with status and token scopes
        """
        data = self._make_request(
            "GET",
            f"{self.BASE_URL}/scm/deployments/{self.deployment.id}/configs/{config_id}/check",
        )

        status_data = data.get("status", {})
        checked = None
        if status_data.get("checked"):
            checked = datetime.fromisoformat(
                status_data["checked"].replace("Z", "+00:00")
            )
        status = ScmStatus(
            checked=checked,
            ok=status_data.get("ok", False),
            error=status_data.get("error"),
        )

        token_scopes = None
        if "tokenScopes" in data:
            scopes = data["tokenScopes"]
            token_scopes = ScmTokenScopes(
                read_metadata=scopes.get("readMetadata", False),
                read_pull_request=scopes.get("readPullRequest", False),
                write_pull_request_comment=scopes.get("writePullRequestComment", False),
                read_contents=scopes.get("readContents", False),
                read_members=scopes.get("readMembers", False),
                manage_webhooks=scopes.get("manageWebhooks", False),
                write_contents=scopes.get("writeContents", False),
            )

        return ScmCheckResult(status=status, token_scopes=token_scopes)

    def _parse_project(self, data: dict) -> Project:
        """Parse project from API response."""
        create_time = None
        if data.get("createTime"):
            create_time = datetime.fromisoformat(
                data["createTime"].replace("Z", "+00:00")
            )

        return Project(
            id=data["id"],
            name=data["name"],
            url=data.get("url"),
            create_time=create_time,
            tags=data.get("tags"),
            latest_scan_id=data.get("latestScanId"),
            primary_branch_id=data.get("primaryBranchId"),
            default_branch_id=data.get("defaultBranchId"),
        )

    def list_projects(
        self,
        statuses: list[ProjectStatus] | None = None,
        names: list[str] | None = None,
        page_size: int = 100,
    ) -> list[Project]:
        """List projects for the deployment with optional filters.

        POST /api/v2/deployments/{id}/projects/list

        Args:
            statuses: Filter by project statuses
            names: Filter by project names
            page_size: Number of results per page
        """
        projects: list[Project] = []
        page_token = ""

        while True:
            body: dict = {
                "pageSize": page_size,
                "pageToken": page_token,
            }

            filter_params: dict = {}
            if statuses:
                filter_params["statuses"] = [s.value for s in statuses]
            if names:
                filter_params["names"] = names

            if filter_params:
                body["filter"] = filter_params

            data = self._make_request(
                "POST",
                f"{self.BASE_URL}/v2/deployments/{self.deployment.id}/projects/list",
                json=body,
            )

            for project in data.get("projects", []):
                projects.append(self._parse_project(project))

            page_token = data.get("pageToken", "")
            if not page_token:
                break

        return projects

    def bulk_update_repos(
        self,
        repo_ids: list[int],
        enable_diff_scan: bool | None = None,
        enable_full_scan: bool | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Bulk update repos with managed scan settings.

        PATCH /api/agent/deployments/{id}/repos

        Args:
            repo_ids: List of repo IDs to update
            enable_diff_scan: Enable diff scanning (PR comments)
            enable_full_scan: Enable full scanning
            tags: Tags to set on repos

        Returns:
            List of updated repo names
        """
        changes = []
        for repo_id in repo_ids:
            change: dict = {}

            if enable_diff_scan is not None or enable_full_scan is not None:
                managed_scans: dict = {}
                if enable_diff_scan is not None:
                    managed_scans["diffScan"] = enable_diff_scan
                if enable_full_scan is not None:
                    managed_scans["fullScan"] = enable_full_scan
                change["managedScans"] = managed_scans

            if tags is not None:
                change["updateTags"] = True
                change["tags"] = tags

            changes.append({"repoId": repo_id, "change": change})

        payload = {
            "deploymentId": self.deployment.id,
            "changes": changes,
        }

        data = self._make_request(
            "PATCH",
            f"{self.BASE_URL}/agent/deployments/{self.deployment.id}/repos",
            json=payload,
        )
        return data.get("updatedRepoNames", [])

    def _parse_repo(self, data: dict) -> Repo:
        """Parse repo from API response."""
        return Repo(
            id=data["id"],
            name=data["name"],
            url=data.get("url"),
            is_archived=data.get("isArchived", False),
            is_setup=data.get("isSetup", False),
            is_disconnected=data.get("isDisconnected", False),
            scm_type=data.get("scmType"),
        )

    def search_repos(
        self,
        setup: bool | None = None,
        page_size: int = 100,
    ) -> list[Repo]:
        """Search repos for the deployment with optional filters.

        POST /api/agent/deployments/{id}/repos/search

        Args:
            setup: Filter by setup status (True=initialized, False=uninitialized)
            page_size: Number of results per page
        """
        repos: list[Repo] = []
        cursor: str | None = None

        while True:
            body: dict = {
                "deploymentId": self.deployment.id,
                "pageSize": page_size,
            }

            if cursor:
                body["cursor"] = cursor

            filters: dict = {}
            if setup is not None:
                filters["setup"] = setup

            if filters:
                body["filters"] = filters

            data = self._make_request(
                "POST",
                f"{self.BASE_URL}/agent/deployments/{self.deployment.id}/repos/search",
                json=body,
            )

            for repo in data.get("repos", []):
                repos.append(self._parse_repo(repo))

            cursor = data.get("cursor")
            if not cursor:
                break

        return repos

    def _parse_scan(self, data: dict) -> Scan:
        """Parse scan from API response."""
        started_at = None
        if data.get("startedAt"):
            started_at = datetime.fromisoformat(
                data["startedAt"].replace("Z", "+00:00")
            )

        completed_at = None
        if data.get("completedAt"):
            completed_at = datetime.fromisoformat(
                data["completedAt"].replace("Z", "+00:00")
            )

        return Scan(
            id=data["id"],
            status=data.get("status", "SCAN_STATUS_UNKNOWN"),
            scan_type=data.get("type", "SCAN_TYPE_UNKNOWN"),
            started_at=started_at,
            completed_at=completed_at,
        )

    def list_project_scans(
        self,
        project_id: int,
        scan_types: list[ScanType] | None = None,
        statuses: list[ScanStatus] | None = None,
        limit: int = 100,
    ) -> list[Scan]:
        """List scans for a project.

        POST /api/v2/deployments/{deploymentId}/projects/{projectId}/scans/list

        Args:
            project_id: The project ID to list scans for
            scan_types: Filter by scan types (e.g., FULL, DIFF)
            statuses: Filter by scan statuses
            limit: Max results per page
        """
        scans: list[Scan] = []
        cursor: str | None = None

        while True:
            body: dict = {
                "limit": limit,
            }

            if cursor:
                body["cursor"] = cursor

            filters: dict = {}
            if scan_types:
                filters["types"] = [t.value for t in scan_types]
            if statuses:
                filters["statuses"] = [s.value for s in statuses]

            if filters:
                body["filters"] = filters

            data = self._make_request(
                "POST",
                f"{self.BASE_URL}/v2/deployments/{self.deployment.id}/projects/{project_id}/scans/list",
                json=body,
            )

            for scan in data.get("scans", []):
                scans.append(self._parse_scan(scan))

            cursor = data.get("cursor")
            if not cursor:
                break

        return scans

    def has_full_scan(self, project_id: int) -> bool:
        """Check if a project has any completed full scans.

        Args:
            project_id: The project ID to check

        Returns:
            True if the project has at least one completed full scan
        """
        scans = self.list_project_scans(
            project_id=project_id,
            scan_types=[ScanType.FULL],
            statuses=[ScanStatus.COMPLETED],
            limit=1,
        )
        return len(scans) > 0

    def trigger_scans(self, repo_ids: list[int]) -> dict:
        """Trigger scans for repos.

        POST /api/agent/deployments/{deploymentId}/scans/run

        Args:
            repo_ids: List of repo IDs to trigger scans for

        Returns:
            API response dict
        """
        body = {
            "runs": [{"repo_id": repo_id} for repo_id in repo_ids],
            "type": "autoscan",
        }

        return self._make_request(
            "POST",
            f"{self.BASE_URL}/agent/deployments/{self.deployment.id}/scans/run",
            json=body,
        )
