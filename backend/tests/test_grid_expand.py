"""_expand_grid_images 的行为契约：拼图拆开用，单图原样用，拆失败绝不丢素材。

注意：本文件**刻意不用 pytest 的 tmp_path fixture**。当前环境下
C:\\Temp\\pytest-of-*\\garbage-* 里堆了 180 项历史残留，pytest 的 basetemp
轮转会去 rmtree 它，被环境的安全删除保护拦下并直接 SystemExit，
导致用例报告被截断、真因查不到。这里自己管临时目录即可绕开。
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.video.skills.multi_image_video import _expand_grid_images


@pytest.fixture
def workdir():
    d = Path(tempfile.mkdtemp(prefix="gd_expand_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _make_image(path: Path, size=(600, 800), color=(120, 130, 140)) -> Path:
    from PIL import Image

    Image.new("RGB", size, color).save(path)
    return path


def _make_grid(path: Path, n=(3, 3), cell=(200, 200), gutter=6) -> Path:
    from PIL import Image, ImageDraw

    cols, rows = n
    w = cols * cell[0] + (cols - 1) * gutter
    h = rows * cell[1] + (rows - 1) * gutter
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for r in range(rows):
        for c in range(cols):
            x0 = c * (cell[0] + gutter)
            y0 = r * (cell[1] + gutter)
            shade = 40 + (r * cols + c) * 20
            d.rectangle(
                [x0, y0, x0 + cell[0] - 1, y0 + cell[1] - 1], fill=(shade, shade + 10, shade + 20)
            )
    im.save(path)
    return path


@pytest.fixture
def fake_asset_lib():
    """save_asset 每次返回一个可解析的假 URL；url_to_local_path 直接回传路径。"""
    counter = {"n": 0}

    async def _save(user_id, filename, content_type, data, **kw):
        counter["n"] += 1
        # URL 由切片文件名决定，不由调用次数决定，便于验证两次展开顺序一致
        return {"id": f"a{counter['n']}", "original_url": f"/uploads/{filename}"}

    return {
        "save_asset": AsyncMock(side_effect=_save),
        "url_to_local_path": MagicMock(side_effect=lambda u: str(Path(u))),
    }


@pytest.fixture(autouse=True)
def _isolate_output(workdir, monkeypatch):
    monkeypatch.setattr("capabilities.video.skills.multi_image_video._OUTPUT_DIR", workdir)
    yield


def _det(reason: str):
    from app.services.grid_detect import GridDetection

    return GridDetection(reason=reason)


@pytest.mark.asyncio
class TestExpandGridImages:
    async def test_grid_is_split_into_tiles(self, workdir, fake_asset_lib):
        g = _make_grid(workdir / "grid.png", n=(3, 3))
        notes: list[str] = []

        with patch.dict("sys.modules", {"app.services.asset_library": MagicMock(**fake_asset_lib)}):
            urls, locals_ = await _expand_grid_images(["/uploads/grid.png"], [str(g)], "u1", notes)

        assert len(urls) == 9
        assert len(locals_) == 9
        assert any("拼图已拆分" in n for n in notes)

    async def test_plain_photo_passes_through(self, workdir, fake_asset_lib):
        p = _make_image(workdir / "photo.png")
        notes: list[str] = []

        with patch.dict("sys.modules", {"app.services.asset_library": MagicMock(**fake_asset_lib)}):
            urls, locals_ = await _expand_grid_images(["/uploads/photo.png"], [str(p)], "u1", notes)

        assert urls == ["/uploads/photo.png"]
        assert locals_ == [str(p)]
        assert notes == []

    async def test_split_failure_keeps_original(self, workdir, fake_asset_lib):
        """拆分炸了也必须保留原图 —— 素材不能因为拆分失败而丢失"""
        g = _make_grid(workdir / "grid.png", n=(3, 3))
        notes: list[str] = []

        with (
            patch.dict("sys.modules", {"app.services.asset_library": MagicMock(**fake_asset_lib)}),
            patch("app.services.grid_detect.split_grid", side_effect=RuntimeError("boom")),
        ):
            urls, locals_ = await _expand_grid_images(["/uploads/grid.png"], [str(g)], "u1", notes)

        assert urls == ["/uploads/grid.png"]
        assert any("拆分失败" in n for n in notes)

    async def test_unreadable_image_is_reported_not_silent(self, workdir, fake_asset_lib):
        """检测做不了要写进 degradation，否则"为什么没拆"事后查不出"""
        notes: list[str] = []

        with (
            patch.dict("sys.modules", {"app.services.asset_library": MagicMock(**fake_asset_lib)}),
            patch(
                "app.services.grid_detect.detect_grid", return_value=_det(reason="依赖缺失：numpy")
            ),
        ):
            urls, _ = await _expand_grid_images(
                ["/uploads/x.png"], [str(workdir / "x.png")], "u1", notes
            )

        assert urls == ["/uploads/x.png"]
        assert any("拼图检测未覆盖" in n for n in notes)

    async def test_order_is_stable_across_calls(self, workdir, fake_asset_lib):
        """两次展开顺序必须一致，否则 refine 时 reference_images 下标会串镜"""
        g = _make_grid(workdir / "grid.png", n=(3, 3))
        args = (["/uploads/grid.png"], [str(g)], "u1")

        with patch.dict("sys.modules", {"app.services.asset_library": MagicMock(**fake_asset_lib)}):
            first, first_locals = await _expand_grid_images(*args, [])
            second, second_locals = await _expand_grid_images(*args, [])

        # 资产库返回的 ID 每次都不同，比较本地切片顺序（同一组固定文件名）
        # 而不是比较两次新保存资产的 URL。
        assert first_locals == second_locals
        assert len(first) == len(second) == 9
