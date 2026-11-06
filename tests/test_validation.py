import os, sys, unittest, time, tempfile
from unittest.mock import MagicMock

# 确保能正确导入 src 模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 如果未安装 fastapi，注入 mock 使得单元测试能正常导入并测试 api.py 内的纯逻辑与路由配置
if "fastapi" not in sys.modules:
    try:
        import fastapi
    except ImportError:
        mock_fastapi = MagicMock()
        mock_fastapi.FastAPI.return_value = MagicMock()
        sys.modules["fastapi"] = mock_fastapi
        sys.modules["fastapi.responses"] = MagicMock()

from src.render import Text2ImgRender, DEFAULT_CSS
from src.api import MIME_MAP, cleanup_expired_files, safe_template_name
from src.util import generate_data_path


class TestText2ImgFixes(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.render = Text2ImgRender()

    def test_default_css_no_important(self):
        """验证 DEFAULT_CSS 不再包含 !important，允许用户自定义覆盖"""
        self.assertNotIn("!important", DEFAULT_CSS)

    async def test_from_html_full_page_preserved(self):
        """验证完整 HTML 页面不会被 Markdown 破坏缩进与结构"""
        raw_html = """<!DOCTYPE html>
<html>
<head>
    <style>
        .custom { color: red; }
    </style>
</head>
<body>
    <div class="custom">
        <p>Hello World</p>
    </div>
</body>
</html>"""
        result = await self.render.from_html(raw_html)
        # 验证 style 标签未被插 <br>
        self.assertIn(".custom { color: red; }", result)
        # 验证 DOCTYPE 存在
        self.assertIn("<!DOCTYPE html>", result)
        # 验证没有被包裹 pre code 代码块
        self.assertNotIn("<pre><code>", result)

    async def test_from_html_markdown_support(self):
        """验证纯 Markdown 能够被正确解析"""
        md_text = """# 大标题
这是一个 **加粗** 测试。

- 列表项 1
- 列表项 2

| 头部1 | 头部2 |
| :--- | :--- |
| 单元格1 | 单元格2 |
"""
        result = await self.render.from_html(md_text)
        self.assertTrue("<h1" in result or "大标题" in result)
        self.assertIn("<strong>加粗</strong>", result)
        self.assertIn("<table", result)

    async def test_clean_green_parameter(self):
        """验证 clean_green 可选参数"""
        green_html = '<p><span style="color: green">绿色文字</span></p>'
        
        # 默认不清理
        no_clean = await self.render.from_html(green_html, clean_green=False)
        self.assertIn('color: green', no_clean)
        
        # 开启清理
        cleaned = await self.render.from_html(green_html, clean_green=True)
        self.assertIn('<strong>绿色文字</strong>', cleaned)

    async def test_clean_green_multiline(self):
        """验证 clean_green 支持跨行标签的清理"""
        green_multiline = '<p><span style="color: green">\n第一行\n第二行\n</span></p>'
        cleaned = await self.render.from_html(green_multiline, clean_green=True)
        self.assertNotIn('color: green', cleaned)
        self.assertIn('<strong>', cleaned)
        self.assertIn('第一行', cleaned)
        self.assertIn('第二行', cleaned)

    def test_sanitize_screenshot_options_clip_and_full_page(self):
        """验证 clip 和 full_page 互斥性处理"""
        raw = {"clip": {"x": 0, "y": 0, "width": 100, "height": 100}, "full_page": True}
        opts = self.render._sanitize_screenshot_options(raw, "jpeg")
        # clip 存在时，full_page 必须被移除或置为 False
        self.assertFalse(opts.get("full_page", False))
        self.assertIn("clip", opts)

    def test_sanitize_screenshot_options_png_and_quality(self):
        """验证 PNG 格式自动剔除 quality 参数"""
        raw = {"type": "png", "quality": 80}
        opts = self.render._sanitize_screenshot_options(raw, "png")
        self.assertNotIn("quality", opts)

    def test_sanitize_screenshot_options_jpeg_and_omit_background(self):
        """验证 JPEG 格式自动剔除 omit_background 并提供默认 quality"""
        raw = {"type": "jpeg", "omit_background": True}
        opts = self.render._sanitize_screenshot_options(raw, "jpeg")
        self.assertNotIn("omit_background", opts)
        self.assertEqual(opts.get("quality"), 92)

    def test_sanitize_screenshot_options_camel_case_alias(self):
        """验证 fullPage 和 omitBackground 等驼峰命名自动映射"""
        raw = {"fullPage": False, "omitBackground": True}
        opts = self.render._sanitize_screenshot_options(raw, "png")
        self.assertIn("full_page", opts)
        self.assertEqual(opts["full_page"], False)
        self.assertIn("omit_background", opts)
        self.assertEqual(opts["omit_background"], True)

    def test_path_traversal_defense(self):
        """验证路径穿越防御机制"""
        malicious_names = [
            "../../etc/passwd",
            "..\\..\\windows\\win.ini",
            "subdir/template",
            "../../../secret"
        ]
        for name in malicious_names:
            self.assertIsNone(safe_template_name(name), name)
        for ok in ["news", "daily_report", "周报-v2"]:
            self.assertEqual(safe_template_name(ok), ok)

    def test_mime_type_mappings(self):
        """验证 MIME 类型标准化"""
        self.assertEqual(MIME_MAP.get("jpg"), "image/jpeg")
        self.assertEqual(MIME_MAP.get("jpeg"), "image/jpeg")
        self.assertEqual(MIME_MAP.get("png"), "image/png")
        self.assertEqual(MIME_MAP.get("webp"), "image/webp")

    def test_generate_data_path_structure(self):
        """验证生成的临时文件路径与格式"""
        rel_path, abs_path = generate_data_path("jpeg", "rendered")
        filename = os.path.basename(rel_path)
        self.assertTrue(filename.startswith("rendered_"))
        self.assertTrue(filename.endswith(".jpeg"))
        self.assertTrue(os.path.isabs(abs_path))

    def test_cleanup_expired_files(self):
        """验证过期临时文件清理功能"""
        test_dir = os.path.join("data", "test_cleanup_tmp")
        os.makedirs(test_dir, exist_ok=True)
        try:
            # 创建一个假过期文件
            old_file = os.path.join(test_dir, "rendered_old_test.jpeg")
            with open(old_file, "w", encoding="utf-8") as f:
                f.write("old data")
            # 将 mtime 设为 2 天前
            two_days_ago = time.time() - 172800
            os.utime(old_file, (two_days_ago, two_days_ago))

            # 创建一个新文件
            new_file = os.path.join(test_dir, "rendered_new_test.jpeg")
            with open(new_file, "w", encoding="utf-8") as f:
                f.write("new data")

            # 执行清理 (阈值设为 24 小时)
            cleanup_expired_files(test_dir, max_age_seconds=86400)

            self.assertFalse(os.path.exists(old_file))
            self.assertTrue(os.path.exists(new_file))
        finally:
            if os.path.exists(test_dir):
                import shutil
                shutil.rmtree(test_dir, ignore_errors=True)


    def test_sanitize_screenshot_options_ignores_device_scale_factor(self):
        """验证非 screenshot 参数 deviceScaleFactor 被安全忽略而非误映射"""
        raw = {"deviceScaleFactor": 2}
        opts = self.render._sanitize_screenshot_options(raw, "jpeg")
        self.assertNotIn("deviceScaleFactor", opts)
        self.assertNotIn("scale", opts)
        # 非法键不影响正常默认值
        self.assertEqual(opts.get("full_page"), True)
        self.assertEqual(opts.get("quality"), 92)

    def test_builtin_markdown_parser_features(self):
        """验证内置备用 Markdown 解析器的代码块、引用、列表和表格"""
        raw_md = """# 一级标题
> 这是引用

```python
def hello():
    print("world")
```

- 项目 1
- 项目 2

1. 步骤 A
2. 步骤 B

| 列1 | 列2 |
| --- | --- |
| 值1 | 值2 |
"""
        parsed = self.render._builtin_markdown_parser(raw_md)
        self.assertIn('<h1 class="t2i-h1">一级标题</h1>', parsed)
        self.assertIn('<blockquote>这是引用</blockquote>', parsed)
        self.assertIn('<pre><code>', parsed)
        self.assertIn('def hello():', parsed)
        self.assertIn('<ul>', parsed)
        self.assertIn('<ol>', parsed)
        self.assertIn('<table>', parsed)

    def test_default_css_margin_resets(self):
        """验证 DEFAULT_CSS 包含首尾元素外边距清零规则，保证上下留白对称"""
        self.assertIn("body > *:first-child", DEFAULT_CSS)
        self.assertIn("body > *:last-child", DEFAULT_CSS)
        self.assertIn("margin-top: 0", DEFAULT_CSS)
        self.assertIn("margin-bottom: 0", DEFAULT_CSS)

    async def test_from_html_custom_padding(self):
        """验证 from_html 支持传入自定义 padding 覆盖"""
        result = await self.render.from_html("<p>Hello</p>", padding="0px")
        self.assertIn("padding: 0px", result)

    def test_pre_code_wrap_styling(self):
        """验证 pre code 包含换行与截断保护样式，避免右边距穿透"""
        self.assertIn("white-space: pre-wrap", DEFAULT_CSS)
        self.assertIn("overflow-wrap: anywhere", DEFAULT_CSS)

    def test_sanitize_screenshot_options_none_or_empty(self):
        """验证 options 传入 None 或空值时的健壮性"""
        opts = self.render._sanitize_screenshot_options(None, "jpeg")
        self.assertEqual(opts.get("full_page"), True)
        self.assertEqual(opts.get("quality"), 92)

        opts_png = self.render._sanitize_screenshot_options({}, "png")
        self.assertEqual(opts_png.get("full_page"), True)
        self.assertNotIn("quality", opts_png)

    def test_default_css_no_first_of_type_reset(self):
        """首个 <p> 不应因 :first-of-type 被清掉上边距 (会导致标题与正文贴在一起)"""
        self.assertNotIn("first-of-type", DEFAULT_CSS)
        self.assertNotIn("last-of-type", DEFAULT_CSS)

    async def test_llm_style_markdown(self):
        """验证 LLM 常见写法：列表/表格前无空行、#标题无空格、单换行保留"""
        md = "说明：\n- 项目A\n- 项目B\n\n结果：\n| a | b |\n|---|---|\n| 1 | 2 |\n\n#标题\n第一行\n第二行"
        result = await self.render.from_html(md)
        self.assertIn("<ul>", result)
        self.assertIn("<table>", result)
        self.assertIn("标题</h1>", result)
        self.assertRegex(result, r"第一行<br\s*/?>")

    async def test_text_after_list_and_table_is_paragraph(self):
        """列表/表格后紧跟的顶格文本应成为独立段落"""
        md = "1. 第一步\n2. 第二步\n对比：\n| a | b |\n|---|---|\n| 1 | 2 |\n结尾"
        result = await self.render.from_html(md)
        self.assertIn("<p>对比：</p>", result)
        self.assertIn("<p>结尾</p>", result)
        self.assertNotIn("<td>结尾</td>", result)

    async def test_list_item_indented_continuation_kept(self):
        result = await self.render.from_html("- 项目A\n  补充说明\n- 项目B")
        self.assertEqual(result.count("<li>"), 2)
        self.assertIn("补充说明", result.split("</li>")[0])

    async def test_code_block_not_mangled(self):
        """代码块内的 # 行不能被当成标题规范化"""
        md = "```c\n#include <stdio.h>\n#!/bin/bash\n```"
        result = await self.render.from_html(md)
        self.assertIn("#include", result)
        self.assertIn("#!/bin/bash", result)

    async def test_body_tag_in_text_not_full_html(self):
        """正文中提到 <body> 不应被误判为完整 HTML 页面"""
        result = await self.render.from_html("# 说明\n\n使用 `<body>` 标签包裹内容")
        self.assertIn("<h1>", result)

    async def test_padding_injection_rejected(self):
        with self.assertRaises(ValueError):
            await self.render.from_html("<p>x</p>", padding="0}</style><script>alert(1)</script>")
        ok = await self.render.from_html("<p>x</p>", padding="20px calc(1em + 4px)")
        self.assertIn("padding: 20px calc(1em + 4px)", ok)

    async def test_styled_full_page_not_injected(self):
        """自带样式的完整页面/模板默认不注入 DEFAULT_CSS，避免与客户端预览不一致"""
        page = "<!doctype html><html><head><style>body{font-size:25px}</style></head><body><table><tr><td>x</td></tr></table></body></html>"
        result = await self.render.from_html(page)
        self.assertEqual(result, page)
        forced = await self.render.from_html(page, default_css=True)
        self.assertIn(DEFAULT_CSS, forced)
        self.assertLess(forced.index(DEFAULT_CSS), forced.index("font-size:25px"))

    async def test_unstyled_full_page_gets_default_css(self):
        result = await self.render.from_html("<html><body><p>hi</p></body></html>")
        self.assertIn(DEFAULT_CSS, result)
        off = await self.render.from_html("# 标题", default_css=False)
        self.assertNotIn(DEFAULT_CSS, off)

    def test_quality_coercion(self):
        opts = self.render._sanitize_screenshot_options({"quality": "150"}, "jpeg")
        self.assertEqual(opts["quality"], 100)


if __name__ == "__main__":
    unittest.main()

