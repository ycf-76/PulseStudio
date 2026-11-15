"""产物隔离：生成类 Skill 的落盘文件名必须按次唯一，不能跨会话/跨作品覆盖。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.produce import tts_piper as piper_mod
from capabilities.video.skills import meme_render as meme_mod


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    return tmp_path


async def test_piper_tts_writes_unique_file_per_call(upload_dir, monkeypatch):
    """同一文本连续合成两次：产物必须是两个文件，且都落在可访问的 /uploads 下。"""

    async def fake_synth(text: str, output_path, **_kwargs) -> str:
        Path(output_path).write_bytes(b"RIFF")
        return str(output_path)

    monkeypatch.setattr(piper_mod, "synthesize_piper", fake_synth)

    skill = piper_mod.PiperTTSSkill()
    first = await skill.execute({"text": "同一段文案"})
    second = await skill.execute({"text": "同一段文案"})

    assert first["output_path"] != second["output_path"]
    assert Path(first["output_path"]).is_file()
    assert Path(second["output_path"]).is_file()
    assert first["card_draft"]["pages"][0]["audioUrl"].startswith("/uploads/tts/")


async def test_meme_render_writes_unique_file_per_call(upload_dir, monkeypatch, tmp_path):
    """两次表情包渲染不能共用 outputs/meme_output.png，否则并发时互相覆盖。"""
    source = tmp_path / "src.png"
    source.write_bytes(b"fake-png")

    async def fake_resolve(self, image_path: str, image_url: str):
        return str(source)

    def fake_render(local_path, **kwargs):
        out = kwargs["output_path"]
        Path(out).write_bytes(b"fake-png")
        return out

    monkeypatch.setattr(meme_mod.MemeRenderSkill, "_resolve_image", fake_resolve)
    monkeypatch.setattr(meme_mod, "render_classic", fake_render)
    monkeypatch.setattr(meme_mod, "render_reaction", fake_render)

    skill = meme_mod.MemeRenderSkill()
    first = await skill.execute({"image_path": str(source), "top_text": "上"})
    second = await skill.execute({"image_path": str(source), "top_text": "上"})

    assert "error" not in first, first
    assert "error" not in second, second
    assert first["output_path"] != second["output_path"]
    assert Path(first["output_path"]).parent == upload_dir / "meme"
    assert first["card_draft"]["pages"][0]["imageUrl"].startswith("/uploads/meme/")
