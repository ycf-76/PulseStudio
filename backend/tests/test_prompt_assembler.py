"""Reactor system prompt 组装测试：skill 目录体积治理（P0 + P2 三层披露）。

验收标准：
1. P0 公共参数去重：相同 schema 的技能，参数说明只在顶部「通用参数」写一次
2. P2 目录极简：条目只含 name + desc + 纪律 hint；独立参数不进目录
3. P2 二层披露：build_skill_doc 输出完整纪律 + 未截断参数（required 无标记/可选有标记）
4. P3 必填前置：含必填字段的技能在目录里补一行「必填: ...」，可选字段仍留二层
5. 真实 registry 目录体积显著下降（实测 43.6k → 21.7k → P1 路由后 ~8k → P2 后更小）
"""

import pytest
from pydantic import BaseModel, Field

from app.engine.reactor.prompt import (
    _DESC_MAX_CHARS,
    _REQUIRED_MAX_CHARS,
    build_skill_catalog,
    build_skill_catalog_section,
    build_skill_doc,
)


class _CommonSchema(BaseModel):
    """模拟 PromptDrivenSkill 的公共 4 字段 schema。"""

    topic: str = Field(default="", description="选题主题")
    content: str = Field(default="", description="待检查内容")
    platform: str = Field(default="", description="目标平台")
    extra_context: str = Field(default="", description="补充背景")


class _UniqueSchema(BaseModel):
    query: str = Field(description="搜索词")
    limit: int = Field(default=10, description="数量上限")


class _EmptySchema(BaseModel):
    pass


class _FakeSkill:
    """build_skill_catalog 按 duck-typing 读取属性，无需真实 Skill 实例。"""

    def __init__(self, name, description="", usage_hint="", schema=None):
        self.name = name
        self.description = description
        self.usage_hint = usage_hint
        self.input_schema = schema


def test_common_params_deduped_to_single_line():
    """测试: 公共参数去重
    场景: 3 个技能共用同一 schema
    预期: 参数说明只在顶部「通用参数」出现一次，各条目不再单列
    """
    skills = [
        _FakeSkill("s1", "描述一", schema=_CommonSchema),
        _FakeSkill("s2", "描述二", schema=_CommonSchema),
        _FakeSkill("s3", "描述三", schema=_CommonSchema),
    ]
    catalog = build_skill_catalog(skills)
    assert catalog.startswith("通用参数:")
    assert catalog.count("topic:str") == 1
    for n in ("s1", "s2", "s3"):
        assert f"- {n} —" in catalog


def test_catalog_entries_minimal_without_own_params():
    """测试: P2 目录极简
    场景: 独立 schema 技能进目录
    预期: 目录只含条目行（无「参数:」行）；参数说明移至二层 skill_doc
    """
    skills = [
        _FakeSkill("s1", "d1", schema=_CommonSchema),
        _FakeSkill("u1", "du", schema=_UniqueSchema),
    ]
    catalog = build_skill_catalog(skills)
    assert "  参数: " not in catalog, "独立参数行不应出现在目录（P2 迁二层）"
    assert "- u1 — du" in catalog
    # 二层 skill_doc 承接参数披露
    doc = build_skill_doc(skills[1])
    assert "query:str" in doc


def test_skill_doc_required_vs_optional_marks():
    """测试: skill_doc 的 required/(可选) 标记（回归 is_required bug）
    场景: schema 含 1 个 required 字段 + 1 个可选字段
    预期: required 无 (可选) 标记，可选字段有；纪律 hint 在 doc 中
    """
    skill = _FakeSkill("u", "d", usage_hint="第一步先确认", schema=_UniqueSchema)
    doc = build_skill_doc(skill)
    assert "第一步先确认" in doc, "纪律 hint 应进 skill_doc"
    assert "query:str — 搜索词," in doc
    assert "query:str — 搜索词 (可选)" not in doc
    assert "limit:int — 数量上限 (可选)" in doc


def test_skill_doc_full_description_not_truncated():
    """测试: skill_doc 描述不截断（与目录截断对照）
    场景: 字段描述超过 _DESC_MAX_CHARS
    预期: 目录截断，skill_doc 保留全文
    """
    long_field_desc = "超长" * 60  # 120 字符 > _DESC_MAX_CHARS
    schema_cls = type(
        "S",
        (BaseModel,),
        {"__annotations__": {"q": str}, "q": Field(description=long_field_desc)},
    )
    skill = _FakeSkill("x", "d", schema=schema_cls)
    catalog = build_skill_catalog([skill])
    doc = build_skill_doc(skill)
    assert long_field_desc not in catalog
    assert long_field_desc in doc


