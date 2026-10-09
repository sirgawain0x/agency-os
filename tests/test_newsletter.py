"""Newsletter integration: listmonk client, suppressions, API skeleton."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import newsletter, newsletter_config
from core.db import Database
from core.newsletter_api import public_account_payload
from plugins.newsletters.listmonk import ListmonkClient
from tests.test_access import client_for, db, make_user  # noqa: F401
from tests.test_accounts import bearer, platform  # noqa: F401


@pytest.fixture
def listmonk_env(monkeypatch):
    monkeypatch.setenv("LISTMONK_URL", "http://listmonk.test")
    monkeypatch.setenv("LISTMONK_API_USER", "api")
    monkeypatch.setenv("LISTMONK_API_TOKEN", "secret")
    newsletter_config.reset_cache()


def test_listmonk_client_check_connection(listmonk_env, monkeypatch):
    def fake_client(**kwargs):
        assert kwargs["headers"]["Authorization"] == "token api:secret"
        mock = MagicMock()
        mock.request.return_value = httpx.Response(200, json={"data": {"version": "v6.2.0"}})
        return mock

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = ListmonkClient()
    result = client.check_connection()
    assert result["ok"] is True
    assert result["version"] == "v6.2.0"


def test_listmonk_timeout_never_raises(listmonk_env):
    mock = MagicMock(spec=httpx.Client)
    mock.request.side_effect = httpx.TimeoutException("slow")
    client = ListmonkClient(client=mock, max_retries=1)
    result = client.check_connection()
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_email_suppression_table(db):
    assert db.is_suppressed("nobody@example.com") is None
    assert db.suppress_email("blocked@example.com", "unsubscribed", "listmonk") is True
    assert db.is_suppressed("blocked@example.com") == "unsubscribed"
    assert db.suppress_email("blocked@example.com", "unsubscribed", "listmonk") is False
    assert db.unsuppress_email("blocked@example.com") is True
    assert db.is_suppressed("blocked@example.com") is None


def test_newsletter_api_not_provisioned(db, platform):
    c = client_for()
    create = c.post("/api/v1/accounts", json={"external_ref": "org_nl", "name": "NL Co"}, headers=platform)
    key = create.json()["key"]
    r = c.get("/api/v1/newsletter/account", headers=bearer(key))
    assert r.status_code == 200
    body = r.json()["newsletter"]
    assert body["status"] == "not_provisioned"
    assert body["external_ref"] == "org_nl"


def test_on_closed_won_dry_run(db, monkeypatch):
    monkeypatch.setenv("LISTMONK_LIST_CUSTOMERS", "42")
    newsletter_config.reset_cache()
    pid = db.upsert_prospect(__import__("core.models", fromlist=["Prospect"]).Prospect(
        name="Won Co", city="LA", state="CA", source="test"))
    cid = db.conn.execute("INSERT INTO campaigns (name) VALUES ('won-c') RETURNING id").fetchone()["id"]
    oid = db.conn.execute(
        """INSERT INTO outreach (prospect_id, campaign_id, stage, contact_email)
           VALUES (?, ?, 'closed_won', 'won@example.com') RETURNING id""",
        (pid, cid),
    ).fetchone()["id"]
    result = newsletter.on_outreach_stage(db, oid, "closed_won", dry_run=True)
    assert result.get("dry_run") is True
    assert result.get("would_add") == "won@example.com"
