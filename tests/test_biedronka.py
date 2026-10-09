import datetime as dt
import tempfile
from unittest.mock import Mock, patch

import requests

import receipts.biedronka as biedronka
from receipts.biedronka import list_receipts, month_ranges, receipt_date_hint, receipt_pdf


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.url = "https://moja.biedronka.pl/panel/ajax/paragons"

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs["data"]))
        payload_key = (kwargs["data"]["begin_data"], kwargs["data"]["page"])
        if payload_key in self.payloads:
            return FakeResponse(self.payloads[payload_key])
        return FakeResponse(self.payloads[kwargs["data"]["page"]])


class FakeDownloadResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code
        self.url = "https://moja.biedronka.pl/panel/download"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeDownloadSession:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, *, headers, timeout):
        assert headers["Accept"] == "application/pdf"
        assert timeout > 0
        self.urls.append(url)
        return self.responses[len(self.urls) - 1]


def test_month_ranges_cover_partial_months_and_leap_february():
    ranges = list(month_ranges(dt.date(2024, 1, 15), dt.date(2024, 3, 2)))
    assert ranges == [
        (dt.date(2024, 1, 15), dt.date(2024, 1, 31)),
        (dt.date(2024, 2, 1), dt.date(2024, 2, 29)),
        (dt.date(2024, 3, 1), dt.date(2024, 3, 2)),
    ]


def test_receipt_date_hint_from_polish_page_label():
    assert receipt_date_hint("2 października 2026\n6,49 zł") == "2026-10-02"
    assert receipt_date_hint("25.08.2025 07:53") == "2025-08-25"
    assert receipt_date_hint("Pobierz e-paragon") is None


def test_session_from_extension_restricts_cookie_domains():
    cookies = [{
        "name": "auth", "value": "opaque", "domain": ".moja.biedronka.pl",
        "path": "/", "expires": None, "secure": True,
    }]
    session = biedronka.session_from_extension("Chrome test", cookies)
    assert session.headers["User-Agent"] == "Chrome test"
    assert len(session.cookies) == 1
    try:
        biedronka.session_from_extension("Chrome test", [
            {**cookies[0], "domain": ".example.com"},
        ])
    except ValueError:
        pass
    else:
        raise AssertionError("non-Biedronka cookie domain was accepted")


def test_list_receipts_uses_date_range_and_paginates():
    session = FakeSession({
        ("2023-01-15", 0): {"NumberOfPages": 0, "Receipts": []},
        ("2023-02-01", 0): {"NumberOfPages": 2, "Receipts": [
            {"Id": "2610022477166169", "DateTime": "2023-02-28T21:37:13+02:00"},
        ]},
        ("2023-02-01", 1): {"NumberOfPages": 2, "Receipts": [
            {"Id": "2609262601161712", "DateTime": "2023-02-01T14:55:38+02:00"},
        ]},
    })
    receipts = list_receipts(session, dt.date(2023, 1, 15), dt.date(2023, 2, 28))
    assert [receipt["Id"] for receipt in receipts] == ["2609262601161712", "2610022477166169"]
    assert [call[1]["page"] for call in session.calls] == [0, 0, 1]
    assert [(call[1]["begin_data"], call[1]["end_data"]) for call in session.calls] == [
        ("2023-01-15", "2023-01-31"),
        ("2023-02-01", "2023-02-28"),
        ("2023-02-01", "2023-02-28"),
    ]
    assert all(call[1]["short"] == "0" for call in session.calls)


def test_list_receipts_handles_empty_history():
    session = FakeSession({
        ("2023-01-01", 0): {"NumberOfPages": 0, "Receipts": []},
    })
    assert list_receipts(session, dt.date(2023, 1, 1), dt.date(2023, 1, 31)) == []
    assert len(session.calls) == 1


def test_receipt_pdf_uses_transaction_pdf_when_electronic_pdf_is_unavailable():
    session = FakeDownloadSession([
        FakeDownloadResponse(b'<script>alert("file unavailable")</script>'),
        FakeDownloadResponse(b"%PDF-transaction"),
    ])
    pdf = receipt_pdf(session, {"Id": "2503175925023336", "IsElectronicPrintoutRequested": True})
    assert pdf == b"%PDF-transaction"
    assert session.urls == [
        "https://moja.biedronka.pl/panel/download/pdf/2503175925023336",
        "https://moja.biedronka.pl/panel/download/2503175925023336",
    ]


