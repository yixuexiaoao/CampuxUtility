"""容器内自检：确认正在运行的代码版本、压缩依赖是否就绪，并实际渲染一张长图看体积

用法 (在容器里执行)：
    docker exec <容器名> python -m src.selfcheck
"""
import asyncio
import os

from .version import __version__
from . import compress as _compress
from . import render as _render
from . import netguard as _netguard

SAMPLE = "\n\n".join(
    [
        "## 自检样例",
        "这是一段用于检查**图片体积**的长文本，包含*列表*、表格与代码。",
        "| 项目 | 说明 |\n|---|---|\n| 压缩 | 文字截图转 256 色 PNG |\n| 回退 | 照片/渐变较多时用 JPEG |",
        "1. 第一项\n2. 第二项\n3. 第三项",
        "```python\nprint('hello t2i')\n```",
    ]
    * 6
)


async def main():
    try:
        import numpy  # noqa: F401
        has_numpy = True
    except ImportError:
        has_numpy = False

    print(f"t2i-service 版本     : {__version__}")
    print(f"Pillow 已安装        : {'是' if _compress.is_available() else '否 (压缩不会生效！)'}")
    print(f"numpy 已安装         : {'是' if has_numpy else '否 (可用，但压缩更慢)'}")
    print(f"T2I_OPTIMIZE 后处理  : {'开启' if _render._OPTIMIZE_ENABLED else '关闭 (压缩不会生效！)'}")
    print(f"T2I_COMPRESS         : {_render.DEFAULT_COMPRESS}  (颜色上限 {_render.COMPRESS_MAX_COLORS}，逐块 PSNR >= {_render.COMPRESS_MIN_TILE_PSNR}dB)")
    print(f"T2I_NETWORK 网络策略  : {_netguard.POLICY}")
    print(f"T2I_SCALE 分辨率倍数 : {_render.DEFAULT_SCALE}  (图片宽度 {int(920 * _render.DEFAULT_SCALE)}px)")

    r = _render.Text2ImgRender()
    try:
        html = await r.from_html(SAMPLE)
        path = await r.html2pic(html, {"type": "jpeg", "quality": 92})
        size_kb = os.path.getsize(path) // 1024
        print(f"自检图片             : {path}  {size_kb}KB")
        print("结论                 : " + ("256 色 PNG 压缩已生效" if path.endswith(".png") else "未压缩 (输出仍为 JPEG)"))
        os.remove(path)
    finally:
        await r.close()


if __name__ == "__main__":
    asyncio.run(main())
