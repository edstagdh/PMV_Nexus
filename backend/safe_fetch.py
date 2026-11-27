"""Fetching a picture from a web address someone typed in (3.9.0: performer photos "From a
link"). The server makes the request, so it must not become a way into the server's own network
(SSRF) or a way to fill its disk:

  - only http:// and https://, no user:password@ in the address, ports 80 / 443 / 8080 / 8443;
  - the host's every address must be a public one: loopback, private (10/8, 172.16/12,
    192.168/16, fc00::/7), link-local (169.254/16 -- cloud metadata), carrier-grade NAT,
    multicast, reserved and unspecified addresses are refused;
  - the connection goes to the address that was checked (the name isn't looked up again by the
    HTTP client, so a DNS answer can't change between the check and the request); for https
    the certificate is still checked against the host name;
  - redirects are followed by hand, at most MAX_REDIRECTS, each one checked the same way;
  - no proxy from the environment, no cookies, nothing of the app's own credentials is sent;
  - at most MAX_BYTES are read (the declared length is checked first), within TIMEOUT seconds;
  - the answer must say it's a picture (image/*, or octet-stream), and the caller still decodes
    it with Pillow (images.to_webp) before anything is saved.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger

from .i18n import L

MAX_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 20.0
PORTS = {80, 443, 8080, 8443}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class FetchRefused(ValueError):
    """Shown to the person who typed the address."""


def _public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                                 or ip.is_private or ip.is_unspecified or (ip.version == 4 and ip in _CGNAT))


async def _check(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, checked address) -- FetchRefused when the address mustn't be used."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise FetchRefused(L("That isn't a web address")) from None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise FetchRefused(L("Only http:// and https:// addresses can be used"))
    if parts.username or parts.password:
        raise FetchRefused(L("Addresses with a user name or password in them aren't used"))
    host = parts.hostname
    if not host:
        raise FetchRefused(L("That isn't a web address"))
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise FetchRefused(L("That isn't a web address")) from None
    if port not in PORTS:
        raise FetchRefused(L("Only the usual web ports (80, 443, 8080, 8443) are used"))
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise FetchRefused(L("{host} wasn't found", host=host)) from None
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    if not addresses or not all(_public(a) for a in addresses):
        logger.warning("Photo from a link refused: {} points to a local or private address", host)
        raise FetchRefused(L("{host} points to a local or private network address -- only public web addresses are used", host=host))
    return scheme, host, port, str(addresses[0])


async def fetch_image(url: str) -> bytes:
    """The picture's bytes (not decoded yet). FetchRefused with a reason otherwise."""
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, ip = await _check(url)
        parts = urlsplit(url)
        target_host = f"[{ip}]" if ":" in ip else ip
        target = parts._replace(netloc=f"{target_host}:{port}").geturl()
        headers = {"Host": host if port in (80, 443) else f"{host}:{port}", "User-Agent": "PMV-Nexus",
                   "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT), follow_redirects=False, trust_env=False) as client:
                request = client.build_request("GET", target, headers=headers,
                                               extensions={"sni_hostname": host} if scheme == "https" else None)
                response = await client.send(request, stream=True)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FetchRefused(L("The address redirected nowhere"))
                        url = urljoin(url, location)
                        continue
                    if response.status_code >= 400:
                        raise FetchRefused(L("{host} answered {status}", host=host, status=response.status_code))
                    ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and not (ctype.startswith("image/") or ctype == "application/octet-stream"):
                        raise FetchRefused(L("That address isn't a picture ({type})", type=ctype))
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                        raise FetchRefused(L("The picture is bigger than {mb} MB", mb=MAX_BYTES // 1048576))
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            raise FetchRefused(L("The picture is bigger than {mb} MB", mb=MAX_BYTES // 1048576))
                    logger.info("Photo from a link: {} ({} KB)", host, len(data) // 1024)
                    return bytes(data)
                finally:
                    await response.aclose()
        except httpx.TimeoutException:
            raise FetchRefused(L("{host} didn't answer in time", host=host)) from None
        except httpx.HTTPError as exc:
            raise FetchRefused(L("{host} couldn't be reached ({error})", host=host, error=type(exc).__name__)) from None
    raise FetchRefused(L("Too many redirects"))
