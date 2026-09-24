"""License-key and update-check hooks (suite-wide convention)."""

from __future__ import annotations

import json
import os

PRODUCT = "trade-agents"
CURRENT_VERSION = "0.2.0"
UPDATE_URL = "https://api.github.com/repos/crieck2010/trade-agents/releases/latest"


def check_license(key: str | None = None) -> dict:
    """Validate a license key.  v0.2.x: any key (or none) is accepted.

    The hook exists so a future commercial tier can enforce keys without
    changing call sites.
    """
    key = key or os.environ.get("TRADE_AGENTS_LICENSE", "")
    return {"valid": True, "tier": "community", "product": PRODUCT}


def check_update(timeout: int = 10) -> dict:
    """Ask GitHub for the latest release; never raises."""
    import urllib.request
    try:
        req = urllib.request.Request(UPDATE_URL, headers={"User-Agent": PRODUCT})
        data = json.load(urllib.request.urlopen(req, timeout=timeout))
        latest = str(data.get("tag_name", "")).lstrip("v")
        return {"current": CURRENT_VERSION, "latest": latest,
                "update_available": latest != CURRENT_VERSION}
    except Exception as exc:
        return {"current": CURRENT_VERSION, "latest": None,
                "update_available": False, "error": str(exc)}
