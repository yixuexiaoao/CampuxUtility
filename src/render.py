import logging, os, asyncio, re, base64, math
from functools import lru_cache
from .util import generate_data_path
from . import compress as _compress
from . import netguard as _netguard
from typing import TypedDict, Literal, Any

try:
    from jinja2.sandbox import SandboxedEnvironment
except ImportError:
    SandboxedEnvironment = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

try:
    from PIL import Image
except ImportError:
    Image = None

# Markdown 引擎优先级：markdown-it-py (CommonMark，兼容 LLM 常见写法) > Python-Markdown > 内置兜底解析器
try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None

try:
    import markdown
except ImportError:
    markdown = None

DEFAULT_CSS = """
<style>
    /* 基础重置与页面版面 (默认兜底样式，无强制标记，允许用户自定义覆盖) */
    * {
        box-sizing: border-box;
    }
    html {
        background-color: #ffffff;
    }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji";
        font-size: 20px;
        line-height: 1.75;
        color: #1f2937;
        background-color: #ffffff;
        padding: 36px 40px;
        margin: 0;
        word-break: break-word;
        overflow-wrap: break-word;
        -webkit-font-smoothing: antialiased;
        text-rendering: optimizeLegibility;
    }

    /* 首尾元素外边距清零，保证上下留白对称 */
    body > *:first-child,
    body > *:first-child > *:first-child {
        margin-top: 0;
    }
    body > *:last-child,
    body > *:last-child > *:last-child {
        margin-bottom: 0;
    }

    /* 标题层级：靠字号、字重与“上大下小”的间距区分层级，让标题贴近它所属的内容 */
    h1, .t2i-h1 {
        font-size: 32px;
        font-weight: 800;
        line-height: 1.35;
        margin: 32px 0 16px 0;
        color: #0f172a;
        padding-bottom: 12px;
        border-bottom: 2px solid #e5e7eb;
    }
    h2, .t2i-h2 {
        font-size: 26px;
        font-weight: 700;
        line-height: 1.4;
        margin: 32px 0 12px 0;
        color: #0f172a;
    }
    h3, .t2i-h3 {
        font-size: 22px;
        font-weight: 700;
        line-height: 1.45;
        margin: 24px 0 8px 0;
        color: #1e293b;
    }
    h4, .t2i-h4, h5, h6 {
        font-size: 20px;
        font-weight: 700;
        line-height: 1.5;
        margin: 20px 0 6px 0;
        color: #1e293b;
    }

    /* 正文段落 */
    p {
        margin: 12px 0;
    }

    strong, b, .bold {
        color: #0f172a;
        font-weight: 700;
    }
    a {
        color: #2563eb;
        text-decoration: none;
    }
    del, s {
        color: #94a3b8;
    }
    img {
        max-width: 100%;
        height: auto;
    }

    /* 列表：紧凑条目，弱化序号颜色 */
    ul, ol {
        padding-left: 1.6em;
        margin: 12px 0;
    }
    li {
        margin: 4px 0;
    }
    li > p {
        margin: 0;
    }
    li > ul, li > ol {
        margin: 4px 0;
    }
    li::marker {
        color: #94a3b8;
    }

    /* 引用块：作为“结论/提示”卡片 */
    blockquote {
        margin: 16px 0;
        padding: 12px 20px;
        border-left: 4px solid #3b82f6;
        background-color: #eff6ff;
        color: #1e3a8a;
        border-radius: 0 8px 8px 0;
    }
    blockquote > * {
        margin: 6px 0;
    }
    blockquote > *:first-child { margin-top: 0; }
    blockquote > *:last-child { margin-bottom: 0; }
    blockquote strong, blockquote b {
        color: #1e3a8a;
    }

    /* 行内代码与代码块 */
    code {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Noto Sans Mono CJK SC", monospace;
        background-color: #f1f5f9;
        padding: 2px 6px;
        border-radius: 5px;
        font-size: 0.88em;
        color: #0f172a;
    }
    pre {
        margin: 16px 0;
        padding: 16px 20px;
        background-color: #0f172a;
        border-radius: 10px;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
    }
    pre code {
        display: block;
        padding: 0;
        background: transparent;
        color: #e2e8f0;
        font-size: 16px;
        line-height: 1.6;
        /* 优先在空格处换行，超长 token 才在字符间断开，避免右侧溢出 */
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        word-break: normal;
    }

    /* 表格：表头浅底、内容顶对齐、细分隔线 */
    table {
        width: 100%;
        font-size: 18px;
        border-collapse: separate;
        border-spacing: 0;
        margin: 16px 0;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        overflow: hidden;
    }
    th, td {
        padding: 10px 14px;
        border-bottom: 1px solid #e5e7eb;
        line-height: 1.6;
        text-align: left;
        vertical-align: top;
    }
    th {
        background-color: #f8fafc;
        font-weight: 700;
        color: #0f172a;
        white-space: nowrap;
    }
    tbody tr:nth-child(even) td {
        background-color: #fbfcfd;
    }
    tr:last-child td {
        border-bottom: none;
    }

    hr {
        border: none;
        border-top: 1px solid #e5e7eb;
        margin: 24px 0;
    }
</style>
"""

