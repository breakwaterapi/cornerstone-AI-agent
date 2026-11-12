import asyncio
import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

API_KEY = "KEY0123456789_breakwater_test"
FROM_NUMBER = "+13125550100"
TO_NUMBER = "+13125550199"
TEXT = "Hello from the breakwater test"


class _Recorder:
    def __init__(self):
        self.requests = []


def _make_server(recorder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                body = None
            recorder.requests.append(
                {
                    "method": "POST",
                    "path": self.path.split("?")[0],
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            if self.path.split("?")[0].rstrip("/") == "/v2/messages":
                payload = {
                    "data": {
                        "record_type": "message",
                        "direction": "outbound",
                        "id": "40385f64-5717-4562-b3fc-2c963f66afa6",
                        "type": "SMS",
                        "messaging_profile_id": "4001932a-e1e0-4a45-8e3e-bd0a1b3a5c11",
                        "from": {
                            "phone_number": FROM_NUMBER,
                            "carrier": "T-Mobile USA",
                            "line_type": "Wireless",
                        },
                        "to": [
                            {
                                "phone_number": TO_NUMBER,
                                "status": "queued",
                                "carrier": "T-Mobile USA",
                                "line_type": "Wireless",
                            }
                        ],
                        "text": TEXT,
                        "parts": 1,
                        "encoding": "GSM-7",
                        "created_at": "2024-01-01T10:00:00.000+00:00",
                        "completed_at": None,
                        "valid_until": "2024-01-01T11:00:00.000+00:00",
                        "errors": [],
                    }
                }
                status = 200
            else:
                payload = {"errors": [{"code": "10005", "title": "Resource not found"}]}
                status = 404
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def telnyx_server(monkeypatch):
    recorder = _Recorder()
    server, thread = _make_server(recorder)
    url = "http://127.0.0.1:%d" % server.server_address[1]

    monkeypatch.setenv("TELNYX_API_KEY", API_KEY)
    monkeypatch.setenv("TELNYX_PHONE_NUMBER", FROM_NUMBER)
    monkeypatch.setenv("TELNYX_API_BASE", url)
    monkeypatch.setenv("TELNYX_BASE_URL", url + "/v2")
    monkeypatch.setenv("TELNYX_MAX_RETRIES", "0")
    monkeypatch.syspath_prepend(SRC_DIR)

    import telnyx

    old_api_base = getattr(telnyx, "api_base", None)
    telnyx.api_base = url
    # make sure the repository module is imported fresh, with the config above
    sys.modules.pop("services.messaging.telnyx_service", None)
    try:
        yield recorder
    finally:
        sys.modules.pop("services.messaging.telnyx_service", None)
        if old_api_base is not None:
            telnyx.api_base = old_api_base
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_send_sms_reaches_telnyx_api_with_key(telnyx_server):
    module = importlib.import_module("services.messaging.telnyx_service")
    service = module.TelnyxService()

    result = asyncio.run(service.send_sms(TO_NUMBER, TEXT))

    assert result is True
    assert len(telnyx_server.requests) == 1
    request = telnyx_server.requests[0]
    assert request["path"] == "/v2/messages"
    assert request["authorization"] == "Bearer " + API_KEY
    assert request["body"]["from"] == FROM_NUMBER
    assert request["body"]["to"] == TO_NUMBER
    assert request["body"]["text"] == TEXT
