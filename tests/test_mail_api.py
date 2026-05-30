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


@respx.mock
async def test_set_mail_read(client):
    cookies = await _auth()
    route = respx.put(f"{BASE}/mail/42/").mock(return_value=Response(204))
    r = await client.put("/api/me/mail/42", json={"read": True}, cookies=cookies)
    assert r.status_code == 200
    assert route.called


@respx.mock
async def test_delete_mail_route(client):
    cookies = await _auth()
    route = respx.delete(f"{BASE}/mail/42/").mock(return_value=Response(204))
    r = await client.delete("/api/me/mail/42", cookies=cookies)
    assert r.status_code == 200
    assert route.called


@respx.mock
async def test_create_and_delete_label(client):
    cookies = await _auth()
    respx.post(f"{BASE}/mail/labels/").mock(return_value=Response(201, json=128))
    r = await client.post("/api/me/mail/labels",
                          json={"name": "Mine", "color": "#ff0000"}, cookies=cookies)
    assert r.status_code == 200
    assert r.json()["label_id"] == 128
    route = respx.delete(f"{BASE}/mail/labels/128/").mock(return_value=Response(204))
    r2 = await client.delete("/api/me/mail/labels/128", cookies=cookies)
    assert r2.status_code == 200
    assert route.called


@respx.mock
async def test_send_mail_route(client):
    cookies = await _auth()
    respx.post(f"{BASE}/mail/").mock(return_value=Response(201, json=999))
    r = await client.post("/api/me/mail", cookies=cookies, json={
        "recipients": [{"recipient_id": 5, "recipient_type": "character"}],
        "subject": "s", "body": "b"})
    assert r.status_code == 200
    assert r.json()["mail_id"] == 999


@respx.mock
async def test_send_mail_propagates_esi_error(client):
    cookies = await _auth()
    respx.post(f"{BASE}/mail/").mock(return_value=Response(
        520, json={"error": "ContactCostNotApproved"}))
    r = await client.post("/api/me/mail", cookies=cookies, json={
        "recipients": [{"recipient_id": 5, "recipient_type": "character"}],
        "subject": "s", "body": "b"})
    assert r.status_code == 502
    assert "ContactCostNotApproved" in r.json()["detail"]


@respx.mock
async def test_resolve_recipients_route(client):
    cookies = await _auth()
    respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
        return_value=Response(200, json={"characters": [{"id": 5, "name": "Bob"}]}))
    r = await client.post("/api/me/mail/resolve", json={"names": ["Bob"]}, cookies=cookies)
    assert r.status_code == 200
    assert r.json()[0]["recipient_id"] == 5


@respx.mock
async def test_mail_contacts_resolved_and_filtered(client):
    cookies = await _auth()
    respx.get(f"{BASE}/contacts/").mock(return_value=Response(200, json=[
        {"contact_id": 5, "contact_type": "character", "standing": 10.0},
        {"contact_id": 8, "contact_type": "corporation", "standing": 0.0},
        {"contact_id": 9, "contact_type": "faction", "standing": 0.0}],
        headers={"X-Pages": "1"}))
    respx.post("https://esi.evetech.net/latest/universe/names/").mock(
        return_value=Response(200, json=[
            {"id": 5, "name": "Bob", "category": "character"},
            {"id": 8, "name": "CorpX", "category": "corporation"}]))
    r = await client.get("/api/me/mail/contacts", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    ids = [c["recipient_id"] for c in data]
    assert 5 in ids and 8 in ids and 9 not in ids
    assert data[0]["recipient_id"] == 5          # highest standing first
    assert data[0]["name"] == "Bob"
