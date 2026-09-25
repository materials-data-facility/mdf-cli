from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, urlparse

import httpx

from mdf.core import config as core_config


def _as_param_list(value: Any) -> List[str]:
    """Normalize a filter option into a list of non-empty query-param values.

    Accepts ``None``, a scalar (``"2024"``, ``2024``, ``2024.0``, ``True``), or
    any iterable of those, so callers can pass a Typer repeatable option, a
    single string, or a bare number from the Python SDK interchangeably —
    ``client.search("perovskite", year=2024)`` must not raise.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes, int, float, bool)):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            # Anything else that is not iterable is still one value.
            values = [value]
    return [str(item) for item in values if item is not None and str(item) != ""]

_V2_API_URLS = {
    "prod": "https://api.materialsdatafacility.org",
    "dev": "https://api-dev.materialsdatafacility.org",
    "staging": "https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging",
    "local": "http://127.0.0.1:8080",
}


class ApiResult(dict):
    """A response body that also remembers the HTTP status it arrived with.

    ``BackendClient._request`` historically returned the decoded JSON body and
    threw the status code away, which forced callers to guess at failure by
    substring-matching error text. ``ApiResult`` is a plain ``dict`` subclass, so
    every existing call site (``result["files"]``, ``result.get("success")``,
    ``json.dumps(result)``) keeps working unchanged and the status never leaks
    into ``--json`` output — but ``result.status_code`` is available to code that
    wants to classify outcomes properly.

    ``status_code`` is ``None`` for results synthesized client-side (connection
    failures, retry exhaustion), i.e. when no HTTP response was ever received.
    """

    __slots__ = ("status_code",)

    def __init__(self, data: Optional[Dict[str, Any]] = None, status_code: Optional[int] = None):
        super().__init__(data or {})
        self.status_code = status_code


def _api_url_for_service(service_instance: Optional[str] = None) -> str:
    override_url = os.environ.get("MDF_API_URL")
    if override_url:
        return override_url
    normalized = core_config.resolve_service(service_instance).strip().lower()
    if normalized == "production":
        normalized = "prod"
    elif normalized == "development":
        normalized = "dev"
    url = _V2_API_URLS.get(normalized, "")
    if not url:
        raise ValueError(
            f"No API URL configured for service '{normalized}'. "
            f"Use --api-url or set MDF_API_URL environment variable."
        )
    return url


#: Hosts that are always loopback: a local dev backend may receive a cached login.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
#: Env var that lets a cached Globus login be sent to an --api-url / MDF_API_URL
#: host outside the known service map.
TRUST_API_URL_ENV = "MDF_TRUST_API_URL"
_TRUTHY = {"1", "true", "yes", "on", "y"}


def is_trusted_api_url(url: str) -> bool:
    """True when ``url`` may receive the user's cached Globus login tokens.

    That is one of the known MDF services (same scheme, host and port as the
    service map) or a loopback host (local development), or any URL when
    ``MDF_TRUST_API_URL`` is set. A cached login carries the data, transfer and
    groups tokens, so it must never follow an arbitrary ``--api-url``.
    """
    if (os.environ.get(TRUST_API_URL_ENV) or "").strip().lower() in _TRUTHY:
        return True
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    origin = (parsed.scheme.lower(), host, parsed.port)
    for known in _V2_API_URLS.values():
        k = urlparse(known)
        if (k.scheme, (k.hostname or "").lower(), k.port) == origin:
            return True
    return False


#: credential_source() value -> how `mdf whoami` describes it.
CREDENTIAL_LABELS = {
    "token": "explicit token (--token)",
    "env_token": "environment token (MDF_CONNECT_TOKEN)",
    "client_credentials": "confidential client (MDF_CLIENT_ID)",
    "dev_user": "dev user (X-User-Id)",
    "local": "local dev backend (no auth)",
    "cached_login": "cached Globus login",
    "saved_token": "saved token (mdf login --token --save)",
}


def credential_source(
    url: str,
    *,
    token: Optional[str] = None,
    service_instance: Optional[str] = None,
    dev_user_id: Optional[str] = None,
) -> Optional[str]:
    """Which credential ``BackendClient.authenticated`` would use for ``url`` (no network).

    The single definition of the resolution order, so what `mdf whoami`
    reports is what requests actually carry. ``None`` means no credential: an
    interactive login would be needed (or is refused, for an untrusted URL).
    """
    resolved_service = core_config.resolve_service(service_instance).strip().lower()
    if token:
        return "token"
    if os.environ.get("MDF_CONNECT_TOKEN"):
        return "env_token"
    if os.environ.get("MDF_CLIENT_ID") and os.environ.get("MDF_CLIENT_SECRET"):
        return "client_credentials"
    if dev_user_id or os.environ.get("MDF_DEV_USER_ID") or (
        resolved_service == "local" and os.environ.get("LOCAL_USER_ID")
    ):
        return "dev_user"
    if resolved_service == "local":
        return "local"
    from mdf.auth.globus import is_logged_in

    if is_trusted_api_url(url) and is_logged_in(service_instance=resolved_service):
        return "cached_login"
    if core_config.load_saved_token(url):
        return "saved_token"
    return None


class BackendClient:
    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        user_id: Optional[str] = None,
        globus_data_token: Optional[str] = None,
        globus_transfer_token: Optional[str] = None,
        mdf_connect_token: Optional[str] = None,
        groups_token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)
        self._token = token
        self._user_id = user_id
        self._globus_data_token = globus_data_token
        self._mdf_connect_token = mdf_connect_token
        self._globus_transfer_token = globus_transfer_token
        self._groups_token = groups_token

    @classmethod
    def from_env(cls) -> "BackendClient":
        base_url = os.environ.get("MDF_API_URL") or _api_url_for_service()
        token = os.environ.get("MDF_CONNECT_TOKEN")
        user_id = os.environ.get("MDF_DEV_USER_ID")
        return cls(base_url=base_url, token=token, user_id=user_id)

    @classmethod
    def authenticated(
        cls,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        service_instance: Optional[str] = None,
        dev_user_id: Optional[str] = None,
        interactive: Optional[bool] = None,
    ) -> "BackendClient":
        """Create an authenticated backend client.

        Resolution order:
        1. Explicit token argument
        2. MDF_CONNECT_TOKEN env var
        3. MDF_CLIENT_ID + MDF_CLIENT_SECRET env vars (confidential client flow)
        4. dev_user_id / MDF_DEV_USER_ID env var (X-User-Id)
        5. A usable cached Globus login — only for a trusted API URL
           (:func:`is_trusted_api_url`)
        6. A token saved by ``mdf login --token T --save`` for this same API URL
        7. An interactive Globus OAuth2 login — also only for a trusted URL

        Steps 1-6 follow :func:`credential_source`. For an untrusted
        ``--api-url``/``MDF_API_URL`` host with no explicit credential this
        raises :class:`mdf.core.exceptions.AuthRequired` rather than send the
        user's Globus tokens there.

        ``interactive`` controls step 7 when no cached login exists: ``True``
        prompts (on stderr), ``False`` raises
        :class:`mdf.core.exceptions.AuthRequired`, and ``None`` (default)
        follows :func:`mdf.auth.globus.interactive_login`, which prompts unless
        a caller such as the CLI narrowed it.
        """
        resolved_service = core_config.resolve_service(service_instance)
        url = base_url or _api_url_for_service(resolved_service)
        normalized_service = resolved_service.strip().lower()

        resolved_token = token or os.environ.get("MDF_CONNECT_TOKEN")
        if resolved_token:
            return cls(base_url=url, token=resolved_token)

        confidential_client_id = os.environ.get("MDF_CLIENT_ID")
        confidential_client_secret = os.environ.get("MDF_CLIENT_SECRET")
        if confidential_client_id and confidential_client_secret:
            # Lazy import so token/dev-user workflows do not require globus_sdk.
            import globus_sdk

            from mdf.auth.globus import (
                DATA_MDF_SCOPE,
                NCSA_MDF_COLLECTION_UUID,
                TRANSFER_SCOPE,
                get_scopes_for_service,
            )

            scope, resource_server = get_scopes_for_service(resolved_service)
            confidential_client = globus_sdk.ConfidentialAppAuthClient(
                confidential_client_id,
                confidential_client_secret,
            )
            token_response = confidential_client.oauth2_client_credentials_tokens(
                requested_scopes=f"{scope} {DATA_MDF_SCOPE} {TRANSFER_SCOPE}",
            )
            by_resource_server = getattr(token_response, "by_resource_server", {}) or {}

            def _extract_access_token(token_entry: Any) -> str:
                if not token_entry:
                    return ""
                if isinstance(token_entry, dict):
                    return token_entry.get("access_token", "")
                return getattr(token_entry, "access_token", "")

            service_token = _extract_access_token(by_resource_server.get(resource_server))
            data_token = _extract_access_token(by_resource_server.get(NCSA_MDF_COLLECTION_UUID))
            transfer_token = _extract_access_token(by_resource_server.get("transfer.api.globus.org"))
            if service_token:
                return cls(base_url=url, token=service_token, globus_data_token=data_token or None, globus_transfer_token=transfer_token or None)

        resolved_user_id = dev_user_id or os.environ.get("MDF_DEV_USER_ID")
        if not resolved_user_id and normalized_service == "local":
            resolved_user_id = os.environ.get("LOCAL_USER_ID")
        if resolved_user_id:
            return cls(base_url=url, user_id=resolved_user_id)

        if normalized_service == "local":
            # Local dev backends commonly run in AUTH_MODE=dev and can derive a default user.
            return cls(base_url=url)

        # A saved token is only for the exact API URL it was validated against;
        # a usable cached Globus login (which also carries the data, transfer
        # and groups tokens) takes precedence over it on trusted URLs.
        source = credential_source(url, service_instance=resolved_service)
        if source == "saved_token":
            return cls(base_url=url, token=core_config.load_saved_token(url))
        if not is_trusted_api_url(url):
            from mdf.core.exceptions import AuthRequired

            raise AuthRequired(
                f"Not sending your Globus login to {url}: it is not a known MDF service",
                hint=(
                    f"pass --token for this API, save one with `mdf login --token T --save --api-url {url}`, "
                    f"or set {TRUST_API_URL_ENV}=1 if you trust this host"
                ),
            )

        # Lazy import so token/dev-user workflows do not require globus_sdk.
        from mdf.auth.globus import get_authorizer_for_scopes, get_scopes_for_service, DATA_MDF_SCOPE, TRANSFER_SCOPE, GROUPS_SCOPE, NCSA_MDF_COLLECTION_UUID

        scope, _resource_server = get_scopes_for_service(resolved_service)
        authorizers = get_authorizer_for_scopes(
            [scope, DATA_MDF_SCOPE, TRANSFER_SCOPE, GROUPS_SCOPE],
            interactive=interactive,
        )

        bearer_prefix = "Bearer "

        def _extract(authorizer):
            if not authorizer:
                return ""
            h = authorizer.get_authorization_header()
            return h[len(bearer_prefix):] if h.startswith(bearer_prefix) else h

        # openid token for userinfo() identity check
        openid_token = _extract(authorizers.get("auth.globus.org"))
        # MDF Connect token for dependent token exchange (groups, transfer)
        mdf_connect_token = _extract(authorizers.get(_resource_server))

        # Data token for Globus HTTPS file operations (X-Globus-Token header)
        # The resource server key is the collection UUID, not the hostname.
        data_token = _extract(authorizers.get(NCSA_MDF_COLLECTION_UUID))

        # Transfer token for mkdir operations before HTTPS uploads
        transfer_token = _extract(authorizers.get("transfer.api.globus.org"))

        # Groups token for direct group membership checks
        groups_token = _extract(authorizers.get("groups.api.globus.org"))

        return cls(base_url=url, token=openid_token, globus_data_token=data_token or None, globus_transfer_token=transfer_token or None, mdf_connect_token=mdf_connect_token or None, groups_token=groups_token or None)

    def close(self) -> None:
        self._client.close()

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def auth_check(self) -> Dict[str, Any]:
        return self._request("GET", "/auth/check")

    def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/submit", json_data=payload)

    def status(self, source_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        params = {"version": version} if version else None
        return self._request("GET", f"/status/{source_id}", params=params)

    def submissions(
        self,
        organization: Optional[str] = None,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        start_key: Optional[str] = None,
        include_counts: bool = False,
    ) -> Dict[str, Any]:
        """List the caller's submissions.

        Filtering and paging happen server-side (``GET /submissions`` — see
        connect_server ``aws/v2/FRONTEND_API.md``): ``status`` is a
        comma-separated filter, ``limit`` is the page size, and ``start_key``
        is the cursor echoed back as ``next_key`` on the previous page.
        ``include_counts`` adds per-status ``counts`` plus an overall ``total``.
        """
        params: Dict[str, Any] = {}
        if organization:
            params["organization"] = organization
        if status:
            params["status"] = status
        if limit is not None:
            params["limit"] = int(limit)
        if start_key:
            params["start_key"] = start_key
        if include_counts:
            params["include_counts"] = "true"
        return self._request("GET", "/submissions", params=params or None)

    def curation_pending(
        self,
        limit: int = 50,
        offset: int = 0,
        organization: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if organization:
            params["organization"] = organization
        return self._request("GET", "/curation/pending", params=params)

    def curation_detail(self, source_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        params = {"version": version} if version else None
        return self._request("GET", f"/curation/{source_id}", params=params)

    def curation_approve(
        self,
        source_id: str,
        mint_doi: bool = True,
        notes: Optional[str] = None,
        metadata_updates: Optional[Dict[str, Any]] = None,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"mint_doi": mint_doi}
        if notes is not None:
            payload["notes"] = notes
        if metadata_updates is not None:
            payload["metadata_updates"] = metadata_updates
        if version:
            payload["version"] = version
        return self._request("POST", f"/curation/{source_id}/approve", json_data=payload)

    def curation_reject(
        self,
        source_id: str,
        reason: str,
        suggestions: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"reason": reason}
        if suggestions is not None:
            payload["suggestions"] = suggestions
        if version:
            payload["version"] = version
        return self._request("POST", f"/curation/{source_id}/reject", json_data=payload)

    def update_status(self, source_id: str, version: str, status: str) -> Dict[str, Any]:
        payload = {"source_id": source_id, "version": version, "status": status}
        return self._request("POST", "/status/update", json_data=payload)

    def versions(self, source_id: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get version history for a dataset."""
        params: Dict[str, Any] = {}
        if limit != 50:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return self._request("GET", f"/versions/{source_id}", params=params or None)

    def edit_metadata(
        self,
        source_id: str,
        version: Optional[str] = None,
        **fields,
    ) -> Dict[str, Any]:
        """Edit metadata on a submission."""
        payload: Dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
        if version:
            payload["version"] = version
        return self._request("POST", f"/submissions/{source_id}/metadata", json_data=payload)

    def withdraw(
        self,
        source_id: str,
        reason: str = "",
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Withdraw a pending_curation submission."""
        payload: Dict[str, Any] = {"reason": reason}
        if version:
            payload["version"] = version
        return self._request("POST", f"/submissions/{source_id}/withdraw", json_data=payload)

    def resubmit(
        self,
        source_id: str,
        notes: str = "",
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resubmit a rejected submission."""
        payload: Dict[str, Any] = {"notes": notes}
        if version:
            payload["version"] = version
        return self._request("POST", f"/submissions/{source_id}/resubmit", json_data=payload)

    def version_diff(
        self,
        source_id: str,
        from_version: str,
        to_version: str,
    ) -> Dict[str, Any]:
        """Get a structured diff between two versions."""
        params = {"from": from_version, "to": to_version}
        return self._request("GET", f"/versions/{source_id}/diff", params=params)

    def delete_submission(
        self,
        source_id: str,
        reason: str,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Soft-delete a submission (curator-only)."""
        payload: Dict[str, Any] = {"reason": reason}
        if version:
            payload["version"] = version
        return self._request("POST", f"/submissions/{source_id}/delete", json_data=payload)

    def admin_stats(self) -> Dict[str, Any]:
        """Get admin statistics (curator-only)."""
        return self._request("GET", "/admin/stats")

    def dataset_stats(self, source_id: str) -> Dict[str, Any]:
        """Get access/download stats for a published dataset."""
        return self._request("GET", f"/stats/{source_id}")

    def get_card(self, source_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Get a dataset preview card."""
        params = {"version": version} if version else None
        return self._request("GET", f"/card/{source_id}", params=params)

    def get_citation(
        self,
        source_id: str,
        format: str = "all",
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get citation for a dataset.

        Args:
            source_id: Dataset identifier
            format: Citation format (bibtex, ris, apa, datacite, all)
            version: Optional version
        """
        params = {"format": format}
        if version:
            params["version"] = version
        return self._request("GET", f"/citation/{source_id}", params=params)

    #: Facet filters accepted by ``GET /search``. Values must be whole facet
    #: values as returned in the response's ``facets`` (the backend indexes
    #: them as exact keywords, so a fragment matches nothing). Repeat a param
    #: to OR several values together.
    SEARCH_FILTERS = ("year", "organization", "author", "keyword", "domain")

    #: Accepted ``sort`` values. Without a query, the backend always sorts by
    #: ``newest`` — relevance has nothing to rank.
    SEARCH_SORTS = ("relevance", "newest", "most_viewed")

    def search(
        self,
        query: Optional[str] = None,
        search_type: str = "all",
        limit: int = 20,
        *,
        offset: int = 0,
        sort: Optional[str] = None,
        year: Optional[Sequence[str]] = None,
        organization: Optional[Sequence[str]] = None,
        author: Optional[Sequence[str]] = None,
        keyword: Optional[Sequence[str]] = None,
        domain: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Search datasets and streams, with facet filters and paging.

        Args:
            query: Search query string. ``None``/empty is a *browse* request —
                the newest published datasets, same response shape.
            search_type: "all", "datasets", or "streams"
            limit: Page size
            offset: Pagination offset
            sort: One of :data:`SEARCH_SORTS`
            year, organization, author, keyword, domain: Facet filters; each
                takes a string or a sequence of strings (sent as repeated
                query params, which the backend ORs together).
        """
        params: Dict[str, Any] = {"type": search_type, "limit": str(limit)}
        if query:
            params["q"] = query
        if offset:
            params["offset"] = str(offset)
        if sort:
            params["sort"] = sort
        for name, value in (
            ("year", year),
            ("organization", organization),
            ("author", author),
            ("keyword", keyword),
            ("domain", domain),
        ):
            values = _as_param_list(value)
            if values:
                # httpx encodes a list as repeated params: ?author=A&author=B
                params[name] = values if len(values) > 1 else values[0]
        return self._request("GET", "/search", params=params)

    def search_semantic(self, query: str, limit: int = 20) -> Dict[str, Any]:
        """Top-k semantic search over the embedding snapshot."""
        params = {"q": query, "limit": str(limit)}
        return self._request("GET", "/search/semantic", params=params)

    def related_by_author(self, source_id: str, limit: int = 20) -> Dict[str, Any]:
        """Datasets that share one or more authors with `source_id`."""
        params = {"by": "author", "limit": str(limit)}
        return self._request("GET", f"/datasets/{source_id}/related", params=params)

    def similar_by_embedding(self, source_id: str, limit: int = 10) -> Dict[str, Any]:
        """Datasets nearest to `source_id` by embedding cosine similarity."""
        params = {"by": "similar", "limit": str(limit)}
        return self._request("GET", f"/datasets/{source_id}/related", params=params)

    def embedding_status(self) -> Dict[str, Any]:
        """Curator-only: coverage + snapshot info for dataset embeddings."""
        return self._request("GET", "/admin/embeddings/status")

    def rebuild_embeddings(
        self,
        force: bool = False,
        build_snapshot: bool = True,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Curator-only: generate missing embeddings and rebuild the S3 snapshot."""
        payload: Dict[str, Any] = {"force": force, "build_snapshot": build_snapshot}
        if limit is not None:
            payload["limit"] = limit
        return self._request("POST", "/admin/embeddings/rebuild", json_data=payload)

    def rebuild_embedding_snapshot(self) -> Dict[str, Any]:
        """Curator-only: rebuild just the S3 snapshot from existing embeddings."""
        return self._request("POST", "/admin/embeddings/snapshot", json_data={})

    def dataset_preview(self, source_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/preview/{source_id}")

    def dataset_files(self, source_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/preview/{source_id}/files")

    def dataset_file_detail(self, source_id: str, path: str) -> Dict[str, Any]:
        encoded_path = quote(path, safe="/")
        return self._request("GET", f"/preview/{source_id}/files/{encoded_path}")

    def dataset_sample(self, source_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/preview/{source_id}/sample")

    _RETRY_STATUSES = {429, 502, 503, 504}
    _MAX_RETRIES = 3

    def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        import time

        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif self._user_id:
            headers["X-User-Id"] = self._user_id
        if self._globus_data_token:
            headers["X-Globus-Token"] = self._globus_data_token
        if self._mdf_connect_token:
            headers["X-MDF-Token"] = self._mdf_connect_token
        if self._groups_token:
            headers["X-Groups-Token"] = self._groups_token

        last_exc: Optional[Exception] = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                response = self._client.request(method, url, json=json_data, params=params, headers=headers)
            except httpx.ConnectError as exc:
                last_exc = exc
                if self.base_url == _V2_API_URLS["prod"]:
                    return ApiResult({
                        "success": False,
                        "error": (
                            f"Cannot connect to the production MDF API at {self.base_url}. "
                            "The production service is not available yet; set "
                            "MDF_SERVICE=staging and retry."
                        ),
                    })
                if attempt < self._MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                return ApiResult({"success": False, "error": f"Connection failed after {self._MAX_RETRIES + 1} attempts: {exc}"})
            except httpx.ConnectTimeout as exc:
                # Same fast-fail as ConnectError: prod has no API yet, so
                # retrying a connect timeout only makes the user wait
                # 1 + 2 + 4 seconds for the same certain failure.
                last_exc = exc
                if self.base_url == _V2_API_URLS["prod"]:
                    return ApiResult({
                        "success": False,
                        "error": (
                            f"Cannot connect to the production MDF API at {self.base_url}. "
                            "The production service is not available yet; set "
                            "MDF_SERVICE=staging and retry."
                        ),
                    })
                if attempt < self._MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                return ApiResult({"success": False, "error": f"Connection failed after {self._MAX_RETRIES + 1} attempts: {exc}"})

            if response.status_code in self._RETRY_STATUSES and attempt < self._MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = 2 ** attempt
                else:
                    delay = 2 ** attempt
                time.sleep(delay)
                continue

            status_code = getattr(response, "status_code", None)
            try:
                payload = response.json()
            except Exception:
                return ApiResult(
                    {"success": False, "error": f"Invalid response: {response.text}"},
                    status_code=status_code,
                )

            if isinstance(payload, dict) and "body" in payload and isinstance(payload["body"], str):
                # API-Gateway-style proxy envelope: the real status lives inside.
                inner_status = payload.get("statusCode") or payload.get("status_code")
                if isinstance(inner_status, (int, str)):
                    try:
                        status_code = int(inner_status)
                    except (TypeError, ValueError):
                        pass
                try:
                    inner = json.loads(payload["body"])
                except Exception:
                    return ApiResult(payload, status_code=status_code)
                return ApiResult(inner, status_code=status_code) if isinstance(inner, dict) else inner
            if isinstance(payload, dict):
                return ApiResult(payload, status_code=status_code)
            return payload

        return ApiResult({"success": False, "error": f"Request failed after {self._MAX_RETRIES + 1} attempts"})
