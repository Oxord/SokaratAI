"""Безопасные HTTP-запросы к пользовательским/внешним URL (защита от SSRF)."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

_MAX_REDIRECTS = 3


def is_public_http_url(url: str) -> bool:
    """True только для http(s)-адресов, все IP которых публичные."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not addr.is_global:
            return False
    return True


def safe_get(url: str, *, headers: dict | None = None, timeout: float = 10.0) -> requests.Response:
    """GET с ручной обработкой редиректов: каждый хоп проверяется на публичность.

    Бросает requests.RequestException, если адрес или редирект ведёт во внутреннюю сеть.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not is_public_http_url(current):
            raise requests.RequestException("адрес недоступен (не публичный http/https)")
        resp = requests.get(
            current, headers=headers, timeout=timeout, allow_redirects=False
        )
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise requests.RequestException("слишком много редиректов")
