"""基于桩模块(stub)的端到端集成测试

不依赖真实 fastapi/playwright 安装，用可断言的 Fake 对象替换它们，
完整验证 render.py 与 api.py 的调用链、参数清洗、路由注册、错误处理。
"""

import os
import sys
import json
import unittest
import asyncio
import tempfile

# 桩浏览器输出的是假图片数据，测试调用链时关闭 Pillow 后处理 (后处理另有专门用例)
os.environ["T2I_OPTIMIZE"] = "0"

try:
    import io as _io
    from PIL import Image as _PILImage
    _buf = _io.BytesIO()
    _img = _PILImage.new("RGB", (120, 60), "white")
    for _x in range(10, 110):
        _img.putpixel((_x, 30), (50, 118, 220))
        _img.putpixel((_x, 31), (120, 160, 230))
    _img.save(_buf, "PNG")
    STUB_PNG = _buf.getvalue()
except ImportError:
    _PILImage = None
    STUB_PNG = b"\x89PNG\r\n\x1a\nFAKE"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# 1. Stub: playwright
# ---------------------------------------------------------------------------
class StubLocator:
    def __init__(self, selector):
        self.selector = selector

class StubPage:
    def __init__(self):
        self.screenshot_kwargs = None
        self.content = None
        self.url = None
        self.closed = False
        self.context = None
        self.goto_calls = []

    def is_closed(self):
        return self.closed

    async def set_content(self, content, **kwargs):
        self.content = content

    async def goto(self, url, **kwargs):
        self.url = url
        self.goto_calls.append(url)

    async def evaluate(self, expr):
        return None

    async def set_viewport_size(self, size):
        self.viewport_size = size

    def locator(self, selector):
        return StubLocator(selector)

    async def screenshot(self, path=None, **kwargs):
        self.screenshot_kwargs = kwargs
        # 写一个假的图片文件；type=png 时返回一张真实的小 PNG，供压缩用例使用
        data = STUB_PNG if kwargs.get("type") == "png" else b"\xff\xd8\xff\xe0FAKE_JPEG"
        if path:
            with open(path, "wb") as f:
                f.write(data)
        return data

    async def close(self):
        self.closed = True

class StubContext:
    def __init__(self):
        self.pages = []
        self.closed = False

    async def new_page(self):
        p = StubPage()
        p.context = self
        self.pages.append(p)
        return p

    async def close(self):
        self.closed = True

class StubBrowser:
    def __init__(self):
        self.context = StubContext()
        self.connected = True
        self.launch_args = None
        self.closed = False

    def is_connected(self):
        return self.connected

    async def new_context(self, **kwargs):
        return self.context

    async def close(self):
        self.closed = True

class StubChromium:
    async def launch(self, **kwargs):
        b = StubBrowser()
        b.launch_args = kwargs
        return b

class StubPlaywright:
    def __init__(self):
        self.chromium = StubChromium()
        self.stopped = False

    async def stop(self):
        self.stopped = True

class StubAsyncPlaywrightFactory:
    def __init__(self):
        self.started_playwright = None

    def __call__(self):
        return self

    async def start(self):
        self.started_playwright = StubPlaywright()
        return self.started_playwright

_async_playwright_factory = StubAsyncPlaywrightFactory()

# 构造 playwright 包
playwright_pkg = type(sys)("playwright")
playwright_async_api = type(sys)("playwright.async_api")
playwright_async_api.async_playwright = _async_playwright_factory
playwright_pkg.async_api = playwright_async_api
sys.modules["playwright"] = playwright_pkg
sys.modules["playwright.async_api"] = playwright_async_api

# ---------------------------------------------------------------------------
# 2. Stub: fastapi
# ---------------------------------------------------------------------------
class FakeFileResponse:
    def __init__(self, path, media_type=None, headers=None):
        self.path = path
        self.media_type = media_type
        self.headers = headers or {}

class FakeResponses:
    FileResponse = FakeFileResponse

class FakeRoute:
    def __init__(self, method, path, func):
        self.method = method
        self.path = path
        self.func = func

