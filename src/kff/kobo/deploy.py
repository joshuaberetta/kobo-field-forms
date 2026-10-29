"""Create, deploy and delete the throwaway projects the live tests and `kff e2e` use.

Deleting is only allowed for projects named `kff-e2e-*` (CLAUDE.md): Kobo keeps no trash,
so nothing else may ever be deleted from here.
"""

from typing import Any

from kff.errors import KffError
from kff.kobo.client import KoboClient

TEST_PREFIX = "kff-e2e-"


def create_and_deploy(client: KoboClient, name: str, content: dict[str, Any]) -> str:
    """A new deployed survey; returns its uid (KB §4.4–4.6: create, then PATCH content,
    then deploy). If a later step fails, the half-made project is deleted."""
    if not name.startswith(TEST_PREFIX):
        raise KffError(f"Test projects must be named {TEST_PREFIX}…")
    asset = client.post("/api/v2/assets/", {"name": name, "asset_type": "survey", "settings": {}})
    uid = asset["uid"]
    try:
        version = client.patch(f"/api/v2/assets/{uid}/", {"content": content})["version_id"]
        client.post(f"/api/v2/assets/{uid}/deployment/", {"active": True, "version_id": version})
    except Exception:
        delete_test_project(client, uid)
        raise
    return uid


def delete_test_project(client: KoboClient, uid: str) -> None:
    asset = client.get(f"/api/v2/assets/{uid}/")
    if not str(asset.get("name", "")).startswith(TEST_PREFIX):
        raise KffError(f"Refusing to delete {uid}: it isn't a {TEST_PREFIX}* test project.")
    client.request("DELETE", f"/api/v2/assets/{uid}/")
