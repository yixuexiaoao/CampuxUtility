"""截图体积压缩：文字/界面截图转 256 色调色板 PNG，带质量门槛

文字、卡片、渐变背景这类截图的颜色种类有限 (含抗锯齿边缘通常几千到几万种)，非常适合调色板 PNG：
- 体积约为 JPEG q86 的 1/2 ~ 1/3，且没有 JPEG 在文字边缘产生的“毛刺/振铃”；
- 画质：调色板由“中位切分 + 加权 k-means 精修”得到，再逐像素精确映射到最近色，
  文字页 PSNR ≈ 50dB、渐变卡片模板 ≈ 44dB (JPEG q86 在同样图片上只有 ≈ 34dB)。

质量门槛 (任一不满足就返回 None，由调用方回退到 JPEG/无损 PNG，避免色带/色阶断层)：
1. 颜色种类不超过 max_colors (默认 65536)；
2. 把图片切成 64×64 的块，每一块的 PSNR 都不低于 min_tile_psnr (默认 33dB)。
   全局 PSNR 会被大片白底“稀释”，页面里夹一张小照片时照片可能已经出现色阶断层；
   逐块检查能精确拦下这种情况，而 emoji、阴影、渐变背景都能通过。
"""
import io
import math

try:
    from PIL import Image
except ImportError:  # 未安装 Pillow 时整个压缩功能自动关闭
    Image = None

try:
    import numpy as np
except ImportError:  # 无 numpy 时退回 Pillow 自带量化 (更快但无质量门槛，仅在颜色很少时使用)
    np = None

DEFAULT_MAX_COLORS = 65536
DEFAULT_MIN_TILE_PSNR = 33.0
DEFAULT_PNG_LEVEL = 9
# 无 numpy 时只对颜色很少的图片做调色板化 (此时没有逐块质量检查，保守处理)
_NO_NUMPY_MAX_COLORS = 4096
_TILE = 64
_SMALL_ERR = 2  # 欧氏误差不超过该值的像素视为“小误差”，按上界计入块误差
_KMEANS_ITERS = 4
_ROWS_PER_CHUNK = _TILE * 16  # 分块映射，避免长图一次性生成数百 MB 的临时数组


def is_available() -> bool:
    return Image is not None


# ---------------------------------------------------------------------------
# 调色板构建 (只在“唯一色”上计算，几万个点，很快)
# ---------------------------------------------------------------------------
def _nearest(cols_f, pal_f, chunk: int = 8192):
    """每个颜色的最近调色板索引与欧氏平方误差：|c|^2 - 2c·p + |p|^2 (矩阵乘法，比三维广播快一个数量级)"""
    pal_norm = (pal_f * pal_f).sum(1)
    idx = np.empty(len(cols_f), dtype=np.uint8)
    err = np.empty(len(cols_f), dtype=np.float32)
    for i in range(0, len(cols_f), chunk):
        c = cols_f[i:i + chunk]
        d = pal_norm[None, :] - 2.0 * (c @ pal_f.T)
        j = d.argmin(1)
        idx[i:i + chunk] = j
        err[i:i + chunk] = np.maximum(0.0, d[np.arange(len(c)), j] + (c * c).sum(1))
    return idx, err


def _build_palette(cols, counts):
    """中位切分得到初始调色板，再做几轮加权 k-means 精修 (PSNR 约 +3dB，大误差像素减少一个数量级)"""
    # 频次开方加权：既照顾大面积背景色，又不让它独占调色板，抗锯齿边缘/emoji 也能分到颜色
    weights = np.sqrt(counts)
    reps = np.maximum(1, np.round(weights).astype(np.int64))
    sample = np.repeat(cols.astype(np.uint8), reps, axis=0).reshape(1, -1, 3)
    pq = Image.fromarray(sample).quantize(256, method=Image.Quantize.MEDIANCUT)
    pal = np.array(pq.getpalette()[: 256 * 3], dtype=np.float32).reshape(-1, 3)

    cols_f = cols.astype(np.float32)
    n = len(pal)
    for _ in range(_KMEANS_ITERS):
        idx, _ = _nearest(cols_f, pal)
        wsum = np.bincount(idx, weights, n)
        used = wsum > 0
        for ch in range(3):
            s = np.bincount(idx, weights * cols_f[:, ch], n)
            pal[used, ch] = s[used] / wsum[used]
    pal = np.clip(np.round(pal), 0, 255).astype(np.float32)
    idx, err = _nearest(cols_f, pal)
    return pal, idx, err


