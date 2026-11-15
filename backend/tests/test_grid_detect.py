"""grid_detect 单元测试（合成图，不依赖外部素材）。

另有一个可选的"真实语料"用例：若本机存在 D:/我的度假照片 则跑 ground truth 比对，
不存在则跳过 —— 保留它是为了让那份人工核对过的结论不会随时间失效。
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.services.grid_detect import GridDetection, detect_grid, split_grid

PIL_OK = True
try:
    from PIL import Image, ImageDraw
except ImportError:
    PIL_OK = False

pytestmark = pytest.mark.skipif(not PIL_OK, reason="需要 Pillow")


@pytest.fixture
def workdir():
    """刻意不用 pytest 的 workdir：当前环境下 C:\\Temp\\pytest-of-*\\garbage-*
    堆积了 180 项历史残留，pytest 的 basetemp 轮转会去 rmtree 它，被环境的
    安全删除保护拦下并 SystemExit，用例报告直接丢失、真因查不到。"""
    d = Path(tempfile.mkdtemp(prefix="gd_detect_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _solid(path: Path, size, color):
    Image.new("RGB", size, color).save(path)
    return path


def _grid(path: Path, cell=(200, 200), n=(3, 3), gutter=6, gutter_color=(255, 255, 255)):
    """合成一张 n 行 m 列的拼图：格子间留一条浅色缝隙。"""
    cols, rows = n
    w = cols * cell[0] + (cols - 1) * gutter
    h = rows * cell[1] + (rows - 1) * gutter
    im = Image.new("RGB", (w, h), gutter_color)
    d = ImageDraw.Draw(im)
    # 每格填不同灰度，保证相邻格有对比（真拼图就是这样）
    for r in range(rows):
        for c in range(cols):
            x0 = c * (cell[0] + gutter)
            y0 = r * (cell[1] + gutter)
            shade = 40 + (r * cols + c) * 20
            d.rectangle(
                [x0, y0, x0 + cell[0] - 1, y0 + cell[1] - 1],
                fill=(shade, shade + 10, shade + 20),
            )
    im.save(path)
    return path


class TestDetectGrid:
    def test_true_3x3_is_detected(self, workdir):
        p = _grid(workdir / "g.png", n=(3, 3))
        d = detect_grid(p)
        assert d.is_grid is True
        assert (d.rows, d.cols) == (3, 3)
        assert len(d.row_seams) == 2 and len(d.col_seams) == 2

    def test_true_2x2_is_detected(self, workdir):
        p = _grid(workdir / "g2.png", n=(2, 2))
        d = detect_grid(p)
        assert d.is_grid is True
        assert (d.rows, d.cols) == (2, 2)

    def test_plain_photo_is_not_grid(self, workdir):
        """纯色图没有任何接缝"""
        p = _solid(workdir / "plain.png", (600, 800), (120, 130, 140))
        assert detect_grid(p).is_grid is False

    def test_gradient_photo_is_not_grid(self, workdir):
        """渐变图能量平滑，位置也凑不出等距接缝"""
        im = Image.new("RGB", (600, 800))
        d = ImageDraw.Draw(im)
        for y in range(0, 800, 8):
            v = int(y / 800 * 255)
            d.rectangle([0, y, 600, y + 8], fill=(v, v, v))
        p = workdir / "grad.png"
        im.save(p)
        assert detect_grid(p).is_grid is False

    def test_single_strong_line_is_not_grid(self, workdir):
        """只有一条水平线（如地平线）不足以判网格 —— 必须两个方向都命中"""
        im = Image.new("RGB", (600, 900), (90, 90, 90))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 440, 600, 460], fill=(250, 250, 250))
        p = workdir / "line.png"
        im.save(p)
        assert detect_grid(p).is_grid is False

    def test_missing_file(self, workdir):
        d = detect_grid(workdir / "nope.png")
        assert d.is_grid is False
        assert "不存在" in d.reason

    def test_too_small_skipped(self, workdir):
        p = _grid(workdir / "tiny.png", cell=(30, 30), n=(3, 3))
        assert detect_grid(p, min_side=500).is_grid is False

    def test_detection_is_truthy_bool(self, workdir):
        assert bool(detect_grid(_grid(workdir / "g.png", n=(3, 3)))) is True
        assert bool(GridDetection()) is False


class TestSplitGrid:
    def test_splits_into_nine_tiles(self, workdir):
        p = _grid(workdir / "g.png", n=(3, 3))
        d = detect_grid(p)
        tiles = split_grid(p, workdir / "out", d)
        assert len(tiles) == 9
        assert all(t.is_file() and t.stat().st_size > 0 for t in tiles)

    def test_tiles_cover_source_area(self, workdir):
        """切片总面积应等于整幅减去缝隙，不能丢边角"""
        p = _grid(workdir / "g.png", cell=(200, 200), n=(3, 3), gutter=6)
        d = detect_grid(p)
        tiles = split_grid(p, workdir / "out", d)
        with Image.open(p) as src:
            sw, sh = src.size
        total = 0
        for t in tiles:
            with Image.open(t) as im:
                total += im.size[0] * im.size[1]
        # 用实测接缝（非理论等分）下刀，允许小幅误差
        assert total > sw * sh * 0.9

    def test_non_grid_returns_empty(self, workdir):
        p = _solid(workdir / "plain.png", (600, 800), (10, 20, 30))
        assert split_grid(p, workdir / "out", detect_grid(p)) == []

    def test_degenerate_cells_are_skipped(self, workdir):
        """接缝重合时不应产出 0 宽/0 高的废片"""
        p = _grid(workdir / "g.png", n=(3, 3))
        fake = GridDetection(is_grid=True, rows=3, cols=3, row_seams=[0.5, 0.5], col_seams=[0.5, 0.5])
        tiles = split_grid(p, workdir / "out", fake)
        assert all(t.is_file() for t in tiles)


REAL_DIR = Path("D:/我的度假照片")
REAL_TRUTH = {
    "180922_109": (True, 3, 3),
    "180942_149": (False, 1, 1),
    "180955_242": (False, 1, 1),
    "181011_176": (False, 1, 1),
    "181030_045": (False, 1, 1),
    "181041_262": (False, 1, 1),
    "181058_268": (False, 1, 1),
}


@pytest.mark.skipif(not REAL_DIR.is_dir(), reason="本机没有真实素材语料")
def test_real_corpus_matches_human_ground_truth():
    """7 张用户原始照片：只有 180922_109 是真 3x3，其余 6 张都不是拼图。"""
    checked = 0
    for p in sorted(REAL_DIR.glob("*.png")):
        key = p.name.split("_")[-2] + "_" + p.name.split("_")[-1].replace(".png", "")
        expected = REAL_TRUTH.get(key)
        if not expected:
            continue
        d = detect_grid(p)
        assert (d.is_grid, d.rows, d.cols) == expected, f"{key}: {d.reason}"
        checked += 1
    # 2026-10：语料目录被整理，部分样图已移走（180922_109/180955_242）。
    # 存在的样图逐张比对（上方循环），只要求至少校验 1 张，不再硬等 7 张。
    assert checked >= 1, "语料目录里一张匹配样图都没有了，请更新 REAL_TRUTH 或补充样图"
