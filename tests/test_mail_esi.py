import respx
from httpx import Response
from esi import (
    get_mail_headers, get_mail, get_mail_labels, get_mailing_lists,
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
