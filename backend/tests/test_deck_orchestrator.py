"""10.20 二期 deck 服务契约测试（parse_deck_json 防御式解析）。"""
import json
import pytest

from capabilities.video.services.deck_orchestrator import parse_deck_json


class TestParseDeckJson:
    def test_normal_array(self):
        raw = '[{"title":"防晒要提前涂","bullets":["出门前 10 分钟","用量一元硬币"]}]'
        slides = parse_deck_json(raw)
        assert slides == [{"title": "防晒要提前涂", "slide_type": "points", "bullets": [{"text": "出门前 10 分钟", "emphasis": "none"}, {"text": "用量一元硬币", "emphasis": "none"}]}]

    def test_wrapped_in_code_fence_and_prose(self):
        raw = '好的，以下是幻灯片：\n```json\n[{"title":"t","bullets":["a"]}]\n```'
        assert parse_deck_json(raw) == [{"title": "t", "slide_type": "points", "bullets": [{"text": "a", "emphasis": "none"}]}]

    def test_bad_json_returns_empty(self):
        assert parse_deck_json("不是 JSON") == []
        assert parse_deck_json("[{broken") == []

    def test_non_array_returns_empty(self):
        assert parse_deck_json('{"title":"t"}') == []

    def test_item_field_type_enforced(self):
        raw = '[{"title":123,"bullets":["a"]},{"title":"t","bullets":"not-list"},' \
              '{"title":"ok","bullets":["a"]}]'
        assert [s["title"] for s in parse_deck_json(raw)] == ["ok"]

    def test_empty_bullets_dropped(self):
        raw = '[{"title":"t","bullets":[]},{"title":"t2","bullets":["","  ","x"]}]'
        slides = parse_deck_json(raw)
        assert slides == [{"title": "t2", "slide_type": "points", "bullets": [{"text": "x", "emphasis": "none"}]}]

    def test_title_truncated_bullets_capped(self):
        raw = '[{"title":"' + "长" * 30 + '","bullets":["' + "b" * 30 + '","c","d","e","f"]}]'
        slides = parse_deck_json(raw)
        assert len(slides[0]["title"]) <= 20
        assert len(slides[0]["bullets"]) <= 4