def _psnr(mse: float) -> float:
    # err 为三通道平方和，按通道平均后计算 PSNR
    return 99.0 if mse <= 1e-9 else 10.0 * math.log10(65025.0 * 3 / mse)


def _map_and_check(im, cols, idx, err, min_tile_psnr: float):
    """逐像素精确映射到调色板 (24bit 查找表)，同时按 64×64 块统计误差；任一块不达标返回 None

    Pillow 的 quantize(palette=...) 映射是近似的 (PSNR 会掉 5~10dB)，所以自己做精确映射。
    """
    keys = cols[:, 0] | (cols[:, 1] << 8) | (cols[:, 2] << 16)  # 与下面 RGBX 小端视图一致
    max_tile_mse = 65025.0 * 3 / (10 ** (min_tile_psnr / 10)) if min_tile_psnr > 0 else float("inf")
    # 误差 <= 2 的像素不逐个统计，按上界 2² 计入每个块 (略偏严格)；其余像素按四舍五入后的误差累加
    small = _SMALL_ERR * _SMALL_ERR
    need_check = float(err.max()) > max_tile_mse
    if need_check:
        # 低字节放调色板索引，高字节放“大误差”颜色的误差 (欧氏距离四舍五入，截断到 255)，一次查表同时得到两者
        big_err = np.where(err > small, np.minimum(255, np.round(np.sqrt(err))), 0).astype(np.uint16)
        lut = np.zeros(1 << 24, dtype=np.uint16)
        lut[keys] = idx.astype(np.uint16) | (big_err << 8)
    else:
        # 所有颜色的误差都低于块门槛，任何块都不可能不达标，跳过逐块统计
        lut = np.zeros(1 << 24, dtype=np.uint8)
        lut[keys] = idx

    w, h = im.size
    packed = np.frombuffer(im.convert("RGBX").tobytes(), dtype=np.uint32).reshape(h, w)
    out = np.empty((h, w), dtype=np.uint8)
    n_tile_cols = -(-w // _TILE)
    col_sizes = np.minimum(_TILE, w - np.arange(n_tile_cols) * _TILE)
    for y in range(0, h, _ROWS_PER_CHUNK):
        v = np.take(lut, packed[y:y + _ROWS_PER_CHUNK] & 0xFFFFFF)
        # uint8 查表直接写入；uint16 查表按 unsafe 截断取低字节 (调色板索引)，省一次按位与
        np.copyto(out[y:y + _ROWS_PER_CHUNK], v, casting="unsafe")
        if not need_check:
            continue
        pos = np.flatnonzero(v >= 256)
        if not pos.size:
            continue
        e = (v.ravel()[pos] >> 8).astype(np.float64)
        rows = v.shape[0]
        n_tile_rows = -(-rows // _TILE)
        tile = (pos // w // _TILE) * n_tile_cols + (pos % w) // _TILE
        sums = np.bincount(tile, e * e, n_tile_rows * n_tile_cols)
        row_sizes = np.minimum(_TILE, rows - np.arange(n_tile_rows) * _TILE)
        if (sums / np.outer(row_sizes, col_sizes).ravel()).max() + small > max_tile_mse:
            return None
    return out


def _png8_numpy(im, color_counts, min_tile_psnr: float):
    counts = np.fromiter((n for n, _ in color_counts), dtype=np.float64, count=len(color_counts))
    cols = np.array([c for _, c in color_counts], dtype=np.int64)
    if len(cols) <= 256:
        # 颜色本来就不超过 256 种：完全无损
        pal = cols.astype(np.float32)
        idx = np.arange(len(cols), dtype=np.uint8)
        err = np.zeros(len(cols), dtype=np.float32)
    else:
        pal, idx, err = _build_palette(cols, counts)
        # 先用唯一色加权误差估算全局 PSNR，明显不达标的直接放弃，省掉整图映射
        if _psnr(float((counts * err).sum() / counts.sum())) < min_tile_psnr:
            return None
    out_idx = _map_and_check(im, cols, idx, err, min_tile_psnr)
    if out_idx is None:
        return None
    out = Image.frombuffer("P", im.size, out_idx, "raw", "P", 0, 1)
    out.putpalette(pal.astype(np.uint8).ravel().tobytes())
    return out


def _to_png8_image(im, max_colors: int, min_tile_psnr: float):
    """RGB 图像 -> 调色板图像；不满足质量门槛时返回 None"""
    if np is None:
        max_colors = min(max_colors, _NO_NUMPY_MAX_COLORS)
    color_counts = im.getcolors(max_colors)
    if color_counts is None:  # 颜色种类超限 (照片/大面积渐变)，getcolors 会提前返回，开销很小
        return None
    if np is not None:
        return _png8_numpy(im, color_counts, min_tile_psnr)
    return im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def _fully_opaque(im) -> bool:
    if im.mode in ("RGBA", "LA"):
        return im.getchannel("A").getextrema()[0] == 255
    return not (im.mode == "P" and "transparency" in im.info)


def _save_png8(out, png_level: int) -> bytes:
    buf = io.BytesIO()
    out.save(buf, "PNG", compress_level=png_level)
    return buf.getvalue()


def encode(png_bytes: bytes, img_type: str, quality: int, png8: bool = True,
           max_colors: int = DEFAULT_MAX_COLORS, min_tile_psnr: float = DEFAULT_MIN_TILE_PSNR,
           png_level: int = DEFAULT_PNG_LEVEL) -> tuple[bytes, str]:
    """把浏览器输出的无损 PNG 编码为最终图片，只解码一次。返回 (数据, 后缀)

    png8=True 时优先尝试 256 色 PNG，不满足质量门槛再按 img_type 回退：
    - jpeg：渐进式 + 哈夫曼表优化；quality >= 95 时关闭色度抽样以保留彩色文字边缘；
    - png ：无损，仅提高压缩等级，像素完全一致 (保留透明通道)。
    """
    with Image.open(io.BytesIO(png_bytes)) as src:
        src.load()
        im = src
        if png8 and im.mode in ("RGB", "RGBA", "P", "L") and _fully_opaque(im):
            # 有真实透明像素时调色板 PNG 会丢失透明度，此时不转
            out = _to_png8_image(im if im.mode == "RGB" else im.convert("RGB"), max_colors, min_tile_psnr)
            if out is not None:
                return _save_png8(out, png_level), "png"
        buf = io.BytesIO()
        if img_type == "png":
            im.save(buf, "PNG", compress_level=png_level)
            return buf.getvalue(), "png"
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True,
                subsampling=0 if quality >= 95 else 2)
        return buf.getvalue(), "jpeg"


def to_png8(png_bytes: bytes, max_colors: int = DEFAULT_MAX_COLORS, png_level: int = DEFAULT_PNG_LEVEL,
            min_tile_psnr: float = DEFAULT_MIN_TILE_PSNR) -> bytes | None:
    """把无损 PNG 截图转为 256 色 PNG；颜色过多或画质不达标时返回 None (兼容旧接口)"""
    with Image.open(io.BytesIO(png_bytes)) as src:
        if src.mode not in ("RGB", "RGBA", "P", "L"):
            return None
        im = src.convert("RGB")
    out = _to_png8_image(im, max_colors, min_tile_psnr)
    return None if out is None else _save_png8(out, png_level)
