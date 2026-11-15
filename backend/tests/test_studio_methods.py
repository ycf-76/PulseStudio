"""studio_methods 方法论装载层测试。

定位钉死：打法/skill = 纯文本规则输入，装载层只做「找到文本 + 拷进工作区」，
不解释、不执行、不翻译规则（测试不针对内容语义断言，只针对装载与落盘行为）。
"""

import pytest

from capabilities.video.skills.studio_methods import (
    install_into_workspace,
    load_method,
)


def _has_chinese(msg: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in msg)


def test_builtin_general_talk_loads():
    m = load_method({"builtin": "general_talk"})
    assert m["source"] == "builtin"
    assert m["name"] == "general_talk"
    assert m["text"].strip()
    # 打法文本按「beat（语义节拍）」组织章节——风格唯一来源的结构性标志
    assert "beat" in m["text"].lower()


def test_builtin_minimal_cards_loads_and_differs():
    a = load_method({"builtin": "general_talk"})["text"]
    b = load_method({"builtin": "minimal_cards"})
    assert b["source"] == "builtin"
    assert b["name"] == "minimal_cards"
    assert b["text"].strip()
    # 两套打法是两种明显不同的文本（结构刻意异构），绝不能雷同
    assert b["text"] != a


def test_builtin_unknown_name_raises_chinese_with_value():
    with pytest.raises(RuntimeError) as ei:
        load_method({"builtin": "no_such_style"})
    msg = str(ei.value)
    assert "no_such_style" in msg
    assert _has_chinese(msg)


def test_path_md_file_reads_directly(tmp_path):
    f = tmp_path / "my_own_skill.md"
    f.write_text("# 我的打法\n按卡片推进，一卡一拍。", encoding="utf-8")
    m = load_method({"path": str(f)})
    assert m == {"name": "my_own_skill", "source": "path",
                 "text": "# 我的打法\n按卡片推进，一卡一拍。"}


def test_path_directory_reads_method_md(tmp_path):
    d = tmp_path / "pack_dir"
    d.mkdir()
    (d / "method.md").write_text("目录形态：规则原文。", encoding="utf-8")
    m = load_method({"path": str(d)})
    assert m == {"name": "pack_dir", "source": "path", "text": "目录形态：规则原文。"}


def test_text_passthrough():
    raw = "纯文本规则：每句话配一个数字出现。"
    m = load_method({"text": raw})
    assert m == {"name": "inline", "source": "text", "text": raw}


def test_install_into_workspace_copies_and_idempotent(tmp_path):
    ws = {"inputs": tmp_path / "ws_inputs"}
    ws["inputs"].mkdir()
    method = load_method({"text": "规则原文 A"})
    p = install_into_workspace(ws, method)
    assert p == ws["inputs"] / "rules" / "method.md"
    assert p.is_file()
    assert p.read_text(encoding="utf-8") == "规则原文 A"
    # 幂等：重复调用不报错，内容一致（只覆盖拷贝，不追加不改写）
    assert install_into_workspace(ws, method) == p
    assert p.read_text(encoding="utf-8") == "规则原文 A"


def test_install_creates_missing_parent_dirs(tmp_path):
    ws = {"inputs": tmp_path / "deep" / "nested" / "inputs"}  # 父目录不存在
    method = load_method({"text": "规则原文 B"})
    p = install_into_workspace(ws, method)
    assert p == ws["inputs"] / "rules" / "method.md"
    assert p.read_text(encoding="utf-8") == "规则原文 B"


def test_invalid_ref_empty_or_multi_key():
    with pytest.raises(RuntimeError) as e1:
        load_method({})
    assert _has_chinese(str(e1.value))
    with pytest.raises(RuntimeError) as e2:
        load_method({"builtin": "general_talk", "text": "重复指定"})
    assert _has_chinese(str(e2.value))


def test_invalid_ref_wrong_types():
    for bad in (None, "general_talk", 42, {"builtin": 123}, {"path": 7},
                {"text": 3.5}, {"unknown_key": "x"}):
        with pytest.raises(RuntimeError) as ei:
            load_method(bad)
        assert _has_chinese(str(ei.value)), bad


def test_path_missing_raises_with_actual_value(tmp_path):
    missing = tmp_path / "ghost" / "method.md"
    with pytest.raises(RuntimeError) as ei:
        load_method({"path": str(missing)})
    assert str(missing) in str(ei.value)
    assert _has_chinese(str(ei.value))