class FakeApp:
    def __init__(self, **kwargs):
        self.routes = []
        self.kwargs = kwargs

    def get(self, path):
        def deco(f):
            self.routes.append(FakeRoute("GET", path, f))
            return f
        return deco

    def post(self, path):
        def deco(f):
            self.routes.append(FakeRoute("POST", path, f))
            return f
        return deco

class FakeRequest:
    def __init__(self, body_dict):
        self._body = body_dict

    async def json(self):
        return self._body

fastapi_pkg = type(sys)("fastapi")
fastapi_pkg.FastAPI = FakeApp
fastapi_pkg.Request = object
fastapi_pkg.responses = FakeResponses
sys.modules["fastapi"] = fastapi_pkg
sys.modules["fastapi.responses"] = FakeResponses

# 先删除可能已被注入 mock 的模块缓存
for mod in list(sys.modules):
    if mod in ("src.api", "src.render", "src.util"):
        sys.modules.pop(mod, None)

from src.render import Text2ImgRender
from src.api import app, text2img, text2img_image, MIME_MAP, cleanup_expired_files


class TestIntegrationStub(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.render = Text2ImgRender()

    def test_route_registration(self):
        """验证路由注册：健康检查 + 三个 GET 别名 + 一个 POST"""
        methods = {(r.method, r.path) for r in app.routes}
        self.assertIn(("GET", "/health"), methods)
        self.assertIn(("GET", "/ping"), methods)
        self.assertIn(("GET", "/text2img/data/{id:path}"), methods)
        self.assertIn(("GET", "/text2img/{id:path}"), methods)
        self.assertIn(("GET", "/data/{id:path}"), methods)
        self.assertIn(("POST", "/text2img/generate"), methods)

    async def test_html2pic_end_to_end_with_stub_browser(self):
        """验证 html2pic 完整调用链：init_browser -> set_content -> screenshot -> 返回路径"""
        html = await self.render.from_html("中文测试")
        result = await self.render.html2pic(html, {"type": "jpeg"})

        # 返回 data/ 下的相对路径
        self.assertTrue(result.startswith("data" + os.sep) or result.startswith("data/"))
        self.assertTrue(result.endswith(".jpeg"))
        self.assertTrue(os.path.exists(result))

        # 验证截图参数：默认 full_page + quality
        page = self.render.browser.context.pages[0]
        self.assertEqual(page.screenshot_kwargs.get("full_page"), True)
        self.assertEqual(page.screenshot_kwargs.get("quality"), 92)
        self.assertIn("中文测试", page.content or "")
        # 页面被重置为空白页并放回页面池，而不是直接销毁
        self.assertEqual(page.goto_calls[-1], "about:blank")
        self.assertIn(page, self.render._idle_pages)
        # 整页截图使用贴合内容的小视口高度，短内容不再留白
        self.assertEqual(page.viewport_size["height"], Text2ImgRender.FIT_VIEWPORT_HEIGHT)

        # 清理
        os.remove(result)

    async def test_html2pic_clip_mutual_exclusion(self):
        """验证传 clip 时 screenshot 收到的参数不含 full_page"""
        clip = {"x": 0, "y": 0, "width": 100, "height": 100}
        result = await self.render.html2pic("<p>hello</p>", {"clip": clip})

        browser = self.render.browser
        page = browser.context.pages[-1]
        self.assertNotIn("full_page", page.screenshot_kwargs)
        self.assertEqual(page.screenshot_kwargs["clip"], clip)
        os.remove(result)

    async def test_html2pic_png_drops_quality(self):
        """验证 PNG 截图时 quality 被剔除、可传 omit_background"""
        result = await self.render.html2pic(
            "<p>hello</p>",
            {"type": "png", "quality": 90, "omit_background": True},
        )
        browser = self.render.browser
        page = browser.context.pages[-1]
        self.assertNotIn("quality", page.screenshot_kwargs)
        self.assertEqual(page.screenshot_kwargs["omit_background"], True)
        self.assertTrue(result.endswith(".png"))
        os.remove(result)

    async def test_html2pic_camel_case_alias(self):
        """验证 camelCase 参数 fullPage/omitBackground 被正确映射后传给 screenshot"""
        result = await self.render.html2pic(
            "<p>hello</p>",
            {"type": "png", "fullPage": False, "omitBackground": True},
        )
        browser = self.render.browser
        page = browser.context.pages[-1]
        self.assertEqual(page.screenshot_kwargs.get("full_page"), False)
        self.assertEqual(page.screenshot_kwargs.get("omit_background"), True)
        os.remove(result)

    async def test_html2pic_mask_selector_to_locator(self):
        """验证 mask 选择器字符串被转换为 locator"""
        result = await self.render.html2pic(
            "<p>hello</p>",
            {"mask": [".secret"]},
        )
        browser = self.render.browser
        page = browser.context.pages[-1]
        masks = page.screenshot_kwargs.get("mask")
        self.assertIsInstance(masks, list)
        self.assertIsInstance(masks[0], StubLocator)
        self.assertEqual(masks[0].selector, ".secret")
        os.remove(result)

    async def test_generate_api_json_mode(self):
        """验证 POST /text2img/generate json 模式返回纯文件 id"""
        req = FakeRequest({"html": "<p>hello</p>", "json": True})
        resp = await text2img(req)
        self.assertEqual(resp.code, 0)
        fid = resp.data["id"]
        self.assertNotIn("data", fid)
        self.assertNotIn("/", fid)
        self.assertTrue(fid.startswith("rendered_"))
        self.assertTrue(fid.endswith(".jpeg"))
        # 文件真实存在
        self.assertTrue(os.path.exists(os.path.join("data", fid)))
        os.remove(os.path.join("data", fid))

    async def test_generate_api_file_mode_media_type(self):
        """验证 POST 直接返回 FileResponse 且 media_type 正确"""
        req = FakeRequest({"html": "<p>hello</p>", "options": {"type": "png"}})
        resp = await text2img(req)
        self.assertIsInstance(resp, FakeFileResponse)
        self.assertEqual(resp.media_type, "image/png")
        from src.version import __version__
        self.assertEqual(resp.headers.get("X-T2I-Version"), __version__)
        self.assertTrue(os.path.exists(resp.path))
        os.remove(resp.path)

    async def test_generate_api_tmplname_path_traversal(self):
        """验证 tmplname 路径穿越被防御：返回 template not found 而非越权读取"""
        req = FakeRequest({"tmplname": "../../../main", "tmpldata": {}})
        resp = await text2img(req)
        self.assertEqual(resp.code, 1)
        self.assertIn("not found", resp.message)

    async def test_generate_api_missing_content(self):
        """验证缺少 html/tmpl 时返回错误"""
        req = FakeRequest({"json": True})
        resp = await text2img(req)
        self.assertEqual(resp.code, 1)
        self.assertIn("html or tmpl not found", resp.message)

    async def test_generate_api_invalid_json(self):
        """验证非法 JSON body 返回错误"""
        class BadRequest:
            async def json(self):
                raise json.JSONDecodeError("bad", "doc", 0)
        resp = await text2img(BadRequest())
        self.assertEqual(resp.code, 1)
        self.assertIn("invalid json body", resp.message)

    async def test_generate_api_options_not_dict_graceful(self):
        """验证 options 非 dict 时优雅降级为默认选项，不崩溃"""
        class BadRequest:
            async def json(self):
                return {"html": "<p>ok</p>", "options": "not-a-dict", "json": True}
        resp = await text2img(BadRequest())
        # 非 dict options 被忽略并降级为默认，正常返回成功
        self.assertEqual(resp.code, 0)
        fid = resp.data["id"]
        os.remove(os.path.join("data", fid))

    async def test_get_image_api(self):
        """验证 GET 接口：存在的文件返回 FileResponse，不存在返回 Result"""
        # 准备一个测试文件
        os.makedirs("data", exist_ok=True)
        test_file = os.path.join("data", "rendered_test_ab12cd34.jpeg")
        with open(test_file, "wb") as f:
            f.write(b"\xff\xd8FAKE")

        resp_ok = await text2img_image("rendered_test_ab12cd34.jpeg")
        self.assertIsInstance(resp_ok, FakeFileResponse)
        self.assertEqual(resp_ok.media_type, "image/jpeg")

        # 带路径形式（旧 id 格式兼容）
        resp_path = await text2img_image("data/rendered_test_ab12cd34.jpeg")
        self.assertIsInstance(resp_path, FakeFileResponse)
        self.assertEqual(resp_path.media_type, "image/jpeg")

        resp_missing = await text2img_image("rendered_not_exist_zz.jpeg")
        self.assertEqual(resp_missing.code, 1)

        os.remove(test_file)

    def test_cleanup_expired_files_only_rendered(self):
        """验证清理只删 rendered_ 前缀，且清理正常文件"""
        test_dir = os.path.join("data", "cleanup_check")
        os.makedirs(test_dir, exist_ok=True)
        try:
            old = os.path.join(test_dir, "rendered_old.jpeg")
            keep = os.path.join(test_dir, "preview_margin.jpg")  # 非 rendered 前缀
            with open(old, "w") as f:
                f.write("x")
            with open(keep, "w") as f:
                f.write("x")
            import time
            os.utime(old, (time.time() - 200000, time.time() - 200000))
            cleanup_expired_files(test_dir, max_age_seconds=86400)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(keep))
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)

    async def test_html2pic_custom_width(self):
        """验证传递 width 参数时动态调整 viewport 宽度"""
        result = await self.render.html2pic("<p>width test</p>", {"width": 600})
        browser = self.render.browser
        page = browser.context.pages[-1]
        self.assertEqual(page.viewport_size["width"], 600)
        os.remove(result)

    async def test_styled_template_keeps_browser_viewport(self):
        """自带样式的完整页面 (客户端模板) 保持默认视口高度，与浏览器预览一致"""
        html = await self.render.from_html("<!doctype html><html><head><style>body{min-height:100vh}</style></head><body><p>x</p></body></html>")
        result = await self.render.html2pic(html, {})
        page = self.render.browser.context.pages[-1]
        self.assertEqual(page.viewport_size["height"], 800)
        os.remove(result)

    async def test_page_pool_reuse(self):
        """验证顺序请求复用同一个页面，并发请求不超过 max_pages"""
        r = Text2ImgRender(max_pages=2)
        for _ in range(3):
            os.remove(await r.html2pic("<p>x</p>", {}))
        self.assertEqual(len(r.browser.context.pages), 1)
        results = await asyncio.gather(*[r.html2pic("<p>y</p>", {}) for _ in range(6)])
        for p in results:
            os.remove(p)
        self.assertLessEqual(len(r.browser.context.pages), 2)

    async def test_failed_render_page_not_reused(self):
        """验证截图失败的页面会被关闭而不是放回池中"""
        r = Text2ImgRender()
        await r.init_browser()
        page = await r.context.new_page()
        async def boom(path, **kw):
            raise RuntimeError("boom")
        page.screenshot = boom
        r._idle_pages.append(page)
        with self.assertRaises(RuntimeError):
            await r.html2pic("<p>x</p>", {})
        self.assertTrue(page.closed)
        self.assertNotIn(page, r._idle_pages)

    async def test_clip_keeps_default_viewport_height(self):
        """验证 clip 截图时保留默认视口高度"""
        result = await self.render.html2pic("<p>x</p>", {"clip": {"x": 0, "y": 0, "width": 10, "height": 10}})
        page = self.render.browser.context.pages[-1]
        self.assertEqual(page.viewport_size["height"], 800)
        os.remove(result)

    async def test_generate_api_backslash_traversal(self):
        """验证 Windows 风格 ..\\ 路径穿越在 Linux 上同样被拒绝"""
        resp = await text2img(FakeRequest({"tmplname": "..\\..\\main", "tmpldata": {}}))
        self.assertEqual(resp.code, 1)
        self.assertIn("not found", resp.message)

    async def test_generate_api_template_ssti_blocked(self):
        """验证用户模板运行在 Jinja2 沙箱中，无法访问 Python 内部对象"""
        req = FakeRequest({"tmpl": "{{ ''.__class__.__mro__[1].__subclasses__() }}", "json": True})
        resp = await text2img(req)
        self.assertEqual(resp.code, 1)
        self.assertIn("render error", resp.message)

    async def test_generate_api_tmpldata_not_dict(self):
        resp = await text2img(FakeRequest({"tmpl": "{{ a }}", "tmpldata": [1, 2]}))
        self.assertEqual(resp.code, 1)

    async def test_health_route(self):
        """验证 /health 接口返回状态与浏览器就绪标识"""
        from src.api import health
        resp = await health()
        self.assertEqual(resp.get("status"), "ok")
        self.assertIn("browser_ready", resp)
        from src.version import __version__
        self.assertEqual(resp["version"], __version__)
        for key in ("pillow", "numpy", "optimize", "compress", "png8_active"):
            self.assertIn(key, resp)

    async def test_lifespan_startup_and_shutdown(self):
        """验证 lifespan 上下文管理器的启动与退出流程"""
        from src.api import lifespan, app
        async with lifespan(app):
            pass


