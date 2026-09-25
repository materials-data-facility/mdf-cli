"""Options every networked `mdf` command shares, and the one way to get a client.

Declare the common options with these aliases instead of hand-copying them::

    def show(source_id: str, service: ServiceOpt = None, json_output: JsonOpt = False, ...):
        with open_client(service=service, api_url=api_url, token=token,
                         dev_user=dev_user, json_mode=json_output) as client:
            ...

``open_client`` closes the client on every path (return, raise, typer.Exit).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Iterator, Optional

import typer

from mdf.core.config import resolve_service

ServiceOpt = Annotated[
    Optional[str],
    typer.Option("--service", "-s", help="Service instance (staging/prod/dev/local)"),
]
ApiUrlOpt = Annotated[
    Optional[str],
    typer.Option("--api-url", help="Override the API base URL"),
]
TokenOpt = Annotated[Optional[str], typer.Option("--token", help="Globus access token")]
#: For local development against a backend in AUTH_MODE=dev; not for users.
DevUserOpt = Annotated[
    Optional[str], typer.Option("--dev-user", hidden=True, help="Dev-mode user id (X-User-Id)")
]
JsonOpt = Annotated[bool, typer.Option("--json", help="JSON output")]


@contextmanager
def open_client(
    *,
    public: bool = True,
    service: Optional[str] = None,
    api_url: Optional[str] = None,
    token: Optional[str] = None,
    dev_user: Optional[str] = None,
    json_mode: bool = False,
) -> Iterator["BackendClient"]:  # noqa: F821
    """Yield a backend client for one command and always close it.

    ``public=True`` is for public reads: it never forces a login (it still uses
    credentials you already have, so your own restricted records stay
    visible). ``public=False`` needs credentials; without any, the root
    command's login policy decides between a prompt and the ``auth`` error.
    """
    from mdf.cli import formatting
    from mdf.core.backend_client import BackendClient

    resolved = resolve_service(service)
    if public:
        client = formatting.read_client(
            api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_mode
        )
    else:
        client = BackendClient.authenticated(
            base_url=api_url, token=token, service_instance=resolved, dev_user_id=dev_user
        )
    try:
        yield client
    finally:
        client.close()
