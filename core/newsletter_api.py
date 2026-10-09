"""
External /api/v1/newsletter/* responses for customer accounts (skeleton for phase 2).

Account keys authorize these routes. A newsletter account row links an accounts.id
to listmonk list IDs once provisioning is built.
"""

from __future__ import annotations

import json
from typing import Optional

from core.newsletter import listmonk_configured, status as newsletter_status
from core.newsletter_config import load as load_config
from plugins.newsletters.listmonk import ListmonkClient


def _account_row(db, account_id: int):
    return db.conn.execute(
        "SELECT * FROM newsletter_accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()


def public_account_payload(db, account: dict) -> dict:
    """Summary for GET /api/v1/newsletter/account."""
    cfg = load_config()
    base = {
        "external_ref": account["external_ref"],
        "listmonk_configured": listmonk_configured(),
        "public_url": cfg.get("public_url") or None,
        "phase": "skeleton",
    }
    row = _account_row(db, account["id"])
    if row is None:
        return {
            **base,
            "status": "not_provisioned",
            "message": "No newsletter account is linked yet. Provisioning (phase 2) attaches listmonk lists to this API account.",
            "list_ids": [],
        }
    list_ids = json.loads(row["listmonk_list_ids"] or "[]")
    return {
        **base,
        "status": row["status"],
        "prospect_id": row["prospect_id"],
        "list_ids": list_ids,
    }


def list_campaigns_for_account(db, account: dict, *, page: int = 1, per_page: int = 20) -> dict:
    if not listmonk_configured():
        return {"ok": False, "error": "service_not_configured", "message": "listmonk is not configured on this server."}
    row = _account_row(db, account["id"])
    list_ids = json.loads(row["listmonk_list_ids"] or "[]") if row else []
    if not list_ids:
        return {
            "ok": True,
            "campaigns": [],
            "total": 0,
            "status": "not_provisioned",
            "message": "Link a newsletter account with list IDs to see campaigns here.",
        }
    client = ListmonkClient()
    try:
        result = client.list_campaigns(list_ids=list_ids, page=page, per_page=per_page)
    finally:
        client.close()
    if not result.get("ok"):
        return {"ok": False, "error": "upstream_error", "message": result.get("error")}
    campaigns = []
    for c in result.get("campaigns") or []:
        campaigns.append(_public_campaign(c))
    return {"ok": True, "campaigns": campaigns, "total": result.get("total", len(campaigns))}


def get_campaign_for_account(db, account: dict, campaign_id: int) -> dict:
    if not listmonk_configured():
        return {"ok": False, "error": "service_not_configured", "message": "listmonk is not configured on this server."}
    row = _account_row(db, account["id"])
    list_ids = {int(x) for x in json.loads(row["listmonk_list_ids"] or "[]")} if row else set()
    client = ListmonkClient()
    try:
        result = client.get_campaign(campaign_id)
    finally:
        client.close()
    if not result.get("ok"):
        return {"ok": False, "error": "not_found", "message": result.get("error") or "campaign not found"}
    campaign = result.get("campaign") or {}
    if list_ids:
        c_lists = campaign.get("lists") or []
        ids = {int(lst.get("id") if isinstance(lst, dict) else lst) for lst in c_lists}
        if not (ids & list_ids):
            return {"ok": False, "error": "not_found", "message": "Campaign not in this account's lists."}
    return {"ok": True, "campaign": _public_campaign(campaign, detail=True)}


def staff_status(db) -> dict:
    return newsletter_status(db)


def _public_campaign(row: dict, detail: bool = False) -> dict:
    out = {
        "id": row.get("id"),
        "name": row.get("name"),
        "subject": row.get("subject"),
        "status": row.get("status"),
        "type": row.get("type"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "sent": row.get("sent"),
        "to_send": row.get("to_send"),
        "tags": row.get("tags") or [],
    }
    if detail:
        out["body"] = row.get("body")
        out["lists"] = [
            {"id": lst.get("id"), "name": lst.get("name")}
            for lst in (row.get("lists") or []) if isinstance(lst, dict)
        ]
    cfg = load_config()
    if cfg.get("public_url") and row.get("uuid"):
        out["archive_url"] = f"{cfg['public_url']}/archive/{row['uuid']}"
    return out
