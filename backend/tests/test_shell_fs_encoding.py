"""BUG-3 回归（2026-11-13）：shell_fs 编码回退链必须显式兜住 GBK。

根因：Python 开启 UTF-8 mode 时 locale.getpreferredencoding(False) 也返回
utf-8，原来的「UTF-8 → 系统首选编码」等于没有 GBK 兜底，Windows 中文
子进程（bash/ffmpeg 等）输出全变替换字符。

全离线、零真机、零网络。
"""

from __future__ import annotations

import locale
from pathlib import Path

import pytest

from app.tools.common.shell_fs import _encoding_chain, decode_output, read_text


@pytest.fixture(autouse=True)
def force_utf8_locale(monkeypatch):
    """模拟最坏情况：系统首选编码也是 utf-8（UTF-8 mode / 英文 locale）。"""
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "utf-8")


def test_encoding_chain_contains_gbk():
    chain = _encoding_chain()
    assert "gbk" in chain, f"回退链缺 GBK: {chain}"
    assert chain[0] == "utf-8", "UTF-8 必须优先，避免把合法 UTF-8 误判成 GBK"


def test_utf8_text_unchanged():
    text = "中文路径 ✅"
    assert decode_output(text.encode("utf-8")) == text


def test_gbk_text_decoded():
    assert decode_output("中文路径".encode("gbk")) == "中文路径"


def test_ascii_unchanged():
    assert decode_output(b"hello world\n") == "hello world\n"


def test_invalid_bytes_do_not_raise():
    out = decode_output(b"\xff\xfe\x00\x80")
    assert isinstance(out, str), "非法字节也要能兜住，不能抛异常"


def test_read_text_gbk_file(tmp_path: Path):
    p = tmp_path / "gbk.txt"
    p.write_bytes("空调直吹 入夏".encode("gbk"))
    assert read_text(p) == "空调直吹 入夏"


def test_read_text_utf8_file(tmp_path: Path):
    p = tmp_path / "utf8.txt"
    p.write_text("中文内容", encoding="utf-8")
    assert read_text(p) == "中文内容"
