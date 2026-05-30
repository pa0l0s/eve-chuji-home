import time
import respx
from httpx import Response
from db import upsert_token
from auth import make_session_cookie

CID = 2122515161
BASE = f"https://esi.evetech.net/latest/characters/{CID}"


async def _auth(character_id=CID):
    await upsert_token(
        character_id=character_id, character_name="Paolo",
        access_token="valid_acc", refresh_token="valid_ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_mail_requires_auth(client):
    r = await client.get("/api/me/mail")
    assert r.status_code == 401


@respx.mock
async def test_mail_headers_enriched_with_names(client):
    cookies = await _auth()
    respx.get(f"{BASE}/mail/").mock(return_value=Response(200, json=[
        {"mail_id": 1, "from": 99, "subject": "hi", "timestamp": "t",
         "labels": [1], "recipients": [{"recipient_id": 5, "recipient_type": "character"}]}]))
    respx.post("https://esi.evetech.net/latest/universe/names/").mock(
        return_value=Response(200, json=[{"id": 99, "name": "Sender", "category": "character"},
                                         {"id": 5, "name": "Rcpt", "category": "character"}]))
    r = await client.get("/api/me/mail", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    assert data[0]["from_name"] == "Sender"
    assert data[0]["recipients"][0]["name"] == "Rcpt"


@respx.mock
async def test_mail_headers_403_gives_scope_hint(client):
    cookies = await _auth()
    respx.get(f"{BASE}/mail/").mock(return_value=Response(403, json={"error": "forbidden"}))
    r = await client.get("/api/me/mail", cookies=cookies)
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower() or "log out" in r.json()["detail"].lower()


@respx.mock
async def test_mail_body_returned_and_cached(client):
    cookies = await _auth()
    route = respx.get(f"{BASE}/mail/42/").mock(return_value=Response(200, json={
        "body": "<font>hi</font>", "subject": "s", "from": 99,
        "labels": [1], "recipients": [], "timestamp": "t"}))
    respx.post("https://esi.evetech.net/latest/universe/names/").mock(
        return_value=Response(200, json=[{"id": 99, "name": "Sender", "category": "character"}]))
    r1 = await client.get("/api/me/mail/42", cookies=cookies)
    r2 = await client.get("/api/me/mail/42", cookies=cookies)
    assert r1.status_code == 200
    assert r1.json()["from_name"] == "Sender"
    assert route.call_count == 1  # second hit served from cache


@respx.mock
async def test_mail_labels_combined_with_lists(client):
    cookies = await _auth()
    respx.get(f"{BASE}/mail/labels/").mock(return_value=Response(200, json={
        "labels": [{"label_id": 1, "name": "Inbox", "unread_count": 8}],
        "total_unread_count": 47}))
    respx.get(f"{BASE}/mail/lists/").mock(return_value=Response(200, json=[
        {"mailing_list_id": 7, "name": "L"}]))
    r = await client.get("/api/me/mail/labels", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    assert data["total_unread_count"] == 47
    assert data["lists"][0]["mailing_list_id"] == 7


@respx.mock
async def test_mail_unread_returns_inbox_count(client):
    cookies = await _auth()
    respx.get(f"{BASE}/mail/labels/").mock(return_value=Response(200, json={
        "labels": [{"label_id": 1, "name": "Inbox", "unread_count": 8},
                   {"label_id": 4, "name": "[Corp]", "unread_count": 39}],
        "total_unread_count": 47}))
    r = await client.get("/api/me/mail/unread", cookies=cookies)
    assert r.status_code == 200
    assert r.json()["inbox_unread"] == 8
