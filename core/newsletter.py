"""
Newsletter list rules, sync, and suppression integration (docs/LISTMONK_INTEGRATION.md).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from core import verify
from core.newsletter_config import load as load_config
from plugins.newsletters.listmonk import ListmonkClient

CURSOR_SUBSCRIBERS = "listmonk:subscribers"
CURSOR_BOUNCES = "listmonk:bounces"

NEWSLETTER_EVENT_KINDS = (
    "newsletter.subscribed",
    "newsletter.unsubscribed",
    "newsletter.bounced",
)


def listmonk_configured() -> bool:
    return ListmonkClient().is_configured()


def _listmonk_epoch(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
    return 0


def _record_event_for_email(db, email: str, kind: str, value: str, *, detail: dict | None = None) -> None:
    row = db.conn.execute(
        """SELECT prospect_id, campaign_id FROM outreach
           WHERE lower(contact_email) = ? ORDER BY updated_at DESC LIMIT 1""",
        (email.strip().lower(),),
    ).fetchone()
    if row is None:
        return
    verify.record_event(db, row["prospect_id"], kind, value, campaign_id=row["campaign_id"], detail=detail)


def _client() -> ListmonkClient:
    return ListmonkClient()


def _outreach_row(db, outreach_id: int):
    return db.conn.execute(
        """SELECT o.*, p.name AS org_name, c.name AS campaign_name
           FROM outreach o
           JOIN prospects p ON p.id = o.prospect_id
           JOIN campaigns c ON c.id = o.campaign_id
           WHERE o.id = ?""",
        (outreach_id,),
    ).fetchone()


def on_outreach_stage(db, outreach_id: int, new_stage: str, *, dry_run: bool = False) -> dict:
    """Apply list rules when an outreach stage changes (currently: closed_won → Customers)."""
    if new_stage != "closed_won":
        return {"ok": True, "skipped": "no rule for stage"}
    cfg = load_config()
    list_id = cfg["list_ids"]["customers"]
    if not list_id:
        return {"ok": True, "skipped": "customers list not configured"}
    row = _outreach_row(db, outreach_id)
    if row is None:
        return {"ok": False, "error": "outreach not found"}
    email = (row["contact_email"] or "").strip().lower()
    if not email:
        return {"ok": True, "skipped": "no email"}
    if db.is_suppressed(email):
        return {"ok": True, "skipped": "suppressed"}
    if dry_run:
        return {"ok": True, "dry_run": True, "would_add": email, "list_id": list_id}
    return _subscribe(
        db,
        email=email,
        name=row["contact_name"] or "",
        list_id=list_id,
        preconfirm=True,
        attribs=_attribs(row),
        outreach_id=outreach_id,
        prospect_id=row["prospect_id"],
        campaign_id=row["campaign_id"],
        event_kind="newsletter.subscribed",
    )


def invite(db, outreach_id: int, *, dry_run: bool = False, user_id: int | None = None) -> dict:
    """Add contact to Civic Updates (double opt-in)."""
    cfg = load_config()
    list_id = cfg["list_ids"]["civic_updates"]
    if not list_id:
        return {"ok": False, "error": "civic_updates list not configured"}
    if not listmonk_configured():
        return {"ok": False, "error": "listmonk is not configured"}
    row = _outreach_row(db, outreach_id)
    if row is None:
        return {"ok": False, "error": "outreach not found"}
    email = (row["contact_email"] or "").strip().lower()
    if not email:
        return {"ok": False, "error": "no contact email on this outreach"}
    if db.is_suppressed(email):
        return {"ok": False, "error": f"suppressed: {db.is_suppressed(email)}"}
    if dry_run:
        return {"ok": True, "dry_run": True, "would_invite": email, "list_id": list_id}
    return _subscribe(
        db,
        email=email,
        name=row["contact_name"] or "",
        list_id=list_id,
        preconfirm=False,
        attribs=_attribs(row),
        outreach_id=outreach_id,
        prospect_id=row["prospect_id"],
        campaign_id=row["campaign_id"],
        event_kind="newsletter.subscribed",
        list_status="unconfirmed",
        user_id=user_id,
    )


def _attribs(row) -> dict:
    return {
        "agency_os": {
            "prospect_id": row["prospect_id"],
            "outreach_id": row["id"],
            "campaign": row["campaign_name"],
            "org_name": row["org_name"],
        }
    }


def _subscribe(
    db,
    *,
    email: str,
    name: str,
    list_id: int,
    preconfirm: bool,
    attribs: dict,
    outreach_id: int,
    prospect_id: int,
    campaign_id: int,
    event_kind: str,
    list_status: str = "confirmed",
    user_id: int | None = None,
) -> dict:
    client = _client()
    try:
        upsert = client.upsert_subscriber(
            email,
            name=name,
            list_ids=[list_id],
            attribs=attribs,
            preconfirm_subscriptions=preconfirm,
        )
        if not upsert.get("ok"):
            return upsert
        sub = upsert.get("subscriber") or {}
        sub_id = sub.get("id")
        if sub_id and not upsert.get("created"):
            added = client.add_to_list([int(sub_id)], list_id, status=list_status)
            if not added.get("ok"):
                return added
        user = type("_U", (), {"id": user_id})() if user_id else None
        verify.record_event(
            db, prospect_id, event_kind, email,
            campaign_id=campaign_id,
            detail={"source": "listmonk", "list_id": list_id, "outreach_id": outreach_id},
            user=user,
        )
        return {"ok": True, "subscriber_id": sub_id, "email": email}
    finally:
        client.close()


def pull_newsletter_changes(db, *, dry_run: bool = False) -> dict:
    """Pull subscriber and bounce changes from listmonk; update suppressions and events."""
    stats = {"subscribers_seen": 0, "suppressions": 0, "events": 0, "skipped": False}
    if not listmonk_configured():
        stats["skipped"] = True
        stats["reason"] = "listmonk not configured"
        return stats
    cfg = load_config()
    our_lists = set(cfg["u9itus_list_ids"])
    client = _client()
    try:
        cursor = int(db.get_sync_cursor(CURSOR_SUBSCRIBERS) or 0)
        pulled = client.changed_subscribers(cursor)
        if not pulled.get("ok"):
            return {**stats, "error": pulled.get("error")}
        max_epoch = cursor
        for sub in pulled.get("results") or []:
            stats["subscribers_seen"] += 1
            email = (sub.get("email") or "").strip().lower()
            if not email:
                continue
            max_epoch = max(max_epoch, _listmonk_epoch(sub.get("updated_at")))
            if sub.get("status") == "blocklisted":
                if not dry_run:
                    if db.suppress_email(email, "blocklisted", "listmonk"):
                        stats["suppressions"] += 1
                continue
            for lst in sub.get("lists") or []:
                lid = int(lst.get("id") or 0)
                if our_lists and lid not in our_lists:
                    continue
                if lst.get("subscription_status") == "unsubscribed":
                    if not dry_run:
                        if db.suppress_email(email, "unsubscribed", "listmonk"):
                            stats["suppressions"] += 1
                        _record_event_for_email(db, email, "newsletter.unsubscribed", email,
                                                detail={"source": "listmonk", "list_id": lid})
                        stats["events"] += 1
            attribs = sub.get("attribs") or {}
            aos = attribs.get("agency_os") if isinstance(attribs, dict) else {}
            if upsert_created := sub.get("created_at"):
                _ = upsert_created
            if not dry_run and aos and isinstance(aos, dict):
                pid = aos.get("prospect_id")
                if pid:
                    pass  # events recorded on subscribe path
        if not dry_run and max_epoch > cursor:
            db.set_sync_cursor(CURSOR_SUBSCRIBERS, max_epoch)

        bounce_cursor = int(db.get_sync_cursor(CURSOR_BOUNCES) or 0)
        bounces = client.bounces(bounce_cursor)
        if bounces.get("ok"):
            max_id = bounce_cursor
            for bounce in bounces.get("results") or []:
                max_id = max(max_id, int(bounce.get("id") or 0))
                email = (bounce.get("email") or "").strip().lower()
                btype = (bounce.get("type") or "").lower()
                if not email:
                    continue
                if btype in ("hard", "complaint"):
                    reason = "complaint" if btype == "complaint" else "bounced"
                    if not dry_run:
                        if db.suppress_email(email, reason, "listmonk"):
                            stats["suppressions"] += 1
                        _record_event_for_email(db, email, "newsletter.bounced", email,
                                                detail={"source": "listmonk", "bounce_type": btype})
                        stats["events"] += 1
            if not dry_run and max_id > bounce_cursor:
                db.set_sync_cursor(CURSOR_BOUNCES, max_id)
        return stats
    finally:
        client.close()


def sync_now(db, *, dry_run: bool = False) -> dict:
    return pull_newsletter_changes(db, dry_run=dry_run)


def status(db) -> dict:
    cfg = load_config()
    client = _client()
    try:
        health = client.check_connection() if client.is_configured() else {"ok": False, "error": "not configured"}
    finally:
        client.close()
    return {
        "listmonk_configured": client.is_configured(),
        "listmonk_version": health.get("version") if health.get("ok") else None,
        "listmonk_error": None if health.get("ok") else health.get("error"),
        "list_ids": cfg["list_ids"],
        "public_url": cfg["public_url"],
        "subscriber_cursor": db.get_sync_cursor(CURSOR_SUBSCRIBERS),
        "bounce_cursor": db.get_sync_cursor(CURSOR_BOUNCES),
        "suppression_count": db.count_suppressions(),
    }