# ---------------------------------------------------------------------------
# 预编译正则 (避免每次请求重复编译)
# ---------------------------------------------------------------------------
_GREEN_COLOR_RE = re.compile(
    r'<(?:span|font)[^>]*color\s*[:=]\s*["\']?(?:green|#00ff00|#0f0|rgb\(\s*0\s*,\s*255\s*,\s*0\s*\)|#047857|#10b981)["\']?[^>]*>(.*?)</(?:span|font)>',
    re.IGNORECASE | re.DOTALL,
)
_GREEN_CLASS_RE = re.compile(
    r'<span[^>]*class=["\'][^"\']*(?:green|text-green|green-text)[^"\']*["\'][^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
# 只在文档开头识别完整 HTML，避免正文/代码块中出现 "<body>" 字样时被误判
_FULL_HTML_RE = re.compile(r'^\s*(?:<!--.*?-->\s*)*(?:<!DOCTYPE\s+html|<html[\s>]|<head[\s>]|<body[\s>])', re.IGNORECASE | re.DOTALL)
_HTML_HEADING_RE = re.compile(
    r'(?:<p[^>]*>|<div[^>]*>)\s*(?:<(?:span|font)[^>]*>)?\s*#+\s*(?:#+\s*)*(?:</(?:span|font)>)?\s*([^<\n\r]+?)\s*(?:</p>|</div>)',
    re.IGNORECASE,
)
_HEAD_OPEN_RE = re.compile(r'<head(?:\s[^>]*)?>', re.IGNORECASE)
_HTML_OPEN_RE = re.compile(r'<html(?:\s[^>]*)?>', re.IGNORECASE)
_HEAD_CLOSE_RE = re.compile(r'</head\s*>', re.IGNORECASE)
# 页面是否自带样式表 (<style> 或 <link rel="stylesheet">)
_OWN_STYLE_RE = re.compile(r'<style[\s>]|<link[^>]+rel=["\']?stylesheet', re.IGNORECASE)
# 由本服务排版的页面打上此标记：截图时高度贴合内容；自带样式的完整页面不打标记，按浏览器默认视口原样渲染
AUTOFIT_MARKER = '<meta name="t2i-autofit" content="1">'

_FENCE_RE = re.compile(r'^\s{0,3}(```|~~~)')
_MD_HEADING_RE = re.compile(r'^(#+\s*)+')
_LIST_ITEM_RE = re.compile(r'^\s{0,3}(?:[-*+•]|\d{1,9}[.)])\s+')
_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$')
# padding 只允许长度/百分比/calc 等安全字符，防止借此注入 CSS/HTML
_SAFE_PADDING_RE = re.compile(r'^[0-9a-zA-Z.%\s(),+\-*/]{1,64}$')


def _normalize_markdown(text: str) -> str:
    """兼容 LLM 常见的“不规范” Markdown 写法 (代码块内的内容保持原样)：

    - `#标题`、`## ## 标题` 等写法规范化为 `# 标题`，层级上限为 4；
    - 列表 / 表格紧跟在普通段落行之后 (缺少空行) 时补一个空行，使其能被识别；
    - 列表 / 表格之后紧跟的顶格普通文本行补空行，避免被吞进上一个列表项或表格行。
    """
    out: list[str] = []
    in_fence = False
    fence_mark = ""
    in_list = False
    in_table = False
    lines = text.split('\n')
    for i, line in enumerate(lines):
        fm = _FENCE_RE.match(line)
        if fm:
            if not in_fence:
                in_fence, fence_mark = True, fm.group(1)
            elif fm.group(1) == fence_mark:
                in_fence = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        stripped = line.strip()
        prev = out[-1] if out else ""
        prev_is_text = bool(prev.strip()) and not prev.lstrip().startswith(('|', '>', '#', '<')) and not _FENCE_RE.match(prev)

        if not stripped:
            in_list = in_table = False
        elif in_table and not stripped.startswith('|'):
            out.append('')
            in_table = False
        if not stripped:
            pass
        elif line.startswith('#'):
            hashes = _MD_HEADING_RE.match(line).group(0).count('#')
            line = '#' * max(1, min(4, hashes)) + ' ' + _MD_HEADING_RE.sub('', line, count=1)
            in_list = False
        elif _LIST_ITEM_RE.match(line):
            if prev_is_text and not in_list:
                out.append('')
            in_list = True
        elif stripped.startswith('|') and not in_table and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            if prev.strip():
                out.append('')
            in_list = False
            in_table = True
        elif in_list and not line[:1].isspace() and not in_table:
            # 顶格文本紧跟列表：视为新段落，而不是上一项的懒惰续行
            out.append('')
            in_list = False
        out.append(line)
    return '\n'.join(out)


@lru_cache(maxsize=1)
def _get_markdown_it():
    return MarkdownIt("commonmark", {"html": True, "breaks": True, "linkify": False}).enable(["table", "strikethrough"])


@lru_cache(maxsize=1)
def _get_jinja_env():
    if SandboxedEnvironment is None:
        raise RuntimeError("Jinja2 package is not installed.")
    # 沙箱环境：阻止 {{ ''.__class__.__mro__ ... }} 一类的模板注入执行任意代码
    return SandboxedEnvironment()


@lru_cache(maxsize=64)
def _compile_template(template: str):
    return _get_jinja_env().from_string(template)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 图片体积优化：浏览器输出无损 PNG，再由 Pillow 统一编码 (避免 JPEG 二次有损)
# T2I_OPTIMIZE=0 可关闭；T2I_JPEG_QUALITY 为未显式传 quality 时的默认 JPEG 质量
# ---------------------------------------------------------------------------
_OPTIMIZE_ENABLED = Image is not None and os.getenv("T2I_OPTIMIZE", "1").strip().lower() not in ("0", "false", "no", "off")
DEFAULT_JPEG_QUALITY = min(100, _env_int("T2I_JPEG_QUALITY", 86))
# 文字截图优先转 256 色 PNG (T2I_COMPRESS=auto|off，默认 auto)：清晰度不变，体积约为 JPEG q92 的 1/3；
# 颜色种类超过 T2I_COMPRESS_MAX_COLORS (照片/渐变) 时回退为上面的 JPEG/PNG 编码
DEFAULT_COMPRESS = os.getenv("T2I_COMPRESS", "auto").strip().lower()


def _env_scale(default: float = 2.0) -> float:
    """输出分辨率倍数 (T2I_SCALE)。图片宽度 = 920 × 倍数；聊天软件会重新编码图片时，体积大致与像素数成正比"""
    try:
        return min(3.0, max(1.0, float(os.getenv("T2I_SCALE", default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(os.getenv(name, default))))
    except (TypeError, ValueError):
        return default


DEFAULT_SCALE = _env_scale()
# 颜色种类上限：超过则一定不转 256 色 (照片)；未超过时还要通过逐块画质检查
COMPRESS_MAX_COLORS = _env_int("T2I_COMPRESS_MAX_COLORS", _compress.DEFAULT_MAX_COLORS)
# 256 色 PNG 的画质门槛：每个 64×64 块的 PSNR 下限 (dB)，越高越保守 (更多图片回退为 JPEG)
COMPRESS_MIN_TILE_PSNR = _env_float("T2I_MIN_TILE_PSNR", _compress.DEFAULT_MIN_TILE_PSNR, 0.0, 99.0)
# PNG zlib 压缩等级：9 比 6 小约 5%，单张多花约 0.15s
PNG_LEVEL = int(_env_float("T2I_PNG_LEVEL", _compress.DEFAULT_PNG_LEVEL, 0, 9))
# 截图走 CDP 的 optimizeForSpeed (像素与 Playwright 截图完全一致，耗时降低约 40%)；T2I_FAST_SHOT=0 关闭
_FAST_SHOT_ENABLED = os.getenv("T2I_FAST_SHOT", "1").strip().lower() not in ("0", "false", "no", "off")

# 截图前的页面准备：等价于 Playwright 的 animations="disabled" + caret="hide"，并返回 devicePixelRatio
# 与 Playwright 一样先等两帧：页面在 load 后的下一帧才加 class 触发的 transition/动画此时才会开始，
# 之后再统一结束；否则截到的是渐显动画的第一帧 (内容透明，图片空白)
_PREPARE_SHOT_JS = """async (disableAnimations) => {
    const settle = () => {
        if (!disableAnimations || !document.getAnimations) return;
        for (const a of document.getAnimations()) {
            try {
                const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : null;
                if (t && t.iterations === Infinity) a.cancel(); else a.finish();
            } catch (e) { try { a.cancel(); } catch (_) {} }
        }
    };
    // 兜底 100ms：极端情况下 rAF 不触发也不会卡住截图
    const frame = () => new Promise(r => { requestAnimationFrame(() => r()); setTimeout(r, 100); });
    settle();
    await frame(); await frame();
    settle();
    const s = document.createElement('style');
    s.textContent = '*{caret-color:transparent !important}';
    (document.head || document.documentElement).appendChild(s);
    await frame();
    settle();
    return window.devicePixelRatio || 1;
}"""


def _encode_image(png_bytes: bytes, dst: str, img_type: str, quality: int) -> None:
    """把浏览器给出的无损 PNG 编码为指定格式的文件 (不转 256 色)"""
    data, _ = _compress.encode(png_bytes, img_type, quality, png8=False, png_level=PNG_LEVEL)
    with open(dst, "wb") as f:
        f.write(data)


def _finalize_image(png_bytes: bytes, img_type: str, quality: int, use_png8: bool) -> str:
    """解码一次 -> 编码 (优先 256 色 PNG) -> 落盘，整段在线程中执行，返回文件路径"""
    data, suffix = _compress.encode(png_bytes, img_type, quality, png8=use_png8,
                                    max_colors=COMPRESS_MAX_COLORS, min_tile_psnr=COMPRESS_MIN_TILE_PSNR,
                                    png_level=PNG_LEVEL)
    path, _ = generate_data_path(suffix=suffix, namespace="rendered")
    try:
        with open(path, "wb") as f:
            f.write(data)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


class ScreenshotOptions(TypedDict, total=False):
    """Playwright 截图参数"""
    timeout: float | None
    type: Literal["jpeg", "png", None]
    quality: int | None
    omit_background: bool | None
    full_page: bool | None
    clip: dict | None
    animations: Literal["allow", "disabled", None]
    caret: Literal["hide", "initial", None]
    scale: Literal["css", "device", None]
    mask: list[Any] | None
    width: int | None
    compress: Literal["auto", "off", None]


class Text2ImgRender:
    """HTML/Markdown 转图片渲染器 (页面池 + 并发限流)"""

    # full_page 截图时使用的极小视口高度：页面高度完全由内容决定，短内容不再留出大片空白
    FIT_VIEWPORT_HEIGHT = 64

    def __init__(self, max_pages: int | None = None):
        self._lock = asyncio.Lock()
        self.playwright = None
        self.browser = None
        self.context = None
        # 容器内以默认 seccomp 运行时 Chromium 沙箱通常无法启动，故默认关闭；
        # 宿主允许 user namespace 时可设 T2I_CHROMIUM_SANDBOX=1 开启沙箱，进一步隔离渲染进程
        sandbox_args = [] if _env_flag("T2I_CHROMIUM_SANDBOX", False) else ['--no-sandbox', '--disable-setuid-sandbox']
        self.browser_args = [
            '--disable-dev-shm-usage',
            '--disable-gpu',
            *sandbox_args,
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-breakpad',
            '--disable-component-update',
            '--disable-domain-reliability',
            '--disable-sync',
            '--disable-translate',
            '--mute-audio',
            '--no-first-run',
            '--disable-blink-features=AutomationControlled',
        ]
        self.browser_timeout = 30000
        self.page_timeout = 30000
        self.viewport = {'width': 920, 'height': 800}
        # 同时渲染的页面数上限 (环境变量 T2I_MAX_PAGES，默认 4)，防止突发并发把内存打满
        self.max_pages = max_pages or _env_int("T2I_MAX_PAGES", 4)
        self._page_sem = asyncio.Semaphore(self.max_pages)
        self._idle_pages: list = []
        self._fast_shot_failures = 0

    async def init_browser(self):
        """预热并初始化浏览器上下文 (并发安全加锁)"""
        if async_playwright is None:
            raise RuntimeError("Playwright package is not installed.")

        if self._is_browser_ready():
            return

        async with self._lock:
            if self._is_browser_ready():
                return

            # 清理旧的不健康资源
            await self._cleanup_internal()

            try:
                if self.playwright is None:
                    self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    args=self.browser_args,
                    timeout=self.browser_timeout
                )
                self.context = await self.browser.new_context(
                    viewport=self.viewport,
                    device_scale_factor=DEFAULT_SCALE
                )
                self._fast_shot_failures = 0  # 浏览器重建后重新允许快速截图
                await _netguard.install(self.context)
            except Exception as e:
                logging.error(f"初始化浏览器实例失败: {e}")
                await self._cleanup_internal()
                raise

    def _is_browser_ready(self) -> bool:
        """检查浏览器与上下文是否处于就绪状态"""
        return (
            self.playwright is not None
            and self.browser is not None
            and self.browser.is_connected()
            and self.context is not None
        )

    async def _cleanup_internal(self):
        """内部静默释放资源"""
        self._idle_pages.clear()
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

    async def close(self):
        """显式释放浏览器全部资源"""
        async with self._lock:
            await self._cleanup_internal()

    # ------------------------------------------------------------------
    # 页面池：复用 Page，省去每次 new_page (~60ms) 的开销
    # ------------------------------------------------------------------
    async def _acquire_page(self):
        await self._page_sem.acquire()
        try:
            while self._idle_pages:
                page = self._idle_pages.pop()
                if not page.is_closed() and page.context is self.context and self._is_browser_ready():
                    return page
            await self.init_browser()
            try:
                return await self.context.new_page()
            except Exception:
                # 浏览器或 context 意外断开：强制重建后重试一次
                async with self._lock:
                    await self._cleanup_internal()
                await self.init_browser()
                return await self.context.new_page()
        except BaseException:
            self._page_sem.release()
            raise

    async def _release_page(self, page, reusable: bool):
        try:
            if reusable and not page.is_closed() and page.context is self.context:
                # 导航到空白页即可彻底清空上一次的 DOM / 脚本 / 定时器
                await page.goto("about:blank", timeout=5000)
                self._idle_pages.append(page)
            else:
                await page.close()
        except Exception:
            try:
                await page.close()
            except Exception:
                pass
        finally:
            self._page_sem.release()

    async def from_jinja_template(self, template: str, data: dict, clean_green: bool = False, padding: str | None = None, default_css: bool | None = None) -> str:
        """使用 Jinja2 沙箱环境渲染模板"""
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("tmpldata must be a JSON object")
        rendered_str = _compile_template(template).render(data)
        return await self.from_html(rendered_str, clean_green=clean_green, padding=padding, default_css=default_css)

    def _markdown_to_html(self, text: str) -> str:
        if MarkdownIt is not None:
            return _get_markdown_it().render(_normalize_markdown(text))
        if markdown is not None:
            return markdown.markdown(
                _normalize_markdown(text),
                extensions=['extra', 'sane_lists', 'nl2br'],
            )
        return self._builtin_markdown_parser(text)

    async def from_html(self, content: str, clean_green: bool = False, padding: str | None = None, default_css: bool | None = None) -> str:
        """解析并注入排版样式与 Markdown 支持

        智能识别输入类型：
        - 若输入为完整 HTML 页面，跳过 Markdown 解析，保留原样 DOM/CSS；
        - 若输入为 Markdown / 纯文本 / HTML 片段，编译为语义化 HTML 并注入样式。

        default_css：是否注入默认排版样式。None (默认) 为自动：自带 <style>/<link> 样式表的完整页面
        (如 AstrBot 等客户端的模板) 不注入，保证渲染结果与浏览器预览一致；其余情况注入。
        """
        text = content.strip()

        # 可选特性：清洗特定绿色标签为黑色加粗 (参数可控，默认关闭，支持多行)
        if clean_green:
            text = _GREEN_COLOR_RE.sub(r'<strong>\1</strong>', text)
            text = _GREEN_CLASS_RE.sub(r'<strong>\1</strong>', text)

        is_full_html = bool(_FULL_HTML_RE.match(text))
        if default_css is None:
            default_css = not (is_full_html and _OWN_STYLE_RE.search(text))

        if is_full_html:
            parsed_html = text
        else:
            # 预处理：清洗并规范化标签包裹的 # 标题 (如 <p># 标题</p>)
            def _clean_html_heading(m):
                full = m.group(0)
                clean_text = re.sub(r'<[^>]+>', '', full)
                clean_text = re.sub(r'^[#\s]+', '', clean_text).strip()
                hashes = re.findall(r'#+', full)
                level = min(len(hashes[0]), 4) if hashes else 1
                return f'<h{level} class="t2i-h{level}">{clean_text}</h{level}>'

            text = _HTML_HEADING_RE.sub(_clean_html_heading, text)
            parsed_html = self._markdown_to_html(text)

        # 注入全局默认样式 (放在 <head> 最前面，用户自己的样式可以覆盖它)
        css = AUTOFIT_MARKER + DEFAULT_CSS if default_css else ""
        if not is_full_html:
            final_html = f'<!DOCTYPE html><html><head><meta charset="utf-8">{css}</head><body>{parsed_html}</body></html>'
        elif not css:
            final_html = parsed_html
        elif _HEAD_OPEN_RE.search(parsed_html):
            final_html = _HEAD_OPEN_RE.sub(lambda m: m.group(0) + css, parsed_html, count=1)
        elif _HTML_OPEN_RE.search(parsed_html):
            final_html = _HTML_OPEN_RE.sub(lambda m: m.group(0) + '<head><meta charset="utf-8">' + css + '</head>', parsed_html, count=1)
        else:
            final_html = f'<head><meta charset="utf-8">{css}</head>{parsed_html}'

        # 支持自定义 padding 覆盖
        if padding:
            padding = padding.strip()
            if not _SAFE_PADDING_RE.match(padding):
                raise ValueError(f"invalid padding: {padding!r}")
            padding_css = f"<style>body {{ padding: {padding}; }}</style>"
            if _HEAD_CLOSE_RE.search(final_html):
                final_html = _HEAD_CLOSE_RE.sub(lambda m: padding_css + m.group(0), final_html, count=1)
            else:
                final_html = f"{padding_css}{final_html}"

        return final_html

    def _builtin_markdown_parser(self, text: str) -> str:
        """内置轻量 Markdown 兜底解析器"""
        lines = text.split('\n')
        output = []
        in_list = False
        in_ol = False
        in_table = False
        in_code_block = False
        code_block_lines = []
        
        for line in lines:
            stripped = line.strip()

            # 代码块处理 ```
            if stripped.startswith('```'):
                if in_code_block:
                    output.append('<pre><code>' + '\n'.join(code_block_lines) + '</code></pre>')
                    code_block_lines = []
                    in_code_block = False
                else:
                    if in_list:
                        output.append('</ul>')
                        in_list = False
                    if in_ol:
                        output.append('</ol>')
                        in_ol = False
                    if in_table:
                        output.append('</tbody></table>')
                        in_table = False
                    in_code_block = True
                continue

            if in_code_block:
                escaped_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                code_block_lines.append(escaped_line)
                continue

            if not stripped:
                if in_list:
                    output.append('</ul>')
                    in_list = False
                if in_ol:
                    output.append('</ol>')
                    in_ol = False
                if in_table:
                    output.append('</tbody></table>')
                    in_table = False
                continue
                
            # Markdown 表格行检测
            if stripped.startswith('|') and stripped.endswith('|') and '|' in stripped[1:-1]:
                if in_list:
                    output.append('</ul>')
                    in_list = False
                if in_ol:
                    output.append('</ol>')
                    in_ol = False
                    
                if re.match(r'^\|(?:\s*:?-+:?\s*\|)+$', stripped):
                    continue
                    
                cells = [c.strip() for c in stripped.strip('|').split('|')]
                formatted_cells = [re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', c) for c in cells]
                
                if not in_table:
                    output.append('<table>')
                    output.append('<thead><tr>' + ''.join(f'<th>{c}</th>' for c in formatted_cells) + '</tr></thead>')
                    output.append('<tbody>')
                    in_table = True
                else:
                    output.append('<tr>' + ''.join(f'<td>{c}</td>' for c in formatted_cells) + '</tr>')
                continue
                
            if in_table:
                output.append('</tbody></table>')
                in_table = False

            # 已存在的 HTML 块级标签直接输出
            if re.match(r'^<(?:h[1-6]|div|table|thead|tbody|tr|th|td|ul|ol|li|p|blockquote|pre|hr)\b', stripped, re.IGNORECASE):
                if in_list:
                    output.append('</ul>')
                    in_list = False
                if in_ol:
                    output.append('</ol>')
                    in_ol = False
                output.append(stripped)
                continue

            # Markdown 标题 (#, ##, ###, ####)
            heading_m = re.match(r'^(#+\s*)+(.*?)$', stripped)
            if heading_m:
                if in_list:
                    output.append('</ul>')
                    in_list = False
                if in_ol:
                    output.append('</ol>')
                    in_ol = False
                hashes = re.findall(r'#+', stripped)
                level = min(len(hashes[0]), 4) if hashes else 1
                title_text = re.sub(r'^[#\s]+', '', stripped).strip()
                output.append(f'<h{level} class="t2i-h{level}">{title_text}</h{level}>')
                continue

            # 分割线
            if re.match(r'^(?:---|\*\*\*|___)$', stripped):
                if in_list:
                    output.append('</ul>')
                    in_list = False
                if in_ol:
                    output.append('</ol>')
                    in_ol = False
                output.append('<hr>')
                continue

            # 引用 > quote
            if stripped.startswith('>'):
                if in_list:
                    output.append('</ul>')
                    in_list = False
                if in_ol:
                    output.append('</ol>')
                    in_ol = False
                quote_text = stripped.lstrip('> ').strip()
                quote_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', quote_text)
                output.append(f'<blockquote>{quote_text}</blockquote>')
                continue

            # 无序列表 (-, *, •)
            list_m = re.match(r'^[-*•]\s+(.*)$', stripped)
            if list_m:
                if not in_list:
                    output.append('<ul>')
                    in_list = True
                item_text = list_m.group(1)
                item_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', item_text)
                output.append(f'<li>{item_text}</li>')
                continue

            # 有序列表 (1. 2.)
            ol_m = re.match(r'^\d+\.\s+(.*)$', stripped)
            if ol_m:
                if not in_ol:
                    output.append('<ol>')
                    in_ol = True
                item_text = ol_m.group(1)
                item_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', item_text)
                output.append(f'<li>{item_text}</li>')
                continue

            if in_list:
                output.append('</ul>')
                in_list = False
            if in_ol:
                output.append('</ol>')
                in_ol = False

            # 普通段落与加粗
            line_content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', stripped)
            output.append(f'<p>{line_content}</p>')

        if in_code_block:
            output.append('<pre><code>' + '\n'.join(code_block_lines) + '</code></pre>')
        if in_list:
            output.append('</ul>')
        if in_ol:
            output.append('</ol>')
        if in_table:
            output.append('</tbody></table>')

        return '\n'.join(output)

    def _sanitize_screenshot_options(self, raw_options: dict | None, img_type: str) -> dict:
        """安全清洗与规范化 Playwright 截图选项，解决参数互斥与驼峰命名兼容"""
        if not isinstance(raw_options, dict):
            raw_options = {}

        # 别名映射：支持 camelCase 转 snake_case
        # 注意：deviceScaleFactor 属于 BrowserContext 参数而非 screenshot 参数，直接忽略（不在白名单即被过滤）
        alias_map = {
            'fullPage': 'full_page',
            'omitBackground': 'omit_background',
        }
        normalized = {}
        for k, v in raw_options.items():
            mapped_key = alias_map.get(k, k)
            normalized[mapped_key] = v

        allowed_keys = {
            'timeout', 'type', 'quality', 'omit_background',
            'full_page', 'clip', 'animations', 'caret', 'scale', 'mask'
        }
        opts = {k: v for k, v in normalized.items() if k in allowed_keys and v is not None}

        # type 统一为 Playwright 认可的取值 (jpg -> jpeg)
        norm_type = img_type.lower()
        norm_type = "png" if norm_type == "png" else "jpeg"
        opts['type'] = norm_type

        # 1. 解决 clip 与 full_page 的参数互斥问题
        if opts.get('clip'):
            opts.pop('full_page', None)
        else:
            opts.setdefault('full_page', True)

        # 2. 解决图片类型与特定参数的互斥问题
        if norm_type == "png":
            # PNG 为无损格式，不支持 quality 参数
            opts.pop('quality', None)
        else:
            # JPEG 不支持透明背景 omit_background
            opts.pop('omit_background', None)
            try:
                opts['quality'] = min(100, max(1, int(opts.get('quality', 92))))
            except (TypeError, ValueError):
                opts['quality'] = 92

        if 'timeout' in opts:
            try:
                opts['timeout'] = float(opts['timeout'])
            except (TypeError, ValueError):
                opts.pop('timeout')

        return opts

    # 快速截图只接管默认的整页截图；clip / mask / 透明背景 / css 缩放等仍交给 Playwright
    _FAST_SHOT_KEYS = frozenset({'type', 'full_page', 'animations', 'caret', 'timeout', 'scale'})
    _FAST_SHOT_MAX_FAILURES = 3

    async def _fast_screenshot(self, page, shot_opts: dict) -> bytes | None:
        """通过 CDP 直接截取整页 PNG，开启 optimizeForSpeed (只降低 PNG 的 zlib 压缩等级，像素不变)

        Chromium 默认用高压缩等级编码 PNG，长图要几百毫秒，而这份 PNG 马上就会被 Pillow 解码重编码，
        压缩率毫无意义。任何异常都返回 None，由调用方回退到 page.screenshot；连续失败多次后不再尝试。
        """
        if (not _FAST_SHOT_ENABLED or self._fast_shot_failures >= self._FAST_SHOT_MAX_FAILURES
                or not shot_opts.get('full_page') or set(shot_opts) - self._FAST_SHOT_KEYS
                or shot_opts.get('scale') == 'css' or shot_opts.get('caret') == 'initial'):
            return None
        timeout_ms = shot_opts.get('timeout')
        timeout_ms = self.page_timeout if timeout_ms is None else timeout_ms
        cdp = None
        try:
            async def _capture():
                nonlocal cdp
                cdp = await page.context.new_cdp_session(page)
                dpr = await page.evaluate(_PREPARE_SHOT_JS, shot_opts.get('animations') != 'allow')
                metrics = await cdp.send("Page.getLayoutMetrics")
                size = metrics.get("cssContentSize") or metrics["contentSize"]
                res = await cdp.send("Page.captureScreenshot", {
                    "format": "png",
                    "optimizeForSpeed": True,
                    "captureBeyondViewport": True,
                    "fromSurface": True,
                    "clip": {"x": 0, "y": 0, "width": math.ceil(size["width"]),
                             "height": math.ceil(size["height"]), "scale": float(dpr or 1)},
                })
                return base64.b64decode(res["data"])

            data = await (asyncio.wait_for(_capture(), timeout_ms / 1000) if timeout_ms else _capture())
            self._fast_shot_failures = 0
            return data
        except Exception as e:
            self._fast_shot_failures += 1
            logging.debug(f"快速截图失败，回退 Playwright 截图: {e!r}")
            return None
        finally:
            if cdp is not None:
                try:
                    await cdp.detach()
                except Exception:
                    pass

    @staticmethod
    def _resolve_img_type(raw_opts: dict) -> str:
        raw_type = str(raw_opts.get("type") or "jpeg").lower()
        return "png" if raw_type == "png" else "jpeg"

    async def html2pic(self, content_or_path: str, screenshot_options: ScreenshotOptions | None = None) -> str:
        """将HTML/Markdown内容或文件渲染为图片

        Args:
            content_or_path (str): HTML/Markdown字符串或文件路径
            screenshot_options (ScreenshotOptions, optional): 截图选项配置

        Returns:
            str: 生成的图片文件相对路径 (data/rendered_xxx.suffix)
        """
        raw_opts = dict(screenshot_options) if screenshot_options else {}
        img_type = self._resolve_img_type(raw_opts)
        opts = self._sanitize_screenshot_options(raw_opts, img_type)
        compress_mode = str(raw_opts.get("compress") or DEFAULT_COMPRESS).lower()
        # 透明背景需要 alpha 通道，调色板 PNG 不保留，此时不转 256 色
        use_png8 = (_OPTIMIZE_ENABLED and compress_mode != "off" and _compress.is_available()
                    and not opts.get("omit_background"))
        result_path = None

        # 视口：宽度可自定义；整页截图时用极小高度，让图片高度贴合内容
        width = self.viewport['width']
        custom_width = raw_opts.get("width")
        if isinstance(custom_width, (int, float)) and not isinstance(custom_width, bool) and 0 < custom_width <= 4096:
            width = int(custom_width)
        # 只有本服务排版的页面才把视口压到最小高度；客户端模板保持 920x800 视口，
        # 与浏览器预览一致 (模板里的 100vh、媒体查询等不受影响)
        fit_content = opts.get('full_page') and not opts.get('clip') and AUTOFIT_MARKER in content_or_path[:8192]
        height = self.FIT_VIEWPORT_HEIGHT if fit_content else self.viewport['height']

        page = await self._acquire_page()
        reusable = True
        try:
            await page.set_viewport_size({"width": width, "height": height})

            # 内存直传 vs 文件路径
            if content_or_path.startswith("file://") or (os.path.isabs(content_or_path) and os.path.exists(content_or_path)):
                url = content_or_path if content_or_path.startswith("file://") else f'file://{content_or_path}'
                await page.goto(url, wait_until='load', timeout=self.page_timeout)
            else:
                await page.set_content(content_or_path, wait_until='load', timeout=self.page_timeout)

            # 等待字体就绪 (最多等待 2 秒熔断兜底，避免外部网络字体加载挂起阻塞)
            try:
                await page.evaluate("Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2000))])")
            except Exception:
                pass

            # 处理 mask 选择器字符串 (若传入 selector 列表则转换为 locator)
            if isinstance(opts.get("mask"), list):
                opts["mask"] = [page.locator(item) if isinstance(item, str) else item for item in opts["mask"]]

            if _OPTIMIZE_ENABLED:
                # 浏览器输出无损 PNG (内存)，再由 Pillow 一次性解码、编码为最终格式
                shot_opts = {k: v for k, v in opts.items() if k != 'quality'}
                shot_opts['type'] = 'png'
                shot_opts.setdefault('animations', 'disabled')
                png_bytes = await self._fast_screenshot(page, shot_opts)
                if png_bytes is None:
                    png_bytes = await page.screenshot(**shot_opts)
                try:
                    quality = min(100, max(1, int(raw_opts.get('quality') or DEFAULT_JPEG_QUALITY)))
                except (TypeError, ValueError):
                    quality = DEFAULT_JPEG_QUALITY
                result_path = await asyncio.to_thread(_finalize_image, png_bytes, img_type, quality, use_png8)
            else:
                result_path, _ = generate_data_path(suffix=img_type, namespace="rendered")
                await page.screenshot(path=result_path, **opts)
            return result_path
        except Exception:
            reusable = False
            if result_path and os.path.exists(result_path):
                try:
                    os.remove(result_path)
                except Exception:
                    pass
            raise
        finally:
            await self._release_page(page, reusable)
