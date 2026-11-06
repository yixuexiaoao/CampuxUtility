"""页面网络访问控制 (SSRF 防护) 单元测试，不依赖网络与浏览器"""
import ipaddress
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import netguard as G


class TestIpAllowed(unittest.TestCase):
    def test_private_and_special_blocked(self):
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                   "0.0.0.0", "100.64.0.1", "198.18.0.1", "224.0.0.1", "255.255.255.255",
                   "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::1"):
            self.assertFalse(G.ip_allowed(ip), ip)

    def test_public_allowed(self):
        for ip in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            self.assertTrue(G.ip_allowed(ip), ip)

    def test_invalid_blocked(self):
        self.assertFalse(G.ip_allowed("not-an-ip"))

    def test_allow_nets(self):
        with mock.patch.object(G, "ALLOW_NETS", (ipaddress.ip_network("198.18.0.0/15"),)):
            self.assertTrue(G.ip_allowed("198.18.2.143"))
            self.assertFalse(G.ip_allowed("127.0.0.1"))


class TestUrlAllowed(unittest.IsolatedAsyncioTestCase):
    async def test_schemes(self):
        self.assertTrue(await G.url_allowed("data:image/png;base64,AAAA", "public"))
        self.assertTrue(await G.url_allowed("about:blank", "public"))
        for url in ("file:///etc/passwd", "chrome://settings", "ftp://8.8.8.8/x", "javascript:alert(1)"):
            self.assertFalse(await G.url_allowed(url, "public"), url)

    async def test_ip_literals(self):
        self.assertFalse(await G.url_allowed("http://127.0.0.1:8999/health", "public"))
        self.assertFalse(await G.url_allowed("http://[::ffff:127.0.0.1]/", "public"))
        self.assertFalse(await G.url_allowed("ws://169.254.169.254/", "public"))
        self.assertTrue(await G.url_allowed("https://8.8.8.8/", "public"))

    async def test_localhost_names_blocked_without_dns(self):
        with mock.patch.object(G, "_dns_cache", {}):
            self.assertFalse(await G.url_allowed("http://localhost:8999/", "public"))
            self.assertFalse(await G.url_allowed("http://app.localhost/", "public"))
            self.assertFalse(await G.url_allowed("http://LOCALHOST./", "public"))

    async def test_dns_result_checked(self):
        """域名只要有一个解析结果是内网地址就拦截；解析失败也拦截"""
        async def fake_getaddrinfo(host, port, **kw):
            table = {"pub.test": ["93.184.216.34"], "evil.test": ["93.184.216.34", "10.0.0.5"]}
            if host not in table:
                raise OSError("no such host")
            return [(2, 1, 6, "", (ip, port)) for ip in table[host]]

        loop = mock.Mock(getaddrinfo=fake_getaddrinfo)
        with mock.patch.object(G, "_dns_cache", {}), mock.patch.object(G.asyncio, "get_running_loop", return_value=loop):
            self.assertTrue(await G.url_allowed("https://pub.test/a.png", "public"))
            self.assertFalse(await G.url_allowed("https://evil.test/a.png", "public"))
            self.assertFalse(await G.url_allowed("https://missing.test/", "public"))

    async def test_policies(self):
        self.assertFalse(await G.url_allowed("https://8.8.8.8/", "none"))
        self.assertTrue(await G.url_allowed("data:text/plain,x", "none"))
        self.assertTrue(await G.url_allowed("http://127.0.0.1/", "all"))
        self.assertTrue(await G.url_allowed("file:///etc/passwd", "all"))


class TestInstall(unittest.IsolatedAsyncioTestCase):
    async def test_install_registers_routes(self):
        ctx = mock.Mock()
        ctx.route = mock.AsyncMock()
        ctx.route_web_socket = mock.AsyncMock()
        with mock.patch.object(G, "POLICY", "public"):
            await G.install(ctx)
        ctx.route.assert_awaited_once()
        ctx.route_web_socket.assert_awaited_once()

    async def test_install_skipped_for_all(self):
        ctx = mock.Mock()
        ctx.route = mock.AsyncMock()
        with mock.patch.object(G, "POLICY", "all"):
            await G.install(ctx)
        ctx.route.assert_not_awaited()

    async def test_guard_aborts_blocked_request(self):
        route = mock.Mock()
        route.request.url = "http://127.0.0.1:8999/health"
        route.continue_ = mock.AsyncMock()
        route.abort = mock.AsyncMock()
        with mock.patch.object(G, "POLICY", "public"):
            await G._guard_request(route)
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
