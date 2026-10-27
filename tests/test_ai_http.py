import socket

import pytest
from django.core.exceptions import ValidationError

from core.services import ai_http


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/responses",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/v1",
        "https://[::1]/v1",
        "https://api.openai.com:8443/v1",
        "https://api.openai.com.attacker.test/v1",
        "https://user:secret@api.openai.com/v1",
        "https://api.openai.com/v1?key=secret",
        "https://api.openai.com/v1#secret",
        "https://api.openai.com\\@127.0.0.1/v1",
        "https://api.openai.com/v1\r\nX: value",
    ],
)
def test_endpoint_rejects_untrusted_or_unsafe_url(url):
    with pytest.raises(ValidationError):
        ai_http.validate_endpoint(url)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1"],
)
def test_resolved_private_addresses_are_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(ValidationError):
        ai_http.public_addresses("api.openai.com")


@pytest.mark.parametrize("status", [200, 302, 401, 429])
def test_transport_is_pinned_and_never_follows_redirects(monkeypatch, status):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )
    seen = []

    class Response:
        def read(self, size):
            return b'{"ok":true}'

    response = Response()
    response.status = status

    class Connection:
        def __init__(self, host, address):
            seen.append((host, address))

        def request(self, method, path, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer test-private-key"

        def getresponse(self):
            return response

        def close(self):
            seen.append("closed")

    monkeypatch.setattr(ai_http, "PinnedHTTPSConnection", Connection)
    if status == 200:
        assert ai_http.post_json("https://api.openai.com/v1/responses", "test-private-key", {}) == {
            "ok": True
        }
    else:
        with pytest.raises(ValidationError) as error:
            ai_http.post_json("https://api.openai.com/v1/responses", "test-private-key", {})
        assert "test-private-key" not in str(error.value)
    assert seen == [("api.openai.com", "93.184.216.34"), "closed"]


def test_actual_connection_uses_validated_ip_and_original_tls_hostname(monkeypatch):
    seen = []

    class Sock:
        def close(self):
            pass

    raw = Sock()

    def connect(address, timeout):
        seen.append((address, timeout))
        return raw

    connection = ai_http.PinnedHTTPSConnection("api.openai.com", "93.184.216.34")

    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            assert sock is raw
            seen.append(server_hostname)
            return sock

    connection._context = Context()
    monkeypatch.setattr(socket, "create_connection", connect)
    connection.connect()
    connection.close()
    assert seen == [(("93.184.216.34", 443), 60), "api.openai.com"]
