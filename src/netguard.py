"""渲染页面的网络访问控制：防止通过提交的 HTML 探测/访问内网 (SSRF)

接口会在 Chromium 中渲染调用方提交的任意 HTML，页面里的 <img>/<link>/fetch/iframe 等会由服务端发起请求。
不加限制时，调用方可以借此访问服务所在内网的地址 (如 127.0.0.1 上的管理端口、云厂商元数据 169.254.169.254)，
并把结果渲染进图片带出来。

策略 (环境变量 T2I_NETWORK)：
- public (默认)：只允许访问公网地址的 http/https/ws/wss；内网、回环、链路本地、保留地址一律拦截。
  域名会先解析，只要有一个解析结果不是公网地址就拦截。
- none：拦截所有网络请求，只能使用内联资源 (data: URI、内联 <style>/<svg>)，最安全。
- all ：不做任何拦截 (2.3.0 及以前的行为，仅限可信调用方)。

T2I_NETWORK_ALLOW：额外放行的网段 (逗号分隔的 CIDR)，仅在 public 策略下生效。
例如服务部署在 fake-ip 模式的透明代理后面时，公网域名会被解析到 198.18.0.0/15，需要设为该网段。

局限：解析与 Chromium 实际连接是两次独立的 DNS 查询，理论上存在 DNS rebinding 绕过的窗口；
对安全要求高的部署请用 none，或在容器网络层面隔离内网。
注意：Playwright 开启请求拦截后会停用 HTTP 缓存，模板引用外部字体/图片时每次都会重新下载。
"""
import asyncio
import ipaddress
import logging
import os
import socket
import time
from urllib.parse import urlsplit

POLICY_PUBLIC = "public"
POLICY_NONE = "none"
POLICY_ALL = "all"
_POLICIES = (POLICY_PUBLIC, POLICY_NONE, POLICY_ALL)

_NETWORK_SCHEMES = ("http", "https", "ws", "wss")
_DNS_TIMEOUT = 3.0
_DNS_CACHE_TTL = 60.0
_DNS_CACHE_MAX = 1024


def _read_policy() -> str:
    policy = os.getenv("T2I_NETWORK", POLICY_PUBLIC).strip().lower()
    if policy not in _POLICIES:
        logging.warning(f"T2I_NETWORK={policy!r} 无效，按 {POLICY_PUBLIC!r} 处理")
        return POLICY_PUBLIC
    return policy


def _read_allow_nets() -> tuple:
    nets = []
    for item in os.getenv("T2I_NETWORK_ALLOW", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logging.warning(f"T2I_NETWORK_ALLOW 中的网段 {item!r} 无效，已忽略")
    return tuple(nets)


POLICY = _read_policy()
ALLOW_NETS = _read_allow_nets()

# host -> (过期时间, 是否允许)
_dns_cache: dict[str, tuple[float, bool]] = {}


def ip_allowed(ip: str) -> bool:
    """只有公网单播地址允许访问 (IPv4 映射/内嵌的 IPv6 地址按其 IPv4 判断)"""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        inner = addr.ipv4_mapped or addr.sixtofour
        if inner is not None:
            addr = inner
    if any(addr in net for net in ALLOW_NETS):
        return True
    return addr.is_global and not addr.is_multicast


async def _host_allowed(host: str, port: int) -> bool:
    try:
        return ip_allowed(str(ipaddress.ip_address(host)))
    except ValueError:
        pass  # 不是 IP 字面量，按域名解析
    if host == "localhost" or host.endswith(".localhost"):
        return False
    now = time.monotonic()
    cached = _dns_cache.get(host)
    if cached and cached[0] > now:
        return cached[1]
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), _DNS_TIMEOUT)
        allowed = bool(infos) and all(ip_allowed(info[4][0]) for info in infos)
    except Exception:
        allowed = False  # 解析失败/超时：Chromium 本来也连不上，直接拦截
    if len(_dns_cache) >= _DNS_CACHE_MAX:
        _dns_cache.clear()
    _dns_cache[host] = (now + _DNS_CACHE_TTL, allowed)
    return allowed


async def url_allowed(url: str, policy: str | None = None) -> bool:
    policy = policy or POLICY
    if policy == POLICY_ALL:
        return True
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in ("data", "blob", "about"):
            return True
        if scheme not in _NETWORK_SCHEMES or policy == POLICY_NONE:
            return False  # file:// chrome:// ftp:// 等一律拒绝
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            return False
        port = parts.port or (443 if scheme in ("https", "wss") else 80)
    except ValueError:
        return False
    return await _host_allowed(host, port)


async def _guard_request(route):
    url = route.request.url
    if await url_allowed(url):
        await route.continue_()
    else:
        logging.info(f"[t2i] 已拦截页面网络请求: {url[:200]}")
        await route.abort("blockedbyclient")


async def _guard_websocket(ws):
    if await url_allowed(ws.url):
        ws.connect_to_server()
    else:
        logging.info(f"[t2i] 已拦截页面 WebSocket: {ws.url[:200]}")
        await ws.close()


async def install(context) -> None:
    """给浏览器上下文挂上拦截规则；策略为 all 或上下文不支持拦截时不做处理"""
    if POLICY == POLICY_ALL or not hasattr(context, "route"):
        return
    await context.route("**/*", _guard_request)
    if hasattr(context, "route_web_socket"):  # Playwright >= 1.48
        await context.route_web_socket("**/*", _guard_websocket)
