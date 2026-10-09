"""Load newsletter settings from config.yaml and environment."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _int_env(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def load() -> dict:
    """Newsletter block: list IDs, public archive base URL."""
    path = ROOT / "config.yaml"
    local = ROOT / "config.local.yaml"
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    if local.exists():
        merged = yaml.safe_load(local.read_text()) or {}
        raw = {**raw, **merged}
    block = raw.get("newsletter") or {}
    list_ids = block.get("list_ids") or {}
    customers = _int_env("LISTMONK_LIST_CUSTOMERS", int(list_ids.get("customers") or 0))
    civic = _int_env("LISTMONK_LIST_CIVIC_UPDATES", int(list_ids.get("civic_updates") or 0))
    public_url = (os.environ.get("LISTMONK_PUBLIC_URL") or block.get("public_url") or "").rstrip("/")
    u9itus_lists = [x for x in (customers, civic) if x]
    return {
        "provider": block.get("provider") or "listmonk",
        "list_ids": {"customers": customers, "civic_updates": civic},
        "u9itus_list_ids": u9itus_lists,
        "public_url": public_url,
    }


def reset_cache() -> None:
    load.cache_clear()
