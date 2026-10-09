"""
listmonk HTTP client for agency-os newsletter integration.

Auth: Authorization: token <api_user>:<api_token>
Env: LISTMONK_URL, LISTMONK_API_USER, LISTMONK_API_TOKEN

Every method returns {ok: bool, ...} and never raises.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx


def _ok(data: dict | None = None, **extra) -> dict:
    out = {"ok": True}
    if data:
        out.update(data)
    out.update(extra)
    return out


def _err(error: str, **extra) -> dict:
    return {"ok": False, "error": error, **extra}


class ListmonkClient:
    """Thin HTTP client for the listmonk JSON API."""

    def __init__(
        self,
        base_url: str = "",
        api_user: str = "",
        api_token: str = "",
        timeout: float = 10.0,
        max_retries: int = 3,
        client: Optional[httpx.Client] = None,
    ):
        self._base_url = (base_url or os.environ.get("LISTMONK_URL", "")).rstrip("/")
        self._api_user = (api_user or os.environ.get("LISTMONK_API_USER", "")).strip()
        self._api_token = (api_token or os.environ.get("LISTMONK_API_TOKEN", "")).strip().strip('"').strip("'")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            token = f"{self._api_user}:{self._api_token}"
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={
                    "Authorization": f"token {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_user and self._api_token)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def check_connection(self) -> dict:
        """GET /api/health — version and uptime."""
        if not self.is_configured():
            return _err("LISTMONK_URL, LISTMONK_API_USER and LISTMONK_API_TOKEN must be set")
        result = self._request("GET", "/api/health")
        if not result.get("ok"):
            return result
        data = result.get("data") or {}
        return _ok(version=data.get("version"), data=data)

    def find_subscriber_by_email(self, email: str) -> dict:
        """Return {ok, subscriber} or {ok, subscriber: None}."""
        email = (email or "").strip().lower()
        if not email:
            return _err("email is required")
        safe = email.replace("'", "''")
        result = self._paginate(
            "/api/subscribers",
            params={"query": f"subscribers.email = '{safe}'", "per_page": 1},
        )
        if not result.get("ok"):
            return result
        rows = result.get("results") or []
        return _ok(subscriber=rows[0] if rows else None)

    def upsert_subscriber(
        self,
        email: str,
        name: str = "",
        list_ids: list[int] | None = None,
        attribs: dict | None = None,
        preconfirm_subscriptions: bool = False,
    ) -> dict:
        """Create a subscriber or return the existing one."""
        email = (email or "").strip().lower()
        if not email:
            return _err("email is required")
        found = self.find_subscriber_by_email(email)
        if not found.get("ok"):
            return found
        existing = found.get("subscriber")
        if existing:
            return _ok(subscriber=existing, created=False)
        body: dict[str, Any] = {
            "email": email,
            "name": name or "",
            "status": "enabled",
            "lists": list_ids or [],
            "preconfirm_subscriptions": preconfirm_subscriptions,
        }
        if attribs:
            body["attribs"] = attribs
        result = self._request("POST", "/api/subscribers", json=body)
        if not result.get("ok"):
            return result
        data = result.get("data") or {}
        return _ok(subscriber=data.get("data") or data, created=True)

    def add_to_list(
        self,
        subscriber_ids: list[int],
        list_id: int,
        *,
        status: str = "confirmed",
    ) -> dict:
        """PUT /api/subscribers/lists — add subscribers to one list."""
        if not subscriber_ids:
            return _err("subscriber_ids is required")
        if not list_id:
            return _err("list_id is required")
        body = {
            "ids": subscriber_ids,
            "action": "add",
            "target_list_ids": [list_id],
            "status": status,
        }
        result = self._request("PUT", "/api/subscribers/lists", json=body)
        if not result.get("ok"):
            return result
        return _ok()

    def changed_subscribers(self, updated_after_epoch: int) -> dict:
        """Subscribers changed after cursor (epoch seconds), ascending."""
        from datetime import datetime, timezone

        if updated_after_epoch:
            cursor_iso = datetime.fromtimestamp(updated_after_epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            query = f"subscribers.updated_at > '{cursor_iso}'"
        else:
            query = "subscribers.id > 0"
        return self._paginate(
            "/api/subscribers",
            params={"query": query, "order_by": "updated_at", "order": "asc", "per_page": 100},
        )

    def bounces(self, after_id: int = 0) -> dict:
        """Bounces with id greater than after_id."""
        params: dict[str, Any] = {"per_page": 100, "order_by": "id", "order": "asc"}
        if after_id:
            params["query"] = f"bounces.id > {int(after_id)}"
        return self._paginate("/api/bounces", params=params, id_field="id")

    def list_campaigns(self, list_ids: list[int] | None = None, page: int = 1, per_page: int = 20) -> dict:
        """GET /api/campaigns — optional filter to campaigns that include any list_id."""
        params: dict[str, Any] = {"page": page, "per_page": per_page, "order_by": "created_at", "order": "desc"}
        result = self._request("GET", "/api/campaigns", params=params)
        if not result.get("ok"):
            return result
        payload = result.get("data") or {}
        rows = payload.get("results") or payload.get("data") or []
        if list_ids:
            want = {int(x) for x in list_ids}
            filtered = []
            for row in rows:
                lists = row.get("lists") or []
                ids = {int(lst.get("id") if isinstance(lst, dict) else lst) for lst in lists}
                if ids & want:
                    filtered.append(row)
            rows = filtered
        return _ok(campaigns=rows, total=payload.get("total", len(rows)))

    def get_campaign(self, campaign_id: int) -> dict:
        result = self._request("GET", f"/api/campaigns/{int(campaign_id)}")
        if not result.get("ok"):
            return result
        payload = result.get("data") or {}
        return _ok(campaign=payload.get("data") or payload)

    def _paginate(self, path: str, params: dict | None = None, id_field: str = "updated_at") -> dict:
        params = dict(params or {})
        page = 1
        all_rows: list = []
        max_updated = 0
        max_id = 0
        while True:
            params["page"] = page
            result = self._request("GET", path, params=params)
            if not result.get("ok"):
                return result
            payload = result.get("data") or {}
            rows = payload.get("results") or payload.get("data") or []
            if not rows:
                break
            all_rows.extend(rows)
            for row in rows:
                if id_field == "id":
                    max_id = max(max_id, int(row.get("id") or 0))
                else:
                    ts = row.get("updated_at") or row.get("created_at") or ""
                    if isinstance(ts, str) and ts.isdigit():
                        max_updated = max(max_updated, int(ts))
                    elif isinstance(ts, (int, float)):
                        max_updated = max(max_updated, int(ts))
            if len(rows) < int(params.get("per_page") or 100):
                break
            page += 1
        out = _ok(results=all_rows)
        if id_field == "id":
            out["max_id"] = max_id
        else:
            out["max_updated_epoch"] = max_updated
        return out

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.is_configured():
            return _err("listmonk is not configured")
        url = f"{self._base_url}{path}"
        for attempt in range(self._max_retries):
            try:
                resp = self.client.request(method, url, **kwargs)
                if resp.status_code == 401:
                    return _err("listmonk rejected the API token", status=401)
                if resp.status_code == 403:
                    return _err("listmonk forbidden", status=403)
                if resp.status_code >= 500:
                    if attempt < self._max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return _err(f"listmonk server error ({resp.status_code})", status=resp.status_code)
                if resp.status_code >= 300:
                    return _err(resp.text[:500] or f"HTTP {resp.status_code}", status=resp.status_code)
                if not resp.content:
                    return _ok(data={})
                try:
                    return _ok(data=resp.json())
                except ValueError:
                    return _ok(data={"raw": resp.text[:500]})
            except httpx.TimeoutException:
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return _err("request timed out")
            except httpx.ConnectError:
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return _err("connection failed")
            except Exception as exc:
                return _err(str(exc))
        return _err("max retries exceeded")


class ListmonkNewsletter:
    """Plugin entry point for registry and the Plugins page."""

    key = "listmonk"

    def is_configured(self) -> bool:
        return ListmonkClient().is_configured()

    def check_connection(self) -> dict:
        client = ListmonkClient()
        try:
            result = client.check_connection()
        finally:
            client.close()
        if not result.get("ok"):
            return {"error": True, "detail": result.get("error") or "connection failed"}
        return {"version": result.get("version")}
