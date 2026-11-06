# Idoknow-api-text2img

当前版本：**2.4.0**（见 `src/version.py`）

### 如何确认正在运行的版本

- 访问 `GET /health`，返回中的 `version` 应为 `2.4.0`，`png8_active` 为 `true` 表示 256 色 PNG 压缩已生效；
- 容器启动日志第一行会打印 `[t2i] version 2.4.0 | pillow=True numpy=True ... png8_active=True`；
- 所有图片响应都带 `X-T2I-Version` 响应头；`json` 模式返回的 `data.version` 也是版本号；
- 在容器内自检（渲染一张长图并打印体积）：`docker exec <容器名> python -m src.selfcheck`

基于 FastAPI 与 Playwright 的极速 HTML / Markdown 转图片微服务。

## 与源仓库的区别

本仓库 fork 自 [idoknow/CampuxUtility](https://github.com/idoknow/CampuxUtility)（对应 Docker Hub 镜像见本节末），在保持接口兼容的基础上做了如下改动：

| 方面 | 源仓库 | 本仓库 |
| :--- | :--- | :--- |
| 图片体积 | 直接输出浏览器截图 | 用 Pillow/numpy 优化编码；文字类截图转 256 色 PNG，带逐块画质门槛，不满足自动回退 JPEG（见“图片体积压缩”） |
| Markdown | 无专门处理 | `markdown-it-py` 解析并兼容大模型输出，缺依赖时依次回退 `markdown` 与内置解析器 |
| 渲染性能 | 每次请求新建页面 | 页面池复用（`T2I_MAX_PAGES`），整页截图走 CDP `optimizeForSpeed` |
| 安全 | 无网络限制 | `T2I_NETWORK` 拦截内网/回环/元数据地址与 `file://`（防 SSRF）；Jinja2 沙箱渲染；`tmplname` 限制字符；容器以非 root（uid 10001）运行 |
| 请求参数 | 基础截图选项 | 新增 `padding`、`default_css`、`clean_green`、`options.width`、`options.compress` 等 |
| 可观测性 | 无 | `/health` 返回版本与压缩状态，响应头 `X-T2I-Version`，`python -m src.selfcheck` 自检 |
| 镜像 | 仅装 Playwright | 内置中文与彩色 Emoji 字体，避免豆腐块；依赖层可缓存 |
| CI | 构建并推送镜像 | 先跑单元测试（`tests/`），再构建 amd64/arm64 并推送；镜像名可由仓库变量 `DOCKER_IMAGE` 覆盖，默认 `xiaobaiski/campuxutility` |

已知差异：过期图片清理在本仓库内置于 `src/api.py`（固定 24 小时），**暂不读取**源仓库的 `IMAGE_LIFETIME_HOURS` 环境变量。

Docker 镜像：`docker pull xiaobaiski/campuxutility:latest`


## 接口说明

### 1. POST `/text2img/generate`

将 HTML 或 Markdown 内容转换为图片文件或返回图片 ID。

#### 请求参数 (JSON)

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `html` | string | 否 | 直接传入的 HTML / Markdown 文本（与 `tmpl` 二选一） |
| `tmpl` | string | 否 | Jinja2 HTML 模板字符串（在沙箱环境中渲染，禁止访问 Python 内部对象） |
| `tmplname` | string | 否 | `tmpl/` 目录下的模板名称（无需 `.html` 后缀，仅允许字母/数字/中文/`_`/`-`） |
| `tmpldata` | object | 否 | 配合 Jinja2 模板使用的字典数据 |
| `json` | boolean | 否 | 为 `true` 时返回 JSON 包含图片 `id`；为 `false` 直接返回图片流（默认 `false`） |
| `clean_green` | boolean | 否 | 是否将绿色字体标签统一转为黑色加粗（默认 `false`） |
| `padding` | string | 否 | 自定义页面内边距（如 `"0"`、`"20px"`、`"36px 40px"`），覆盖默认版面边距（仅允许长度值，非法值返回错误） |
| `default_css` | boolean | 否 | 是否注入默认排版样式。缺省为自动：自带 `<style>`/`<link>` 样式表的完整 HTML 页面（如 AstrBot 模板）不注入，渲染结果与浏览器预览一致；Markdown、HTML 片段和无样式页面会注入 |
| `options` | object | 否 | Playwright 截图配置选项 |

#### `options` 截图选项支持（同时兼容 snake_case 与 camelCase）：

- `type` (`"jpeg"` \| `"png"`, 默认 `"jpeg"`): 输出图片格式
- `quality` (int, 1-100, 仅适用于 JPEG, 默认 86；未安装 Pillow 或 `T2I_OPTIMIZE=0` 时为 92): 图片压缩质量。已安装 Pillow 时，浏览器输出无损 PNG 后统一编码为渐进式优化 JPEG / 无损优化 PNG，体积明显更小；可用环境变量 `T2I_JPEG_QUALITY` 调整默认质量
- `omit_background` / `omitBackground` (bool, 仅适用于 PNG): 是否开启透明背景
- `full_page` / `fullPage` (bool, 默认 `true`): 是否截取全网页
- `clip` (object `{x, y, width, height}`): 局部区域裁切（传入时自动处理与 `full_page` 的互斥）
- `timeout` (float): 单次截图超时时间（毫秒）
- `animations` (`"allow"` \| `"disabled"`): 是否允许播放 CSS 动画
- `caret` (`"hide"` \| `"initial"`): 是否隐藏光标
- `scale` (`"css"` \| `"device"`): 页面缩放模式
- `mask` (list[string]): 需要遮罩的 CSS 选择器列表
- `compress` (`"auto"` \| `"off"`, 默认取环境变量 `T2I_COMPRESS`): 是否把文字/界面截图转为 256 色 PNG，见下文
- `width` (int, 默认 920): 画布宽度（CSS 像素，输出图片为 2 倍分辨率）；整页截图时图片高度自动贴合内容

#### 图片体积压缩

已安装 Pillow 时默认开启（`T2I_COMPRESS=auto`）。浏览器先输出无损 PNG，文字、卡片、渐变背景类截图再转为 **256 色调色板 PNG**：
调色板由“中位切分 + 加权 k-means 精修”生成，逐像素精确映射到最近色，没有 JPEG 在文字边缘的振铃/毛刺。

| 实测（宽 1840px） | JPEG q86（旧版输出） | **256 色 PNG（2.3.0 起）** |
| :--- | :--- | :--- |
| Markdown 长回复（1840×9948，含 emoji/代码/表格） | 1180KB，PSNR 35dB | **448KB，PSNR 50dB** |
| 渐变背景 + 卡片阴影模板（1840×7196） | 1132KB，PSNR 34dB | **596KB，PSNR 44dB** |
| 含照片的模板（1840×8158） | 1451KB | 自动保持 JPEG（见下） |

**画质门槛**（不满足就自动回退为 JPEG / 无损 PNG，保证不出现色带、色阶断层）：
1. 颜色种类不超过 `T2I_COMPRESS_MAX_COLORS`（默认 65536）；
2. 图片切成 64×64 的块，每块 PSNR 都不低于 `T2I_MIN_TILE_PSNR`（默认 33dB）。页面里夹带照片/头像时全局 PSNR 会被白底“稀释”，逐块检查能精确拦下；emoji、阴影、渐变背景都能通过。

- 开启后即使请求 `type: "jpeg"`，输出也可能是 `.png`（返回的 `id` 与 `Content-Type` 同步变化）；
- 照片较多的页面仍为 JPEG，体积主要由像素数决定，可用 `T2I_SCALE=1.5` 进一步降低（约 -35%）；
- `omit_background: true`（透明背景）时不转 256 色；单次请求可用 `options.compress = "off"` 关闭。

#### Markdown 兼容说明

默认使用 `markdown-it-py`（CommonMark + 表格 + 删除线）解析，并针对大模型输出做了兼容：
单个换行即换行；列表/表格前后缺少空行也能正确识别；`#标题`（无空格）视为标题；代码块内容保持原样。
未安装 `markdown-it-py` 时依次回退到 `markdown` 与内置解析器。

---

### 2. GET `/text2img/data/{id}` 或 `/data/{id}`

根据图片 ID（纯文件名）获取生成的图片文件。

- 支持格式：`GET /text2img/data/rendered_1724734321_abcdef12.jpeg`
- 自动返回正确的 `Content-Type`（如 `image/jpeg`, `image/png`）

---

### 3. GET `/health` 或 `/ping`

健康检查探针接口，返回 `{"status": "ok", "browser_ready": true, "version": "2.4.0", "pillow": true, "numpy": true, "optimize": true, "compress": "auto", "network": "public", "png8_active": true}`。

---

### 环境变量

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `PORT` | `8999` | 服务端口 |
| `T2I_MAX_PAGES` | `4` | 同时渲染的页面数上限；渲染页面会被复用（页面池），超出的请求排队等待 |
| `T2I_COMPRESS` | `auto` | `auto`：文字截图转 256 色 PNG；`off`：只做 JPEG/PNG 优化编码 |
| `T2I_COMPRESS_MAX_COLORS` | `65536` | 颜色种类超过该值（照片）不转 256 色 |
| `T2I_MIN_TILE_PSNR` | `33` | 256 色 PNG 的画质门槛：每个 64×64 块的 PSNR 下限（dB），调高更保守 |
| `T2I_PNG_LEVEL` | `9` | PNG 压缩等级（0–9）；9 比 6 小约 5%，长图多花约 0.15s |
| `T2I_FAST_SHOT` | `1` | 整页截图走 CDP `optimizeForSpeed`（像素一致，截图约快 40%）；`0` 关闭，失败时也会自动回退 |
| `T2I_SCALE` | `2` | 输出分辨率倍数（1–3），图片宽度 = 920 × 倍数。聊天软件（如 QQ）会对收到的图片重新编码，此时体积主要由像素数决定：设为 `1.5` 体积约降 35%，手机全屏查看无可见差别 |
| `T2I_NETWORK` | `public` | 渲染页面的网络访问策略：`public` 只允许访问公网地址；`none` 拦截所有网络请求（只能用内联/data: 资源）；`all` 不拦截（旧行为，仅限可信调用方） |
| `T2I_NETWORK_ALLOW` | 空 | `public` 策略下额外放行的网段（逗号分隔 CIDR）。服务在 fake-ip 透明代理后面时，公网域名会解析到 `198.18.0.0/15`，需设为该网段 |
| `T2I_CHROMIUM_SANDBOX` | `0` | 设为 `1` 时开启 Chromium 沙箱（去掉 `--no-sandbox`），需要宿主允许 user namespace |

### 安全说明

接口会在服务端 Chromium 中渲染调用方提交的任意 HTML，页面里的图片、样式、`fetch`、iframe、WebSocket 都由服务端发起请求。
2.4.0 起默认（`T2I_NETWORK=public`）拦截所有指向内网、回环、链路本地（含云厂商元数据 `169.254.169.254`）及保留地址的请求，`file://` 等非网络协议也一律拒绝，防止借渲染探测或读取内网服务（SSRF）。

- 域名会先解析再判断，但解析与 Chromium 实际连接是两次独立的 DNS 查询，理论上存在 DNS rebinding 绕过窗口；安全要求高时请用 `T2I_NETWORK=none`，或在容器网络层隔离内网；
- 开启请求拦截后 Playwright 会停用 HTTP 缓存，模板引用的外部字体/图片每次都会重新下载，建议尽量内联；
- 镜像以非 root 用户（uid 10001）运行；如果把宿主目录挂载到 `/app/data`，需保证该目录对 uid 10001 可写。
