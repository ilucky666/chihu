"""Restricted, DNS-pinned HTTPS transport for user-selected AI endpoints."""

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables


def validate_endpoint(url):
    try:
        if not url or any(ord(char) < 33 for char in url) or "\\" in url:
            raise ValueError
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in url
            or "#" in url
            or not host
            or host not in settings.AI_ALLOWED_HOSTS
            or not parsed.path.startswith("/")
        ):
            raise ValueError
        # Endpoint hostnames must be approved DNS names, never IP literals.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        return host, parsed.path
    except (ValueError, UnicodeError) as exc:
        raise ValidationError(
            "API 地址须为获准服务域名的完整 HTTPS 地址，端口为443，不能含账号、查询参数或片段。"
        ) from exc


def public_addresses(host):
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses:
            raise ValueError
        for value in addresses:
            address = ipaddress.ip_address(value)
            mapped = getattr(address, "ipv4_mapped", None)
            if (
                not address.is_global
                or address.is_multicast
                or address.is_reserved
                or (mapped and not mapped.is_global)
            ):
                raise ValueError
        return addresses
    except (OSError, ValueError) as exc:
        raise ValidationError("AI 服务域名无法安全解析为公网地址") from exc


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=60, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


@sensitive_variables("key", "payload", "body", "raw", "connection")
def post_json(url, key, payload):
    host, path = validate_endpoint(url)
    addresses = public_addresses(host)
    connection = PinnedHTTPSConnection(host, addresses[0])
    try:
        body = json.dumps(payload).encode("utf-8")
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        # http.client never follows redirects or reads environment proxy settings.
        if response.status != 200:
            hints = {
                400: "检查协议和模型是否支持图片",
                401: "API 密钥无效",
                403: "服务拒绝访问",
                429: "额度不足或请求过多",
            }
            raise ValidationError(
                f"AI 请求失败（HTTP {response.status}）：{hints.get(response.status, '服务未成功响应')}"
            )
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValidationError("AI 响应过大")
        return json.loads(raw)
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("AI 服务连接或响应无效，请检查个人设置") from exc
    finally:
        connection.close()