class TestImageEncode(unittest.TestCase):
    def test_encode_jpeg_and_png(self):
        """Pillow 后处理：JPEG 可解码、PNG 像素无损一致 (未安装 Pillow 时跳过)"""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import io
        from src.render import _encode_image
        im = Image.new("RGB", (200, 100), "white")
        im.putpixel((10, 10), (200, 30, 30))
        buf = io.BytesIO(); im.save(buf, "PNG")
        with tempfile.TemporaryDirectory() as d:
            j, p = os.path.join(d, "a.jpeg"), os.path.join(d, "a.png")
            _encode_image(buf.getvalue(), j, "jpeg", 86)
            _encode_image(buf.getvalue(), p, "png", 0)
            self.assertEqual(Image.open(j).size, (200, 100))
            self.assertEqual(Image.open(p).convert("RGB").getpixel((10, 10)), (200, 30, 30))


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(_PILImage is None, "Pillow not installed")
class TestPng8Compress(unittest.IsolatedAsyncioTestCase):
    """256 色 PNG 压缩 (需开启 Pillow 后处理)"""

    def setUp(self):
        from unittest import mock
        import src.render as render_mod
        self._patch = mock.patch.object(render_mod, "_OPTIMIZE_ENABLED", True)
        self._patch.start()
        self.render = Text2ImgRender()

    def tearDown(self):
        self._patch.stop()

    async def test_text_page_becomes_png8(self):
        result = await self.render.html2pic("<p>x</p>", {"type": "jpeg", "quality": 92})
        page = self.render.browser.context.pages[-1]
        self.assertEqual(page.screenshot_kwargs["type"], "png")
        self.assertTrue(result.endswith(".png"))
        with _PILImage.open(result) as im:
            self.assertEqual(im.mode, "P")
            self.assertEqual(im.size, (120, 60))
        os.remove(result)

    async def test_per_request_off_keeps_jpeg(self):
        result = await self.render.html2pic("<p>x</p>", {"type": "jpeg", "compress": "off"})
        self.assertTrue(result.endswith(".jpeg"))
        with _PILImage.open(result) as im:
            self.assertEqual(im.format, "JPEG")
        os.remove(result)

    async def test_transparent_png_not_palettized(self):
        result = await self.render.html2pic("<p>x</p>", {"type": "png", "omit_background": True})
        with _PILImage.open(result) as im:
            self.assertNotEqual(im.mode, "P")
        os.remove(result)

    def test_png8_exact_when_few_colors(self):
        import io
        from src.compress import to_png8
        data = to_png8(STUB_PNG)
        a = _PILImage.open(io.BytesIO(STUB_PNG)).convert("RGB")
        b = _PILImage.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_photo_like_image_falls_back(self):
        """颜色种类过多 (照片/渐变) 时返回 None，由调用方回退为 JPEG，避免色带"""
        import io, random
        from src.compress import to_png8
        random.seed(1)
        img = _PILImage.frombytes("RGB", (200, 200), bytes(random.randrange(256) for _ in range(200 * 200 * 3)))
        buf = io.BytesIO(); img.save(buf, "PNG")
        self.assertIsNone(to_png8(buf.getvalue()))

    def test_gradient_card_becomes_png8(self):
        """颜色超过旧阈值 16384 的渐变背景 + 文字：通过逐块画质检查，仍输出 256 色 PNG"""
        import io
        import numpy as np
        from src import compress
        h, w = 768, 1024
        y, x = np.mgrid[0:h, 0:w]
        a = np.stack([102 + x * 48 / w, 126 - y * 66 / h, 234 - (x + y) * 74 / (w + h)], -1)
        # Chromium 绘制渐变时会做 ±1 的抖动，使颜色种类远多于渐变本身的色阶数
        a = a + np.random.default_rng(2).integers(-1, 2, a.shape)
        a = np.clip(a, 0, 255).astype(np.uint8)
        a[100:110, 50:600] = (30, 30, 30)  # 模拟文字笔画
        img = _PILImage.fromarray(a)
        self.assertGreater(len(img.getcolors(1 << 24)), 16384)
        buf = io.BytesIO(); img.save(buf, "PNG")
        data, suffix = compress.encode(buf.getvalue(), "jpeg", 86)
        self.assertEqual(suffix, "png")
        out = np.asarray(_PILImage.open(io.BytesIO(data)).convert("RGB"), np.float32)
        mse = ((out - a.astype(np.float32)) ** 2).mean()
        self.assertGreater(10 * np.log10(65025 / mse), 40)

    def test_small_photo_in_text_page_falls_back(self):
        """大片白底中夹一小块照片：全局 PSNR 很高，但逐块检查会拦下，回退 JPEG 避免照片色阶断层"""
        import io
        import numpy as np
        from src import compress
        rng = np.random.default_rng(5)
        a = np.full((1024, 1024, 3), 255, np.uint8)
        a[400:528, 400:528] = rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)
        buf = io.BytesIO(); _PILImage.fromarray(a).save(buf, "PNG")
        self.assertIsNone(compress.to_png8(buf.getvalue()))
        data, suffix = compress.encode(buf.getvalue(), "jpeg", 86)
        self.assertEqual(suffix, "jpeg")

    def test_transparent_pixels_never_palettized(self):
        """带真实透明像素的图片即使颜色很少也不转调色板，避免丢失透明度"""
        import io
        from src import compress
        img = _PILImage.new("RGBA", (64, 64), (255, 255, 255, 0))
        img.putpixel((5, 5), (255, 0, 0, 255))
        buf = io.BytesIO(); img.save(buf, "PNG")
        data, suffix = compress.encode(buf.getvalue(), "png", 86)
        with _PILImage.open(io.BytesIO(data)) as out:
            self.assertEqual(out.mode, "RGBA")
            self.assertEqual(out.getpixel((0, 0))[3], 0)

    async def test_fast_screenshot_skipped_for_clip_and_mask(self):
        """clip / mask / 透明背景等非默认截图交给 Playwright，不走 CDP 快速截图"""
        page = await self.render._acquire_page()
        try:
            for opts in ({"type": "png", "clip": {"x": 0, "y": 0, "width": 10, "height": 10}},
                         {"type": "png", "full_page": True, "mask": []},
                         {"type": "png", "full_page": True, "omit_background": True},
                         {"type": "png", "full_page": True, "scale": "css"}):
                self.assertIsNone(await self.render._fast_screenshot(page, opts))
            self.assertEqual(self.render._fast_shot_failures, 0)
        finally:
            await self.render._release_page(page, True)

    async def test_fast_screenshot_failure_falls_back(self):
        """桩页面没有 CDP 会话：快速截图失败后回退 page.screenshot，连续失败后不再尝试"""
        for _ in range(4):
            result = await self.render.html2pic("<p>x</p>", {"type": "jpeg"})
            self.assertTrue(os.path.exists(result))
            os.remove(result)
        self.assertEqual(self.render._fast_shot_failures, self.render._FAST_SHOT_MAX_FAILURES)
