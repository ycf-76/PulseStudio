"""2026-11-03 平台缺口修复验证：

1. 文件沙箱：file_read/file_write 放行 backend/outputs 白名单根，
   同时 .env / 项目根仍拒绝（红线不破）。
2. vl_analyze：不再依赖 skill_call 注入的文本 LLM，惰性构建
   QwenVLAdapter；显式注入 llm 时走注入路径。解析/工具函数纯单测。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fix 1: sandbox extra roots
# ---------------------------------------------------------------------------


def test_outputs_dir_allowed_for_read_write(tmp_path):
    from app.engine.reactor.file_ops_core import read_file, write_file

    target = Path(__file__).resolve().parents[1] / "outputs" / "_gapfix_test" / "a.txt"
    rel = str(target)

    w = write_file(rel, "hello p2m")
    assert w["ok"] is True, w
    r = read_file(rel)
    assert r["ok"] is True
    assert "hello p2m" in r["content"]


def test_env_file_still_rejected():
    """红线回归：.env 与项目根任意文件仍不可读。"""
    from app.engine.reactor.file_ops_core import read_file, resolve_and_guard

    backend_dir = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError):
        resolve_and_guard(str(backend_dir / ".env"))
    assert read_file(str(backend_dir / ".env"))["ok"] is False
    assert read_file(str(backend_dir / "app" / "config.py"))["ok"] is False


def test_outputs_path_relative_to_custom_workspace_rejected():
    """会话级 cwd 指向别处时，outputs 只能以绝对路径访问（白名单按绝对路径判定）。"""
    from app.core.context_vars import current_workspace_root
    from app.engine.reactor.file_ops_core import resolve_and_guard

    token = current_workspace_root.set(
        str(tmp_cwd := Path(__file__).resolve().parents[1] / "app" / "data" / "workspace")
    )
    try:
        # 绝对路径 → 放行
        p = resolve_and_guard(
            str(tmp_cwd.parent.parent.parent / "outputs" / "_gapfix_test" / "a.txt")
        )
        assert "outputs" in str(p)
        # 相对路径落在 workspace 内 → 放行
        assert resolve_and_guard("x.txt").is_relative_to(tmp_cwd)
    finally:
        current_workspace_root.reset(token)


# ---------------------------------------------------------------------------
# Fix 2: vl_analyze
# ---------------------------------------------------------------------------


def _png_b64() -> str:
    # 1x1 红色 PNG
    return base64.b64encode(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415408d763f8cfc00000030101"
            "00c9fe92ef0000000049454e44ae426082"
        )
    ).decode()


class _FakeVL:
    """模拟 QwenVLAdapter：记录消息、返回固定 JSON。"""

    model_name = "fake-vl"

    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        content = messages[0]["content"]
        assert {"image": content[1]["image"]}["image"].startswith("data:image/png;base64,")
        return {
            "content": '```json\n{"tags": ["摩托车", "夜景"], "mood": "酷炫",'
            ' "composition": "对角线构图", "color_palette": ["黑", "橙"]}\n```',
            "token_usage": 10,
        }


@pytest.mark.asyncio
async def test_vl_analyze_with_injected_llm():
    from app.skills.analyze.vl_analyze import VLAnalyzeSkill

    fake = _FakeVL()
    skill = VLAnalyzeSkill(llm=fake)
    result = await skill.execute({"image_base64": _png_b64(), "account_id": ""})
    assert result["ok"] is True
    assert result["tags"] == ["摩托车", "夜景"]
    assert result["mood"] == "酷炫"
    assert result["composition"] == "对角线构图"
    assert result["color_palette"] == ["黑", "橙"]


@pytest.mark.asyncio
async def test_vl_analyze_via_image_path():
    """image_path 通道：智能体只给路径，技能走沙箱守卫自行读图编码。"""
    from app.skills.analyze.vl_analyze import VLAnalyzeSkill

    png_path = Path(__file__).resolve().parents[1] / "outputs" / "_gapfix_test" / "img.png"
    png_path.write_bytes(base64.b64decode(_png_b64()))

    fake = _FakeVL()
    skill = VLAnalyzeSkill(llm=fake)
    result = await skill.execute({"image_path": str(png_path)})
    assert result["ok"] is True
    assert len(fake.calls) == 1

    # 沙箱外路径必须拒绝（红线回归）
    outside = Path(__file__).resolve().parents[1] / ".env"
    with pytest.raises(ValueError):
        await skill.execute({"image_path": str(outside)})


def test_vl_analyze_helpers():
    from app.skills.analyze.vl_analyze import _flatten_content, _parse_analysis, _to_data_uri

    assert _to_data_uri("abc").startswith("data:image/png;base64,abc")
    assert _to_data_uri("data:image/jpeg;base64,abc") == "data:image/jpeg;base64,abc"
    # DashScope list content 拉平
    assert _flatten_content([{"text": "a"}, {"text": "b"}]) == "ab"
    assert _flatten_content("plain") == "plain"
    # JSON 解析 + markdown 包裹容错
    parsed = _parse_analysis(
        '前言 ```json\n{"tags": ["t1"], "mood": "m", "composition": "c", "color_palette": ["x"]}\n``` 后记'
    )
    assert parsed["tags"] == ["t1"] and parsed["mood"] == "m"
    # 完全不合法 → 降级不抛错
    fallback = _parse_analysis("不是 JSON 的输出")
    assert fallback["tags"] == [] and fallback["raw"] == "不是 JSON 的输出"