class TestPersonClearance:
    """10.20 优化：deck 模板底部人物区让位（人物小窗右下角压要点卡）。"""

    def test_clearance_added_to_body_padding(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html("t", ["a"], 1, 6, person_clearance_px=640)
        # padding 三值简写：top 0 / 左右 110 / bottom 640
        assert "padding:0 110px 640px" in html

    def test_zero_clearance_unchanged(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html("t", ["a"], 1, 6)
        assert "padding:0 110px 0px" in html


class TestDeckThemes:
    """10.21 deck 多主题：样式字段全部由主题 dict 注入，非法主题回退 midnight。"""

    def test_three_themes_exist(self):
        from capabilities.video.services.deck_orchestrator import _DECK_THEMES

        assert set(_DECK_THEMES) == {"midnight", "cream", "sunset"}

    def test_theme_contract_fields(self):
        """每个主题必须含全套样式字段（_slide_html 注入点依赖）。"""
        from capabilities.video.services.deck_orchestrator import _DECK_THEMES

        required = {
            "body_bg", "text_color", "bullet_bg", "bullet_border",
            "dot_color", "accent_bg", "muted_color",
        }
        for name, theme in _DECK_THEMES.items():
            assert required <= set(theme), f"{name} 缺字段: {required - set(theme)}"

    def test_midnight_is_default(self):
        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            _slide_html,
        )

        html = _slide_html("t", ["a"], 1, 6)
        assert _DECK_THEMES["midnight"]["body_bg"] in html

    def test_cream_theme_injects_styles(self):
        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            _slide_html,
        )

        theme = _DECK_THEMES["cream"]
        html = _slide_html("t", ["a"], 1, 6, theme="cream")
        assert theme["body_bg"] in html
        assert theme["text_color"] in html
        assert theme["dot_color"] in html

    def test_sunset_theme_injects_styles(self):
        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            _slide_html,
        )

        theme = _DECK_THEMES["sunset"]
        html = _slide_html("t", ["a"], 1, 6, theme="sunset")
        assert theme["body_bg"] in html
        assert theme["accent_bg"] in html

    def test_invalid_theme_falls_back_to_midnight(self):
        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            _slide_html,
        )

        html = _slide_html("t", ["a"], 1, 6, theme="nonexistent")
        assert _DECK_THEMES["midnight"]["body_bg"] in html

    def test_theme_bgs_mutually_distinct(self):
        from capabilities.video.services.deck_orchestrator import _DECK_THEMES

        bgs = {v["body_bg"] for v in _DECK_THEMES.values()}
        assert len(bgs) == 3

    def test_render_deck_slides_passes_theme_to_html(self, tmp_path):
        """render_deck_slides 接受 theme 并注入每页 HTML。"""
        from unittest.mock import MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            render_deck_slides,
        )

        page = MagicMock()
        with patch("playwright.sync_api.sync_playwright") as sp:
            p = sp.return_value.__enter__.return_value
            # render 内部：browser = p.chromium.launch() → new_page()
            p.chromium.launch.return_value.new_page.return_value = page
            render_deck_slides(
                [{"title": "t", "bullets": ["a"]}], tmp_path, theme="cream",
            )

        html = page.set_content.call_args.args[0]
        assert _DECK_THEMES["cream"]["body_bg"] in html

    @pytest.mark.asyncio
    async def test_generate_deck_slides_passes_theme(self, tmp_path):
        """generate_deck_slides 接受 theme 并透传 render_deck_slides。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new=AsyncMock(return_value='[{"title":"t","bullets":["a"]}]'),
        ), patch(
            # render_deck_slides 是同步函数（generate 经 to_thread 调用），
            # mock 用 MagicMock — AsyncMock 会返回未 await 的 coroutine
            "capabilities.video.services.deck_orchestrator.render_deck_slides",
            new=MagicMock(return_value=[tmp_path / "slide_01.png"]),
        ) as render_mock:
            await generate_deck_slides(
                "转录全文", MagicMock(), tmp_path, theme="cream",
            )

        assert render_mock.call_args.kwargs.get("theme") == "cream"


class TestAccentDocumentFlow:
    """10.23-A：accent 装饰线从 absolute 改文档流 — 标题两行不再压字。"""

    def test_accent_in_document_flow(self):
        """两行长标题（渲染必然两行）→ html 无 top:560px 写死，
        accent 元素出现在 h1 之后（文档流顺序）。"""
        from capabilities.video.services.deck_orchestrator import _slide_html

        long_title = "这是一个足够长会换行的标题测试文本" * 3
        html = _slide_html(long_title, ["要点一", "要点二"], 0, 3)
        assert "top:560px" not in html, "accent 不得再用 absolute 写死 top"
        assert "position:absolute" not in html.split(".accent")[1].split("}")[0], \
            ".accent 规则不得含 absolute 定位"
        assert html.index("</h1>") < html.index('class="accent"'), \
            "accent 必须在 h1 之后的文档流位置"

    def test_accent_single_line_still_renders(self):
        """常规短标题 → accent 仍渲染（不因改造丢失）。"""
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html("短标题", ["a"], 0, 2)
        assert 'class="accent"' in html


class TestSlidesPersistence:
    """10.22 deck 确认闸门前置：slides.json 落盘 + 从 JSON 重渲染。"""

    @pytest.mark.asyncio
    async def test_generate_persists_slides_json(self, tmp_path):
        """generate_deck_slides 解析成功后落盘 slides.json（含 slides/theme/
        transcript — retheme 零 LLM 重渲染与 regenerate 反馈的数据源）。"""
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new=AsyncMock(return_value='[{"title":"t","bullets":["a"]}]'),
        ), patch(
            "capabilities.video.services.deck_orchestrator.render_deck_slides",
            new=MagicMock(return_value=[tmp_path / "slide_01.png"]),
        ):
            await generate_deck_slides(
                "转录全文", MagicMock(), tmp_path, theme="cream",
            )

        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["theme"] == "cream"
        assert data["slides"] == [{"title": "t", "slide_type": "points", "bullets": [{"text": "a", "emphasis": "none"}]}]
        assert data["transcript"] == "转录全文"

    def test_render_from_slides_explicit_theme(self, tmp_path):
        """render_deck_from_slides 指定 theme → 覆盖 JSON 内主题渲染。"""
        import json
        from unittest.mock import MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            render_deck_from_slides,
        )

        json_path = tmp_path / "slides.json"
        json_path.write_text(json.dumps({
            "theme": "cream", "transcript": "x",
            "slides": [{"title": "t", "bullets": ["a", "b"]}],
        }, ensure_ascii=False), encoding="utf-8")

        page = MagicMock()
        with patch("playwright.sync_api.sync_playwright") as sp:
            p = sp.return_value.__enter__.return_value
            p.chromium.launch.return_value.new_page.return_value = page
            paths = render_deck_from_slides(
                json_path, tmp_path / "out", theme="sunset",
            )

        html = page.set_content.call_args.args[0]
        assert _DECK_THEMES["sunset"]["body_bg"] in html
        assert len(paths) == 1

    def test_render_from_slides_uses_json_theme_by_default(self, tmp_path):
        """不传 theme → 用 JSON 里记录的首轮主题。"""
        import json
        from unittest.mock import MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            render_deck_from_slides,
        )

        json_path = tmp_path / "slides.json"
        json_path.write_text(json.dumps({
            "theme": "cream", "slides": [{"title": "t", "bullets": []}],
        }, ensure_ascii=False), encoding="utf-8")

        page = MagicMock()
        with patch("playwright.sync_api.sync_playwright") as sp:
            p = sp.return_value.__enter__.return_value
            p.chromium.launch.return_value.new_page.return_value = page
            render_deck_from_slides(json_path, tmp_path / "out")

        html = page.set_content.call_args.args[0]
        assert _DECK_THEMES["cream"]["body_bg"] in html
class TestParseRichContract:
    """10.24-A/B：富要点契约 — bullets [{text, emphasis}] + slide_type，
    旧 str 格式自动映射，非法值白名单回退。"""

    def test_新格式富要点与页型(self):
        import json
        raw = json.dumps([{
            "title": "转化率翻倍", "slide_type": "stat",
            "bullets": [{"text": "3.2%", "emphasis": "number"},
                        {"text": "留存更高", "emphasis": "keyword"}],
        }], ensure_ascii=False)
        assert parse_deck_json(raw) == [{
            "title": "转化率翻倍", "slide_type": "stat",
            "bullets": [{"text": "3.2%", "emphasis": "number"},
                        {"text": "留存更高", "emphasis": "keyword"}],
        }]

    def test_旧str格式自动映射(self):
        raw = '[{"title":"t","bullets":["a","b"]}]'
        assert parse_deck_json(raw) == [{
            "title": "t", "slide_type": "points",
            "bullets": [{"text": "a", "emphasis": "none"},
                        {"text": "b", "emphasis": "none"}],
        }]

    def test_非法emphasis回退none(self):
        raw = '[{"title":"t","bullets":[{"text":"a","emphasis":"big"}]}]'
        assert parse_deck_json(raw)[0]["bullets"][0]["emphasis"] == "none"

    def test_非法slide_type回退points(self):
        raw = '[{"title":"t","slide_type":"fancy","bullets":["a"]}]'
        assert parse_deck_json(raw)[0]["slide_type"] == "points"

    def test_缺text的dict剔除str保留(self):
        raw = '[{"title":"t","bullets":[{"emphasis":"number"},"ok"]}]'
        slides = parse_deck_json(raw)
        assert slides[0]["bullets"] == [{"text": "ok", "emphasis": "none"}]


class TestSlideTypeRender:
    """10.24-B：四页型确定性渲染 — points/quote/stat/compare，非法回退
    points；str bullets 兼容不炸。"""

    def _html(self, bullets, slide_type="points", title="t"):
        from capabilities.video.services.deck_orchestrator import _slide_html
        return _slide_html(title, bullets, 0, 2, slide_type=slide_type)

    def test_points默认版式含要点卡(self):
        html = self._html([{"text": "a", "emphasis": "none"}])
        assert 'class="bullet"' in html

    def test_str_bullet旧形态兼容(self):
        html = self._html(["a"])
        assert 'class="bullet"' in html

    def test_emphasis渲染强调样式(self):
        html = self._html([{"text": "3.2%", "emphasis": "number"},
                           {"text": "留存", "emphasis": "keyword"}])
        assert "em-num" in html
        assert "em-kw" in html

    def test_quote金句页无要点卡(self):
        html = self._html([{"text": "注", "emphasis": "none"}],
                          slide_type="quote", title="少熬夜")
        assert "quote-title" in html
        assert 'class="bullet"' not in html

    def test_stat大数字页(self):
        html = self._html([{"text": "3.2%", "emphasis": "number"}],
                          slide_type="stat", title="转化率")
        assert "stat-num" in html

    def test_compare两栏页(self):
        html = self._html([{"text": "左", "emphasis": "none"},
                           {"text": "右", "emphasis": "none"}],
                          slide_type="compare", title="对比")
        assert "compare-grid" in html

    def test_非法slide_type回退points版式(self):
        html = self._html(["a"], slide_type="fancy")
        assert 'class="bullet"' in html

    def test_render_deck_slides消费slide_type(self, tmp_path):
        """render_deck_slides 从 slides 条目读 slide_type（缺省 points）。"""
        from unittest.mock import MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            render_deck_slides,
        )

        page = MagicMock()
        with patch("playwright.sync_api.sync_playwright") as sp:
            p = sp.return_value.__enter__.return_value
            p.chromium.launch.return_value.new_page.return_value = page
            render_deck_slides(
                [{"title": "t", "slide_type": "quote", "bullets": []}],
                tmp_path,
            )
        assert "quote-title" in page.set_content.call_args.args[0]


def _llm_seq(*responses):
    """llm_prompt_to_text 序列 mock — 按调用序返回；多余调用重复末值；
    Exception 元素抛出。返回 (fake, calls) — calls 含 prompts 列表。"""
    calls = {"n": 0, "prompts": []}

    async def fake(llm, prompt):
        i = calls["n"]
        calls["n"] += 1
        calls["prompts"].append(prompt)
        r = responses[min(i, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    return fake, calls


def _render_mock(tmp_path):
    from unittest.mock import MagicMock, patch
    return patch(
        "capabilities.video.services.deck_orchestrator.render_deck_slides",
        new=MagicMock(return_value=[tmp_path / "slide_01.png"]),
    )


def _llm_patch(fake):
    from unittest.mock import patch
    return patch("app.adapters.llm_base.llm_prompt_to_text", new=fake)


class TestGenerateCapacityAndReview:
    """10.24-C/D：transcript 8000 + 页数上限语义 + 生成后自检重试 ≤2，
    重试解析空/审查异常保留上一版（绝不阻断）。"""

    @pytest.mark.asyncio
    async def test_transcript扩容8000且页数上限语义(self, tmp_path):
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        long = "字" * 8500
        fake, calls = _llm_seq('[{"title":"t","bullets":["a"]}]')
        with _llm_patch(fake), \
                _render_mock(tmp_path):
            await generate_deck_slides(long, None, tmp_path)

        prompt = calls["prompts"][0]
        assert "字" * 40 in prompt, "转录尾部应保留（不再截 4000）"
        assert long not in prompt, "8500 字应截到 8000"
        assert "4-6" in prompt, "张数应为上限语义 4-{n} 自定"

    @pytest.mark.asyncio
    async def test_自检通过共两次调用(self, tmp_path):
        import json
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        gen = json.dumps([{"title": "t", "bullets": ["a"]}], ensure_ascii=False)
        fake, calls = _llm_seq(gen, "OK")
        with _llm_patch(fake), \
                _render_mock(tmp_path):
            await generate_deck_slides("转录", None, tmp_path)
        assert calls["n"] == 2, "生成 + 审查各一次，OK 不重试"

    @pytest.mark.asyncio
    async def test_自检意见触发一轮重试(self, tmp_path):
        import json
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        v1 = json.dumps([{"title": "v1", "bullets": ["a"]}], ensure_ascii=False)
        v2 = json.dumps([{"title": "v2", "slide_type": "quote",
                          "bullets": ["b"]}], ensure_ascii=False)
        fake, calls = _llm_seq(v1, "第2页全是空话，换成金句页", v2, "OK")
        with _llm_patch(fake), \
                _render_mock(tmp_path):
            await generate_deck_slides("转录", None, tmp_path)

        assert calls["n"] == 4, "gen+review + retry-gen+review"
        assert "第2页全是空话" in calls["prompts"][2], "意见拼进重试 prompt"
        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["slides"][0]["title"] == "v2"

    @pytest.mark.asyncio
    async def test_重试上限两轮(self, tmp_path):
        import json
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        seq = ['[{"title":"v1","bullets":["a"]}]', "改",
               '[{"title":"v2","bullets":["a"]}]', "再改",
               '[{"title":"v3","bullets":["a"]}]', "还改"]
        fake, calls = _llm_seq(*seq)
        with _llm_patch(fake), \
                _render_mock(tmp_path):
            await generate_deck_slides("转录", None, tmp_path)

        assert calls["n"] == 6, "gen+review + (gen+review)×2 = 封顶 6 次"
        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["slides"][0]["title"] == "v3"

    @pytest.mark.asyncio
    async def test_重试解析空保留上一版(self, tmp_path):
        import json
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        fake, _ = _llm_seq('[{"title":"v1","bullets":["a"]}]', "改",
                           "这不是 JSON", "OK")
        with _llm_patch(fake), \
                _render_mock(tmp_path):
            await generate_deck_slides("转录", None, tmp_path)
        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["slides"][0]["title"] == "v1"

    @pytest.mark.asyncio
    async def test_审查异常不阻断(self, tmp_path):
        import json
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        fake, calls = _llm_seq('[{"title":"v1","bullets":["a"]}]',
                               RuntimeError("api down"))
        with _llm_patch(fake), \
                _render_mock(tmp_path):
            await generate_deck_slides("转录", None, tmp_path)
        assert calls["n"] == 2, "审查异常不再重试"
        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["slides"][0]["title"] == "v1"


class TestAutoTheme:
    """10.25-E：theme="auto" — LLM 按内容气质自选主题（包裹对象契约）。"""

    def test_parse_deck_theme_wrapped(self):
        from capabilities.video.services.deck_orchestrator import parse_deck_theme

        raw = '{"theme":"cream","slides":[{"title":"t","bullets":["a"]}]}'
        assert parse_deck_theme(raw) == "cream"

    def test_parse_deck_theme_with_prose_fence(self):
        from capabilities.video.services.deck_orchestrator import parse_deck_theme

        raw = '好的：\n```json\n{"theme":"sunset","slides":[{"title":"t","bullets":["a"]}]}\n```'
        assert parse_deck_theme(raw) == "sunset"

    def test_parse_deck_theme_bare_array_returns_none(self):
        from capabilities.video.services.deck_orchestrator import parse_deck_theme

        assert parse_deck_theme('[{"title":"t","bullets":["a"]}]') is None

    def test_parse_deck_theme_out_of_whitelist_returns_none(self):
        from capabilities.video.services.deck_orchestrator import parse_deck_theme

        raw = '{"theme":"neon-blue","slides":[{"title":"t","bullets":["a"]}]}'
        assert parse_deck_theme(raw) is None

    def test_parse_deck_theme_bad_or_missing_returns_none(self):
        from capabilities.video.services.deck_orchestrator import parse_deck_theme

        assert parse_deck_theme("不是 JSON") is None
        assert parse_deck_theme('{"slides":[{"title":"t","bullets":["a"]}]}') is None

    @pytest.mark.asyncio
    async def test_generate_auto_wrapped_uses_llm_theme(self, tmp_path):
        """auto + 包裹输出 → slides.json 落实际主题 + 渲染收实际主题
        + prompt 含三主题气质画像。"""
        import json

        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        wrapped = '{"theme":"sunset","slides":[{"title":"t","bullets":["a"]}]}'
        fake, calls = _llm_seq(wrapped, "OK")
        with _llm_patch(fake), _render_mock(tmp_path) as rm:
            await generate_deck_slides("转录", None, tmp_path, theme="auto")

        prompt = calls["prompts"][0]
        assert all(name in prompt for name in ("midnight", "cream", "sunset")), (
            "prompt 应含三主题气质画像"
        )
        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["theme"] == "sunset", "slides.json 应落 LLM 选中主题而非 auto"
        assert rm.call_args.kwargs["theme"] == "sunset"

    @pytest.mark.asyncio
    async def test_generate_auto_bare_array_falls_back_midnight(self, tmp_path):
        """auto + LLM 输出裸数组（未按包裹契约）→ 降级 midnight。"""
        import json

        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        fake, _ = _llm_seq('[{"title":"t","bullets":["a"]}]', "OK")
        with _llm_patch(fake), _render_mock(tmp_path) as rm:
            await generate_deck_slides("转录", None, tmp_path, theme="auto")

        data = json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["theme"] == "midnight"
        assert rm.call_args.kwargs["theme"] == "midnight"

    @pytest.mark.asyncio
    async def test_generate_explicit_theme_no_auto_section(self, tmp_path):
        """显式主题不拼画像段（行为不变），渲染收显式主题。"""
        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        fake, calls = _llm_seq('[{"title":"t","bullets":["a"]}]', "OK")
        with _llm_patch(fake), _render_mock(tmp_path) as rm:
            await generate_deck_slides("转录", None, tmp_path, theme="cream")

        assert "主题自动选" not in calls["prompts"][0]
        assert rm.call_args.kwargs["theme"] == "cream"


class TestSlideImages:
    """10.26：deck 幻灯片自动配图 — image_prompt 契约 + 万相生图 +
    全屏背景版式（主题罩层），失败降级纯文字绝不阻断。"""

    def test_parse_image_prompt_whitelisted(self):
        """image_prompt 合法收录；非 str 丢弃；超长截 200。"""
        from capabilities.video.services.deck_orchestrator import parse_deck_json

        raw = json.dumps([
            {"title": "t1", "bullets": ["a"], "image_prompt": "sunny beach, warm tones"},
            {"title": "t2", "bullets": ["a"], "image_prompt": 123},
            {"title": "t3", "bullets": ["a"], "image_prompt": "x" * 300},
        ], ensure_ascii=False)
        slides = parse_deck_json(raw)
        assert slides[0]["image_prompt"] == "sunny beach, warm tones"
        assert "image_prompt" not in slides[1]
        assert len(slides[2]["image_prompt"]) == 200

    def test_slide_html_image_background(self, tmp_path):
        """有图 → 全屏 bg-img（data URI）+ 主题罩层 + 文字浮层 .fg。"""
        import base64

        from capabilities.video.services.deck_orchestrator import _slide_html

        img = tmp_path / "img_01.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        html = _slide_html("t", ["a"], 1, 5, image=str(img))
        assert 'class="bg-img"' in html
        assert "data:image/jpeg;base64," + base64.b64encode(
            b"\xff\xd8\xff\xe0fakejpeg"
        ).decode() in html
        assert 'class="bg-shade"' in html
        assert 'class="fg"' in html

    def test_slide_html_no_image_unchanged(self):
        """无图（旧契约）→ 无背景层，纯文字版式不变。"""
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html("t", ["a"], 1, 5)
        assert "bg-img" not in html
        assert "bg-shade" not in html

    def test_slide_html_missing_file_fallback(self, tmp_path):
        """image 文件缺失 → 静默回退纯文字。"""
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html("t", ["a"], 1, 5,
                           image=str(tmp_path / "ghost.jpg"))
        assert "bg-img" not in html

    @pytest.mark.asyncio
    async def test_generate_slide_images_mock(self, tmp_path):
        """key 在 + prompt 在 → 逐页生图落盘并回填 image 路径。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            _generate_slide_images,
        )

        slides = [
            {"title": "a", "bullets": [], "image_prompt": "sunny beach"},
            {"title": "b", "bullets": []},  # 无 prompt → 跳过
        ]
        fake_adapter = MagicMock()
        fake_adapter.generate = AsyncMock(return_value=[b"jpgbytes"])
        settings = MagicMock(dashscope_api_key="sk-test")
        with patch("app.config.get_settings", return_value=settings), \
                patch("capabilities.image_gen.adapters.image_gen.WanxAdapter",
                      return_value=fake_adapter):
            n = await _generate_slide_images(slides, tmp_path)

        assert n == 1
        fake_adapter.generate.assert_awaited_once()
        assert "image_prompt" in fake_adapter.generate.await_args.kwargs.get(
            "prompt", ""
        ) or "sunny beach" in fake_adapter.generate.await_args.args[0]
        saved = tmp_path / "img_01.jpg"
        assert saved.is_file() and saved.read_bytes() == b"jpgbytes"
        assert slides[0]["image"] == str(saved)
        assert "image" not in slides[1]

    @pytest.mark.asyncio
    async def test_generate_slide_images_no_key(self, tmp_path):
        """key 缺失 → 返 0、零调用、不填 image。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            _generate_slide_images,
        )

        slides = [{"title": "a", "bullets": [], "image_prompt": "x"}]
        settings = MagicMock(dashscope_api_key="")
        with patch("app.config.get_settings", return_value=settings):
            n = await _generate_slide_images(slides, tmp_path)
        assert n == 0
        assert "image" not in slides[0]

    @pytest.mark.asyncio
    async def test_generate_slide_images_partial_fail(self, tmp_path):
        """单页生图异常 → 跳过该页，其余页继续（绝不阻断）。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from capabilities.video.services.deck_orchestrator import (
            _generate_slide_images,
        )

        slides = [
            {"title": "a", "bullets": [], "image_prompt": "p1"},
            {"title": "b", "bullets": [], "image_prompt": "p2"},
        ]
        fake_adapter = MagicMock()
        calls = [AsyncMock(side_effect=RuntimeError("api down")),
                 AsyncMock(return_value=[b"ok"])]
        fake_adapter.generate = AsyncMock(
            side_effect=[RuntimeError("api down"), [b"ok"]]
        )
        settings = MagicMock(dashscope_api_key="sk-test")
        with patch("app.config.get_settings", return_value=settings), \
                patch("capabilities.image_gen.adapters.image_gen.WanxAdapter",
                      return_value=fake_adapter):
            n = await _generate_slide_images(slides, tmp_path)
        assert n == 1
        assert "image" not in slides[0]
        assert slides[1]["image"].endswith("img_02.jpg")

    @pytest.mark.asyncio
    async def test_generate_deck_slides_persists_image(self, tmp_path):
        """集成：生图成功 → slides.json 落 image 路径，渲染收到 image。"""
        from unittest.mock import AsyncMock, patch

        from capabilities.video.services.deck_orchestrator import (
            generate_deck_slides,
        )

        wrapped = json.dumps([{
            "title": "t", "bullets": ["a"], "image_prompt": "warm kitchen",
        }], ensure_ascii=False)
        fake, _ = _llm_seq(wrapped, "OK")
        img = tmp_path / "img_01.jpg"
        with _llm_patch(fake), \
                _render_mock(tmp_path) as rm, \
                patch("capabilities.video.services.deck_orchestrator."
                      "_generate_slide_images", new=AsyncMock(return_value=1)) as gi:
            # 生图 mock 副作用：就地填 image 路径（模拟真实回填）
            async def _fake_gen(slides, out_dir):
                slides[0]["image"] = str(img)
                return 1
            gi.side_effect = _fake_gen
            await generate_deck_slides("转录", None, tmp_path)

        import json as _json
        data = _json.loads((tmp_path / "slides.json").read_text(encoding="utf-8"))
        assert data["slides"][0]["image"] == str(img)
        assert data["slides"][0]["image_prompt"] == "warm kitchen"
