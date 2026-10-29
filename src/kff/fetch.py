"""Fetch a deployed form version from Kobo.

The asset's `content` is the latest save, which may not be deployed, so the content
always comes from `versions/{deployed version uid}/` (kobo-mcp KB §4.2, §5.6).
"""

import re

from kff.errors import KffError, NotFoundError
from kff.kobo.client import KoboClient
from kff.store import FormVersion

# A project link (`https://kf.kobotoolbox.org/#/forms/<uid>/summary`) or a bare uid.
_LINK_UID = re.compile(r"/forms/([A-Za-z0-9]+)")
_UID = re.compile(r"^a[A-Za-z0-9]{10,}$")


def parse_asset_uid(value: str) -> str:
    value = value.strip()
    if m := _LINK_UID.search(value):
        value = m.group(1)
    if not _UID.match(value):
        raise KffError(f"{value!r} doesn't look like a Kobo project uid (it starts with 'a').")
    return value


def fetch_form(
    client: KoboClient, server: str, asset_uid: str, version_uid: str | None = None
) -> FormVersion:
    """The deployed version `version_uid` (default: the one deployed now) of a survey."""
    asset = client.get(f"/api/v2/assets/{asset_uid}/")
    if asset.get("asset_type") != "survey":
        raise KffError(f"{asset_uid} is a {asset.get('asset_type')}, not a survey.")
    if not asset.get("has_deployment") or not asset.get("deployed_version_id"):
        raise KffError(f"{asset_uid} ({asset['name']}) has never been deployed.")
    version_uid = version_uid or asset["deployed_version_id"]

    try:
        version = client.get(f"/api/v2/assets/{asset_uid}/versions/{version_uid}/")
    except NotFoundError:
        raise KffError(f"{asset_uid} has no version {version_uid}.") from None
    if not version.get("date_deployed"):
        raise KffError(
            f"Version {version_uid} of {asset_uid} was saved but never deployed, so "
            "submissions can't be made against it."
        )
    return FormVersion(
        server=server,
        asset_uid=asset_uid,
        version_uid=version_uid,
        name=asset["name"],
        version_number=version.get("version_number"),
        date_deployed=version["date_deployed"],
        deployment_uuid=asset["deployment__uuid"],
        content_hash=version.get("content_hash"),
        content=version["content"],
    )
