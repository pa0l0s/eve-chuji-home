import respx
from httpx import Response
from esi import (
    get_mail_headers, get_mail, get_mail_labels, get_mailing_lists,
    set_mail, delete_mail, create_mail_label, delete_mail_label,
    send_mail, resolve_recipient_ids,
)

CID = 2122515161
BASE = f"https://esi.evetech.net/latest/characters/{CID}"


@respx.mock
async def test_get_mail_headers_basic():
    route = respx.get(f"{BASE}/mail/").mock(return_value=Response(
        200, json=[{"mail_id": 1, "from": 99, "subject": "hi",
                    "timestamp": "2026-05-30T15:19:00Z",
                    "labels": [1], "recipients": []}]))
    out = await get_mail_headers(CID, "tok")
    assert out[0]["mail_id"] == 1
    assert route.called


@respx.mock
async def test_get_mail_headers_passes_pagination_and_labels():
    route = respx.get(f"{BASE}/mail/").mock(return_value=Response(200, json=[]))
    await get_mail_headers(CID, "tok", labels=[4], last_mail_id=500)
    req = route.calls[0].request
    assert "last_mail_id=500" in str(req.url)
    assert "labels=4" in str(req.url)


@respx.mock
async def test_get_mail_body():
    respx.get(f"{BASE}/mail/42/").mock(return_value=Response(
        200, json={"body": "<font>hi</font>", "subject": "s", "from": 9,
                   "labels": [1], "recipients": [], "timestamp": "t"}))
    out = await get_mail(CID, "tok", 42)
    assert out["body"] == "<font>hi</font>"


@respx.mock
async def test_get_mail_labels():
    respx.get(f"{BASE}/mail/labels/").mock(return_value=Response(
        200, json={"labels": [{"label_id": 1, "name": "Inbox", "unread_count": 8}],
                   "total_unread_count": 47}))
    out = await get_mail_labels(CID, "tok")
    assert out["total_unread_count"] == 47


@respx.mock
async def test_get_mailing_lists():
    respx.get(f"{BASE}/mail/lists/").mock(return_value=Response(
        200, json=[{"mailing_list_id": 7, "name": "L"}]))
    out = await get_mailing_lists(CID, "tok")
    assert out[0]["mailing_list_id"] == 7


@respx.mock
async def test_set_mail_sends_read_and_labels():
    route = respx.put(f"{BASE}/mail/42/").mock(return_value=Response(204))
    await set_mail(CID, "tok", 42, read=True, labels=[4])
    body = route.calls[0].request.content.decode()
    assert '"read": true' in body or '"read":true' in body
    assert "4" in body


@respx.mock
async def test_set_mail_omits_unset_fields():
    route = respx.put(f"{BASE}/mail/42/").mock(return_value=Response(204))
    await set_mail(CID, "tok", 42, read=False)
    body = route.calls[0].request.content.decode()
    assert "labels" not in body


@respx.mock
async def test_delete_mail():
    route = respx.delete(f"{BASE}/mail/42/").mock(return_value=Response(204))
    await delete_mail(CID, "tok", 42)
    assert route.called


@respx.mock
async def test_create_mail_label_returns_id():
    respx.post(f"{BASE}/mail/labels/").mock(return_value=Response(201, json=128))
    out = await create_mail_label(CID, "tok", "Mine", "#ff0000")
    assert out == 128


@respx.mock
async def test_delete_mail_label():
    route = respx.delete(f"{BASE}/mail/labels/128/").mock(return_value=Response(204))
    await delete_mail_label(CID, "tok", 128)
    assert route.called


@respx.mock
async def test_send_mail_returns_id_and_shapes_body():
    route = respx.post(f"{BASE}/mail/").mock(return_value=Response(201, json=999))
    recipients = [{"recipient_id": 5, "recipient_type": "character"}]
    out = await send_mail(CID, "tok", recipients, "subj", "body")
    assert out == 999
    body = route.calls[0].request.content.decode()
    assert "subj" in body and "recipient_id" in body


@respx.mock
async def test_resolve_recipient_ids():
    respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
        return_value=Response(200, json={
            "characters": [{"id": 5, "name": "Bob"}],
            "corporations": [{"id": 8, "name": "CorpX"}]}))
    out = await resolve_recipient_ids(["Bob", "CorpX"])
    assert {"recipient_id": 5, "recipient_type": "character", "name": "Bob"} in out
    assert {"recipient_id": 8, "recipient_type": "corporation", "name": "CorpX"} in out
