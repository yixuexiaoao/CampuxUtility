import fastapi, os, re, time, logging, asyncio
from contextlib import asynccontextmanager
from .render import Text2ImgRender, ScreenshotOptions
from . import render as _render_mod
from . import compress as _compress
from . import netguard as _netguard
from .version import __version__
from dataclasses import dataclass
from json.decoder import JSONDecodeError

render = Text2ImgRender()

# 所有图片响应都带上版本号，方便在客户端/抓包里确认实际运行的是哪个版本
VERSION_HEADERS = {"X-T2I-Version": __version__}


def _runtime_info() -> dict:
    try:
        import numpy  # noqa: F401
        has_numpy = True
    except ImportError:
        has_numpy = False
    return {
        "version": __version__,
        "pillow": _compress.is_available(),
        "numpy": has_numpy,
        "optimize": bool(_render_mod._OPTIMIZE_ENABLED),
        "compress": _render_mod.DEFAULT_COMPRESS,
        "scale": _render_mod.DEFAULT_SCALE,
        # 页面网络访问策略：public 只允许公网 / none 全部拦截 / all 不拦截
        "network": _netguard.POLICY,
        # 三项都满足时，文字截图会输出 256 色 PNG
        "png8_active": bool(_render_mod._OPTIMIZE_ENABLED and _compress.is_available()
                            and _render_mod.DEFAULT_COMPRESS != "off"),
    }

MIME_MAP = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "svg": "image/svg+xml"
}

# 模板名只允许字母/数字/下划线/中划线 (含中文)，从根上杜绝 ../ 与 ..\ 路径穿越
_TEMPLATE_NAME_RE = re.compile(r"[\w\-]{1,128}")


def safe_template_name(name: str) -> str | None:
    """校验模板名，合法则原样返回，否则返回 None (跨平台，不依赖 os.path.basename)"""
    name = str(name).strip()
    return name if _TEMPLATE_NAME_RE.fullmatch(name) else None


def cleanup_expired_files(directory: str = "data", max_age_seconds: int = 86400):
    """自动清理 data/ 目录下过期的历史渲染临时文件（默认保留 24 小时）"""
    if not os.path.exists(directory):
        return
    now = time.time()
    try:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            if os.path.isfile(fpath) and fname.startswith("rendered_"):
                try:
                    if now - os.path.getmtime(fpath) > max_age_seconds:
                        os.remove(fpath)
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"清理临时文件异常: {e}")


async def _periodic_cleanup(interval_seconds: int = 3600, max_age_seconds: int = 86400):
    """后台定时清理 data/ 目录下过期的历史渲染临时文件"""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            # 文件遍历/删除是阻塞 IO，放到线程池执行，避免卡住事件循环
            await asyncio.to_thread(cleanup_expired_files, "data", max_age_seconds)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning(f"定时清理异常: {e}")


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    """应用生命周期管理：启动时预热浏览器实例并启动定时清理，退出时释放资源"""
    info = _runtime_info()
    print(f"[t2i] version {info['version']} | pillow={info['pillow']} numpy={info['numpy']} "
          f"optimize={info['optimize']} compress={info['compress']} scale={info['scale']} network={info['network']} png8_active={info['png8_active']}", flush=True)
    await asyncio.to_thread(cleanup_expired_files)
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    try:
        await render.init_browser()
    except Exception as e:
        logging.warning(f"浏览器预热失败 (将在请求时自动重试): {e}")
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await render.close()


app = fastapi.FastAPI(lifespan=lifespan)


@dataclass
class Result:
    """API响应结果数据类"""
    code: int
    message: str
    data: dict


@app.get("/health")
@app.get("/ping")
async def health():
    """服务健康检查接口"""
    return {"status": "ok", "browser_ready": render._is_browser_ready(), **_runtime_info()}


@app.get("/text2img/data/{id:path}")
@app.get("/text2img/{id:path}")
@app.get("/data/{id:path}")
async def text2img_image(id: str):
    """根据ID获取生成的图片文件，兼容多种路由前缀与路径形式"""
    filename = os.path.basename(id)
    pic = os.path.join("data", filename)
    if os.path.exists(pic) and os.path.isfile(pic):
        suffix = filename.split(".")[-1].lower() if "." in filename else "jpeg"
        media_type = MIME_MAP.get(suffix, f"image/{suffix}")
        return fastapi.responses.FileResponse(pic, media_type=media_type, headers=VERSION_HEADERS)
    return Result(code=1, message="file not found", data={})


@app.post("/text2img/generate")
async def text2img(request: fastapi.Request):
    """HTML/Markdown转图片生成接口"""
    try:
        data = await request.json()
    except JSONDecodeError:
        return Result(code=1, message="invalid json body", data={})
    except Exception as e:
        return Result(code=1, message=f"request error: {str(e)}", data={})

    if not isinstance(data, dict):
        return Result(code=1, message="request body must be a JSON object", data={})

    is_json_return = bool(data.get("json", False))
    clean_green = bool(data.get("clean_green", False))
    padding = data.get("padding")
    if padding is not None:
        padding = str(padding)
    # 是否注入默认排版样式：缺省为自动 (自带样式的完整 HTML/模板不注入，与浏览器预览一致)
    default_css = data.get("default_css")
    default_css = None if default_css is None else bool(default_css)
    tmpldata = data.get('tmpldata') or {}
    if not isinstance(tmpldata, dict):
        return Result(code=1, message="tmpldata must be a JSON object", data={})

    # 处理HTML内容来源：模板或直接HTML/Markdown
    try:
        if 'tmpl' in data or 'tmplname' in data:
            if 'tmpl' in data:
                tmpl = str(data['tmpl'])
            else:
                raw_tmplname = str(data['tmplname'])
                safe_tmplname = safe_template_name(raw_tmplname)
                tmpl_path = os.path.join("tmpl", f"{safe_tmplname}.html") if safe_tmplname else None
                if not tmpl_path or not os.path.isfile(tmpl_path):
                    return Result(code=1, message=f"template {raw_tmplname} not found", data={})
                with open(tmpl_path, "r", encoding="utf-8") as f:
                    tmpl = f.read()
            html_content = await render.from_jinja_template(tmpl, tmpldata, clean_green=clean_green, padding=padding, default_css=default_css)
        elif 'html' in data:
            html_content = await render.from_html(str(data["html"]), clean_green=clean_green, padding=padding, default_css=default_css)
        else:
            return Result(code=1, message="html or tmpl not found", data={})
    except Exception as e:
        return Result(code=1, message=f"template or html render error: {str(e)}", data={})

    # 截图选项解析
    raw_options = data.get("options")
    options = ScreenshotOptions(**raw_options) if isinstance(raw_options, dict) else ScreenshotOptions()

    # 执行极速渲染
    try:
        pic = await render.html2pic(html_content, options)
    except Exception as e:
        return Result(code=1, message=f"render screenshot error: {str(e)}", data={})

    # 规范化返回纯文件ID (如 rendered_1724734321_abcdef12.jpeg)
    file_id = os.path.basename(pic)

    if is_json_return:
        return Result(code=0, message="success", data={
            "id": file_id,
            "version": __version__,
        })
    suffix = file_id.rsplit(".", 1)[-1].lower()
    return fastapi.responses.FileResponse(pic, media_type=MIME_MAP.get(suffix, "image/jpeg"), headers=VERSION_HEADERS)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api:app", host="0.0.0.0", port=8999)
