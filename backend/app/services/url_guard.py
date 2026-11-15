"""出站 URL 安全校验（SSRF 防护）。

BYOK 允许用户填写自定义 base_url，后端会「带着用户的 Key 主动请求该地址」。
如果不校验，攻击者可以填 `http://169.254.169.254/latest/meta-data/` 探云元数据、
填内网地址扫内网、填 `file://` 读本地文件。

本模块只做「目标地址是否允许出站」的判断，不做任何网络请求。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# 保留主机名：即便解析不到 IP，名字本身就代表内网/元数据语义
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.com",
    "instance-data",
    "kubernetes",
    "kubernetes.default",
    "kubernetes.default.svc",
})

# 保留域名后缀
_BLOCKED_SUFFIXES = (".internal", ".local", ".localdomain", ".corp", ".lan", ".intranet")

# 常见云厂商元数据 IP（其实都在 link-local / private 段内，列出来便于错误提示）
_METADATA_IPS = frozenset({
    "169.254.169.254",
    "169.254.170.2",
    "100.100.100.200",
})


class UnsafeUrlError(ValueError):
    """目标地址被安全策略拒绝。"""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _check_host(host: str, *, resolve_hostnames: bool) -> None:
    lowered = host.lower().strip(".")
    if lowered in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"禁止访问保留主机名: {host}")
    if lowered in _METADATA_IPS:
        raise UnsafeUrlError(f"禁止访问云元数据地址: {host}")
    if lowered.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrlError(f"禁止访问内网域名: {host}")

    literal_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal_ip = ipaddress.ip_address(lowered)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_blocked_ip(literal_ip):
            raise UnsafeUrlError(f"禁止访问内网/保留 IP: {host}")
        return

    if not resolve_hostnames:
        return

    try:
        infos = socket.getaddrinfo(lowered, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        # 解析不了就拒绝：服务端若连 DNS 都不可用，这个地址也调用不通，
        # 与其放行后失败，不如在保存时明确告诉用户。
        raise UnsafeUrlError(f"无法解析主机名: {host}") from exc

    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(resolved):
            raise UnsafeUrlError(f"主机名 {host} 解析到内网/保留 IP: {addr}")


def validate_outbound_url(
    url: str,
    *,
    allow_private: bool = False,
    resolve_hostnames: bool = True,
) -> str:
    """校验一个将被后端主动请求的 URL。

    Args:
        url: 待校验地址；空字符串直接放行（表示使用默认地址）。
        allow_private: 显式允许内网/保留地址（仅供本地调试，默认关闭）。
        resolve_hostnames: 是否对非字面 IP 的主机名做 DNS 解析并检查结果 IP。

    Returns:
        原样返回 url，方便链式调用。

    Raises:
        UnsafeUrlError: 地址不安全。
    """
    if not url or not url.strip():
        return ""

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"仅允许 http/https 地址，收到: {scheme or '空 scheme'}")

    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeUrlError("地址缺少主机名")

    if allow_private:
        return url

    _check_host(host, resolve_hostnames=resolve_hostnames)
    return url
