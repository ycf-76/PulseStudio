"""网格拼图（九宫格/双联图）检测与拆分。

为什么需要它：
  用户上传的「小红书式九宫格长图」本质是一张拼图，一个画面里塞了 9 个场景、
  多个人物分身。直接把它当单张参考图喂给 image2video，模型会把整张拼图当作
  **单一动态画面**去动 —— 9 格各自独立扭动，观感就是"人物变形/鬼畜"。
  这是 2026-10-17 那次成片质量问题最上游的根因。

判据（在 7 张真实素材上实测得出，不是拍脑袋）：
  接缝 = 相邻行/列差异能量远高于中位数的位置。但**能量高低不能单独判网格**：
  单图 180955_242 的列能量峰值 10.24 反而高于真九宫格 180922_109 的 9.75。
  真正的区分点是**位置** —— 只有真拼图的接缝会同时落在两个方向的 1/3 与 2/3：

    文件          真值    行缝位置        列缝位置
    180922_109    3x3     0.34, 0.67     0.33, 0.67
    180942_149    双联    —              边界噪声
    180955_242    单图    0.48           0.0, 0.25, 0.83
    181011_176    单图    0.56, 0.80     0.45, 1.0
    181030_045    单图    —              0.47, 1.0
    181041_262    单图    0.0, 0.64      —
    181058_268    单图    —              —

  所以要同时满足：能量足够高 + 位置贴近 k/n + 排除图片边界噪声。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.grid_types import GridDetection  # noqa: F401  (re-export 供旧调用方)

logger = logging.getLogger(__name__)

# 依赖在**模块级**导入，不在函数里懒加载。原因（踩过一次）：
# numpy 若在 `patch.dict(sys.modules, ...)` 上下文里首次导入，补丁退出时会
# clear() 再还原，把 numpy 从 sys.modules 抹掉；下一次 import 触发 C 扩展二次加载，
# 直接报 "cannot load module more than once per process"，且现象是"单独跑正常、
# 整轮跑就失败"，极难定位。放到模块级，导入时机固定在本模块被 import 的那一刻。
try:
    import numpy as np
    from PIL import Image

    _DEPS_OK = True
    _DEPS_ERR = ""
except ImportError as e:  # pragma: no cover - 缺依赖时的降级分支
    np = None
    Image = None
    _DEPS_OK = False
    _DEPS_ERR = str(e)
    logger.warning(f"[grid_detect] 未安装 numpy/Pillow，网格检测将不可用：{e}")

# 接缝能量的判定倍率：≥ 中位数的这个倍数才算"可疑接缝"
ENERGY_RATIO = 3.0
# 位置容差：实测真拼图的偏差 ≤0.03，取 0.04 留余量
POS_TOL = 0.04
# 边界噪声容差：拼图边缘本身会产生巨大梯度，必须排除
EDGE_MARGIN = 0.03
# 参与检测的候选切分份数（3x3 / 2x2 / 4x4）
_CANDIDATES = (4, 3, 2)


def _seam_energy(path: Path, max_side: int = 600) -> tuple[list[float], list[float]] | None:
    """算归一化后的行/列接缝能量。返回 None 表示读不了这张图。"""
    if not _DEPS_OK:
        return None

    try:
        with Image.open(path) as im:
            im = im.convert("L")
            w, h = im.size
            scale = max_side / max(w, h)
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            arr = np.asarray(im, dtype=np.float32)
    except Exception as e:
        logger.warning(f"[grid_detect] 无法读取图片 {path}：{e}")
        return None

    row_e = np.abs(np.diff(arr, axis=0)).mean(axis=1)
    col_e = np.abs(np.diff(arr, axis=1)).mean(axis=0)

    def _norm(e):
        med = float(np.median(e))
        return (e / (med + 1e-6)).tolist()

    return _norm(row_e), _norm(col_e)


def _peaks(energy: list[float], ratio: float = ENERGY_RATIO) -> list[float]:
    """取能量 ≥ ratio 的归一化位置，排除边界、且彼此间隔 ≥10%（避免同一条缝被重复计入）。"""
    n = len(energy)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: energy[i], reverse=True)
    picked: list[float] = []
    for i in order:
        if energy[i] < ratio:
            break
        pos = i / n
        if pos < EDGE_MARGIN or pos > 1 - EDGE_MARGIN:
            continue
        if any(abs(pos - p) < 0.10 for p in picked):
            continue
        picked.append(pos)
    return sorted(picked)


def _best_division(peaks: list[float], tol: float = POS_TOL) -> tuple[int, list[float]]:
    """在候选份数里挑最大的 n，使得每条 k/n（k=1..n-1）都有实测峰与之对应。"""
    best_n, best_hits = 1, []
    for n in _CANDIDATES:
        hits: list[float] = []
        ok = True
        for k in range(1, n):
            target = k / n
            match = next((p for p in peaks if abs(p - target) <= tol), None)
            if match is None:
                ok = False
                break
            hits.append(match)
        if ok and n > best_n:
            best_n, best_hits = n, hits
    return best_n, best_hits


def detect_grid(image_path: str | Path, *, min_side: int = 200) -> GridDetection:
    """判断一张图是否为网格拼图（3x3 / 2x2 / 4x4）。

    保守设计：判不出来就返回 is_grid=False —— 宁可漏检也不要把一张好照片切碎
    （把单图切成残片正是 2026-10-17 那次"半张脸 + 大片空白"的成因，比不拆更糟）。
    """
    path = Path(image_path)
    if not path.is_file():
        return GridDetection(reason="文件不存在")

    if not _DEPS_OK:
        return GridDetection(reason=f"依赖缺失：需要 numpy 与 Pillow（{_DEPS_ERR}）")

    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception as e:
        return GridDetection(reason=f"无法读取：{e}")

    if max(w, h) < min_side:
        return GridDetection(reason=f"尺寸过小（{w}x{h}）")

    energ = _seam_energy(path)
    if energ is None:
        return GridDetection(reason="依赖缺失或读取失败")
    row_e, col_e = energ

    row_peaks = _peaks(row_e)
    col_peaks = _peaks(col_e)

    rows, row_hits = _best_division(row_peaks)
    cols, col_hits = _best_division(col_peaks)

    if rows < 2 or cols < 2:
        return GridDetection(
            reason=f"未在两个方向同时找到等距接缝（行峰={[round(p,2) for p in row_peaks]}，"
                   f"列峰={[round(p,2) for p in col_peaks]}）",
        )

    return GridDetection(
        is_grid=True,
        rows=rows,
        cols=cols,
        row_seams=row_hits,
        col_seams=col_hits,
        reason=f"检测到 {rows}x{cols} 等距接缝",
    )


def split_grid(
    image_path: str | Path,
    out_dir: str | Path,
    detection: GridDetection,
    prefix: str = "tile",
) -> list[Path]:
    """按检测结果把拼图切成单图，返回切片路径列表（行优先）。

    只用检测到的**实际接缝位置**下刀，不按理论等分 —— 截图类拼图的
    格子边界未必精确落在 1/3，用实测位置能避免切在人脸中间。
    """
    if not detection.is_grid:
        return []
    if not _DEPS_OK:
        return []

    path = Path(image_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        # 接缝的归一化位置 → 像素坐标，两端补 0 与整幅
        xs = [0] + [int(round(p * w)) for p in detection.col_seams] + [w]
        ys = [0] + [int(round(p * h)) for p in detection.row_seams] + [h]

        tiles: list[Path] = []
        for r in range(len(ys) - 1):
            for c in range(len(xs) - 1):
                box = (xs[c], ys[r], xs[c + 1], ys[r + 1])
                if box[2] - box[0] <= 1 or box[3] - box[1] <= 1:
                    continue
                tile = im.crop(box)
                out = out_dir / f"{prefix}_r{r}_c{c}.png"
                tile.save(out, format="PNG")
                tiles.append(out)
    return tiles