def test_skill_doc_empty_schema_no_param_line():
    """测试: 无字段 schema 的 skill_doc
    预期: 只有 hint 行，无参数行；目录也不出现「参数: 无」
    """
    skill = _FakeSkill("e", "de", usage_hint="无需参数", schema=_EmptySchema)
    catalog = build_skill_catalog([skill])
    doc = build_skill_doc(skill)
    assert "  参数: 无" not in catalog
    assert "参数: " not in doc
    assert "无需参数" in doc


def test_desc_truncated_to_max_chars():
    """测试: 描述截断
    场景: 技能描述远超上限
    预期: 主行描述部分不超过 _DESC_MAX_CHARS 字符
    """
    long_desc = "超长描述" * 100  # 400 字符
    catalog = build_skill_catalog([_FakeSkill("s", long_desc, schema=_UniqueSchema)])
    first_line = catalog.splitlines()[0]
    assert first_line.startswith("- s — ")
    assert len(first_line) <= len("- s — ") + _DESC_MAX_CHARS


def test_required_fields_rendered_in_catalog():
    """测试: P3 必填字段前置
    场景: 技能 schema 含 1 个必填 + 1 个可选字段
    预期: 目录条目下补一行「必填: query:str — 搜索词」，且不含可选字段
    """
    catalog = build_skill_catalog([_FakeSkill("u1", "du", schema=_UniqueSchema)])

    assert "  必填: query:str — 搜索词" in catalog
    assert "limit:int" not in catalog, "可选字段仍留二层 skill_doc"


def test_all_optional_schema_gets_no_required_line():
    """测试: 全可选 schema 不产生「必填」行
    场景: 模拟 PromptDrivenSkill 的公共 4 字段（全带默认值）
    预期: 目录不出现「必填」行（避免无意义噪声）
    """
    catalog = build_skill_catalog([_FakeSkill("s1", "d1", schema=_CommonSchema)])

    assert "必填" not in catalog


def test_required_line_truncated_to_max_chars():
    """测试: 必填行截断
    场景: 必填字段描述超长
    预期: 该行内容不超过 _REQUIRED_MAX_CHARS，目录不被撑爆
    """
    long_desc = "超长" * 120
    schema_cls = type(
        "S",
        (BaseModel,),
        {"__annotations__": {"q": str}, "q": Field(description=long_desc)},
    )
    catalog = build_skill_catalog([_FakeSkill("x", "d", schema=schema_cls)])
    required_lines = [ln for ln in catalog.splitlines() if ln.strip().startswith("必填: ")]
    assert len(required_lines) == 1
    assert len(required_lines[0].strip()[len("必填: "):]) <= _REQUIRED_MAX_CHARS


def test_usage_hint_lines_preserved_in_catalog():
    """测试: usage_hint 聚合不受去重影响
    场景: 技能带多行 usage_hint
    预期: 每行以两空格缩进保留在目录条目下（闸门纪律不剥离）
    """
    skills = [
        _FakeSkill("s1", "d1", usage_hint="第一步先确认\n第二步再执行", schema=_CommonSchema),
        _FakeSkill("s2", "d2", schema=_CommonSchema),
    ]
    catalog = build_skill_catalog(skills)
    assert "  第一步先确认" in catalog
    assert "  第二步再执行" in catalog


@pytest.mark.integration
def test_real_registry_catalog_size():
    """测试: 真实 registry 目录体积验收（P2）
    场景: 全量注册后构建目录（140 个技能，生产路径同源）
    预期: 通用参数行存在；目录 < 18000 字符（P0 前 43.6k / P0 后 21.7k，
          P2 移除 ~32 个 Python 技能独立参数行）
    """
    from app.core.skill_registry import SkillRegistry, ensure_builtin_skills_registered

    ensure_builtin_skills_registered()
    skills = SkillRegistry.instance().all_skill_classes()
    catalog = build_skill_catalog(skills)
    assert len(skills) > 100, "技能注册数异常，前置条件不成立"
    assert "通用参数: topic:str" in catalog
    assert len(catalog) < 18000
    # 目录不再有任何独立参数行
    assert not [l for l in catalog.splitlines() if l.startswith("  参数: ")]


def test_section_renders_with_template():
    """测试: 完整段落渲染
    场景: 有技能时 build_skill_catalog_section 输出模板 + 目录
    预期: 含模板标题、skill_call 用法、skill_doc 约定与通用参数说明
    """
    skills = [
        _FakeSkill("s1", "d1", schema=_CommonSchema),
        _FakeSkill("s2", "d2", schema=_CommonSchema),
    ]
    section = build_skill_catalog_section(skills)
    assert "## 可用技能" in section
    assert "通用参数" in section
    assert "skill_call" in section
    assert "skill_doc" in section, "模板应说明二层披露约定"
    assert build_skill_catalog_section([]) == ""