def test_saved_session_preserves_cookie_scope_and_user_agent():
    with tempfile.TemporaryDirectory() as directory:
        session_path = biedronka.Path(directory) / "session.json"
        original = requests.Session()
        original.headers["User-Agent"] = "Biedronka test browser"
        original.cookies.set(
            "auth-cookie", "test-cookie-value", domain="moja.biedronka.pl", path="/panel", secure=True
        )
        with patch.object(biedronka, "SESSION", session_path):
            biedronka._save_session(original)
            restored = biedronka._load_session()

    assert restored is not None
    assert restored.headers["User-Agent"] == "Biedronka test browser"
    cookie = next(iter(restored.cookies))
    assert (cookie.name, cookie.value, cookie.domain, cookie.path, cookie.secure) == (
        "auth-cookie", "test-cookie-value", "moja.biedronka.pl", "/panel", True
    )


def test_sync_clamps_history_start_to_2023():
    requested = []
    session = object()

    def fake_list_receipts(actual_session, start, end):
        assert actual_session is session
        requested.append((start, end))
        return []

    with (
        patch.object(biedronka, "_load_session", return_value=session),
        patch.object(biedronka, "_save_session"),
        patch.object(biedronka, "list_receipts", side_effect=fake_list_receipts),
        patch.object(biedronka, "download_receipts", return_value=[]),
    ):
        biedronka.sync(dt.date(2020, 1, 1), dt.date(2023, 1, 1))
    assert requested == [(dt.date(2023, 1, 1), dt.date(2023, 1, 1))]


def test_session_transfer_requires_and_consumes_one_time_pairing():
    from app import server

    with server.BIEDRONKA_PAIRING_LOCK:
        previous_pairing = server.BIEDRONKA_PAIRING
        server.BIEDRONKA_PAIRING = None
    try:
        client = server.app.test_client()
        extension_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
        preflight = client.options(
            "/api/biedronka/session-transfer",
            headers={
                "Origin": extension_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            base_url="http://127.0.0.1:8765",
        )
        assert preflight.headers["Access-Control-Allow-Origin"] == extension_origin
        blocked_preflight = client.options(
            "/api/biedronka/session-transfer",
            headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
            base_url="http://127.0.0.1:8765",
        )
        assert "Access-Control-Allow-Origin" not in blocked_preflight.headers
        pairing_response = client.post(
            "/api/biedronka/session-transfer/start",
            json={},
            headers={"Origin": extension_origin},
            base_url="http://127.0.0.1:8765",
        )
        assert pairing_response.status_code == 200
        assert pairing_response.headers["Access-Control-Allow-Origin"] == extension_origin
        token = pairing_response.get_json()["token"]
        cookies = [{
            "name": "auth", "value": "opaque", "domain": ".moja.biedronka.pl",
            "path": "/", "expires": None, "secure": True,
        }]
        with (
            patch.object(biedronka, "verify_session"),
            patch.object(biedronka, "_save_session"),
        ):
            transferred = client.post(
                "/api/biedronka/session-transfer",
                json={"token": token, "user_agent": "Chrome test", "cookies": cookies},
                headers={"Origin": extension_origin},
                base_url="http://127.0.0.1:8765",
            )
            replayed = client.post(
                "/api/biedronka/session-transfer",
                json={"token": token, "user_agent": "Chrome test", "cookies": cookies},
                base_url="http://127.0.0.1:8765",
            )
        assert transferred.status_code == 200
        assert transferred.get_json()["cookies"] == 1
        assert replayed.status_code == 403
    finally:
        with server.BIEDRONKA_PAIRING_LOCK:
            server.BIEDRONKA_PAIRING = previous_pairing


def test_session_status_distinguishes_missing_valid_expired_and_unavailable():
    from app import server
    from requests import Timeout

    client = server.app.test_client()
    base_url = "http://127.0.0.1:8765"
    session_file = Mock()
    session_file.exists.return_value = False
    with patch.object(biedronka, "SESSION", session_file):
        assert client.get("/api/biedronka/session-status", base_url=base_url).get_json()["status"] == "missing"

    session = requests.Session()
    session_file.exists.return_value = True
    with (
        patch.object(biedronka, "SESSION", session_file),
        patch.object(biedronka, "_load_session", return_value=session),
        patch.object(biedronka, "verify_session"),
    ):
        assert client.get("/api/biedronka/session-status", base_url=base_url).get_json()["status"] == "valid"

    with (
        patch.object(biedronka, "SESSION", session_file),
        patch.object(biedronka, "_load_session", return_value=session),
        patch.object(biedronka, "verify_session", side_effect=biedronka.BiedronkaSessionExpired),
    ):
        assert client.get("/api/biedronka/session-status", base_url=base_url).get_json()["status"] == "expired"

    with (
        patch.object(biedronka, "SESSION", session_file),
        patch.object(biedronka, "_load_session", return_value=session),
        patch.object(biedronka, "verify_session", side_effect=Timeout),
    ):
        assert client.get("/api/biedronka/session-status", base_url=base_url).get_json()["status"] == "unavailable"


def test_biedronka_is_registered_as_an_update_source():
        from core import update

        assert any(key == "biedronka" for key, _, _ in update.STEPS)
        assert "biedronka" in update.NETWORK
