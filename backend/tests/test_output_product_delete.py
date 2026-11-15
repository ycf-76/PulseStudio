"""outputs 产物删除的单元测试。

测试: outputs 产物删除
场景: 我的空间里删除一条 outputs 目录产物
预期: 只删该目录直属媒体与编辑覆盖层，子目录里的其它产物保留
文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#15.2（产物目录归属「平台生成」）
"""
from __future__ import annotations

import pytest

from app.services import output_index


@pytest.fixture
def product_root(tmp_path, monkeypatch):
    """把产物根目录指到临时目录，避免动到真实 outputs/。"""
    root = tmp_path / "outputs"
    root.mkdir()
    monkeypatch.setenv("MY_WORKS_OUTPUT_ROOT", str(root))
    return root


def _target(rel_path: str) -> dict:
    for product in output_index.scan_products():
        if product["rel_path"] == rel_path:
            return product
    raise AssertionError(f"产物未扫描到: {rel_path}")


def test_delete_product_removes_direct_media_and_overlay_keeps_nested(product_root):
    """直属媒体与覆盖层被删，子目录产物与非媒体文件不动。"""
    directory = product_root / "demo" / "campaign"
    directory.mkdir(parents=True)
    (directory / "p1.png").write_bytes(b"x")
    (directory / "p2.jpg").write_bytes(b"x")
    (directory / "_content_edit.json").write_text("{}", encoding="utf-8")
    (directory / "notes.txt").write_text("keep", encoding="utf-8")
    nested = directory / "detail"
    nested.mkdir()
    (nested / "p3.png").write_bytes(b"x")

    result = output_index.delete_product(_target("demo/campaign"))

    assert result["files"] == 3
    assert not (directory / "p1.png").exists()
    assert not (directory / "p2.jpg").exists()
    assert not (directory / "_content_edit.json").exists()
    assert (directory / "notes.txt").exists()
    assert (nested / "p3.png").exists()

    remaining = {product["rel_path"] for product in output_index.scan_products()}
    assert "demo/campaign" not in remaining
    assert "demo/campaign/detail" in remaining


def test_delete_product_removes_empty_directory(product_root):
    """媒体删光后空目录一并清掉，避免留下空壳目录。"""
    directory = product_root / "demo" / "solo"
    directory.mkdir(parents=True)
    (directory / "p1.png").write_bytes(b"x")

    output_index.delete_product(_target("demo/solo"))

    assert not directory.exists()
    assert not (product_root / "demo").exists()


def test_delete_product_missing_directory_raises_lookup_error(product_root):
    """目录已不存在时抛 LookupError，由 API 层映射成 404。"""
    with pytest.raises(LookupError):
        output_index.delete_product({"rel_path": "not-exist"})


def test_product_dir_rejects_path_traversal(product_root):
    """越界路径一律拒绝，防止删除产物根目录之外的文件。"""
    assert output_index.product_dir("../outside") is None
    assert output_index.product_dir("") is None


def test_delete_output_work_maps_missing_product_to_404(monkeypatch):
    """删除接口按前缀分发：产物不存在返回 404 而不是误删。"""
    from fastapi import HTTPException

    from app.api.routers import my_works

    monkeypatch.setattr(output_index, "get_product", lambda _product_id: None)
    with pytest.raises(HTTPException) as exc_info:
        my_works._delete_output_work("output:deadbeef")

    assert exc_info.value.status_code == 404


def test_delete_artifact_work_rejects_other_owner(monkeypatch):
    """删创作产物先鉴权：非本人作品返回 403，不落到删盘。"""
    import asyncio

    from fastapi import HTTPException

    from app.api.routers import my_works
    from app.services import creative_artifact

    class _ForeignArtifact:
        owner_user_id = "someone-else"

    async def _deny(_artifact, _user_id):
        raise PermissionError("无权访问该作品")

    monkeypatch.setattr(creative_artifact, "load_artifact", lambda _aid: _ForeignArtifact())
    monkeypatch.setattr(creative_artifact, "verify_owner", _deny)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(my_works._delete_artifact_work("artifact:abc", "me"))

    assert exc_info.value.status_code == 403