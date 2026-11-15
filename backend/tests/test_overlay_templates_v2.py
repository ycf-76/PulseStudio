"""叠加模板升级测试 — seekTo 时间轴 / 透明背景 / 防越界 / 词级数据。

对齐验收标准：
  A1 词级时间戳驱动（words 参数注入 + seekTo 逐帧求值）
  A2 无绿幕（不出现 #00FF00）、无越界（max-width 约束）
  A4 字幕质感（大字号 ≥64px、双层描边）
"""

from __future__ import annotations

import pytest

from capabilities.video.adapters.overlay_templates import TEMPLATES


_WORDS = [
    {"text": "今天", "start": 0.0, "end": 0.4},
    {"text": "天气", "start": 0.4, "end": 0.8},
    {"text": "真不错", "start": 0.8, "end": 1.5},
]


# caption 类模板必传 text
_CAPTION_ARGS = {"caption_pop": {"text": "测试文本"}, "caption_karaoke": {"text": "测试文本"}, "caption_typewriter": {"text": "测试文本"}}


def _render(name: str, **extra) -> str:
    fn = TEMPLATES[name]
    kwargs = {"duration": 2.0}
    kwargs.update(_CAPTION_ARGS.get(name, {}))
    kwargs.update(extra)
    return fn(**kwargs)


# ── 1. 全模板通用契约 ─────────────────────────────────────

class TestTemplateContract:
    def test_所有模板_无绿幕色(self):
        for name in TEMPLATES:
            assert "#00FF00" not in _render(name), f"{name} 仍含绿幕色"

    def test_所有模板_含seekTo时间轴(self):
        """确定性渲染契约：渲染器逐帧调用 seekTo(t) 求值元素状态。"""
        for name in TEMPLATES:
            assert "window.seekTo" in _render(name), f"{name} 缺少 seekTo"

    def test_所有模板_背景透明(self):
        for name in TEMPLATES:
            assert "transparent" in _render(name), f"{name} 背景不透明"

    def test_bg_color参数_向后兼容不报错(self):
        """LLM 旧输出可能带 bg_color，必须兼容。"""
        for name in TEMPLATES:
            html = _render(name, bg_color="#00FF00")
            assert "00FF00" not in html  # 接受但忽略


# ── 2. 字幕模板（A1 + A4 核心）────────────────────────────

class TestCaptionTemplates:
    def test_caption_pop_词级数据注入(self):
        html = TEMPLATES["caption_pop"](text="今天天气真不错", words=_WORDS, duration=2.0)
        assert "今天" in html
        # 词级时间数据必须出现在 HTML（驱动 seekTo）
        assert "0.4" in html  # 第二词 start

    def test_caption_pop_大字号默认值(self):
        html = TEMPLATES["caption_pop"](text="测试", duration=2.0)
        assert "72px" in html  # 默认 font_size=72（旧 48）

    def test_caption_pop_双层描边(self):
        html = TEMPLATES["caption_pop"](text="测试", duration=2.0)
        assert "-webkit-text-stroke" in html
        assert "text-shadow" in html

    def test_caption_pop_防越界max_width(self):
        html = TEMPLATES["caption_pop"](text="测试", duration=2.0)
        assert "max-width" in html

    def test_caption_karaoke_词级高亮数据(self):
        html = TEMPLATES["caption_karaoke"](text="今天天气真不错", words=_WORDS, duration=2.0)
        assert "window.seekTo" in html
        assert "真不错" in html

    def test_caption_karaoke_大字号(self):
        html = TEMPLATES["caption_karaoke"](text="测试", duration=2.0)
        assert "68px" in html  # 默认 ≥64

    def test_caption_typewriter_字符级seek(self):
        html = TEMPLATES["caption_typewriter"](text="你好世界", words=_WORDS, duration=2.0)
        assert "window.seekTo" in html


# ── 3. 卡片/图形模板 ──────────────────────────────────────

class TestCardTemplates:
    @pytest.mark.parametrize("name", [
        "title_card", "stat_card", "number_roll",
        "keyword_icon", "keyword_highlight", "lower_third", "bar_chart_grow",
    ])
    def test_卡片模板_含seekTo和透明背景(self, name):
        html = TEMPLATES[name](duration=2.0)
        assert "window.seekTo" in html
        assert "transparent" in html

    def test_number_roll_seek驱动计数(self):
        """数字滚动由 seekTo(t) 驱动，不再是 rAF 自走。"""
        html = TEMPLATES["number_roll"](label="粉丝", value="0", target="3000", duration=3.0)
        assert "window.seekTo" in html
        assert "requestAnimationFrame" not in html  # 自走动画会破坏确定性

    def test_title_card_大标题(self):
        html = TEMPLATES["title_card"](title="三分钟学会", duration=2.0)
        assert "font-size" in html


# ── 4. v3 视觉冲击：亮色色块 / 新模板 ─────────────────────

class TestV3AccentBlocks:
    _WORDS2 = [
        {"text": "防晒", "start": 0.0, "end": 0.5},
        {"text": "还能", "start": 0.5, "end": 0.9},
        {"text": "防老", "start": 0.9, "end": 1.6},
    ]

    def test_caption_pop_重音词亮色色块(self):
        """accent_words 命中词 → 亮色圆角色块弹出（CapCut 贴纸风）。"""
        html = TEMPLATES["caption_pop"](
            text="防晒还能防老", words=self._WORDS2,
            accent_words=["防老"], duration=2.0,
        )
        low = html.lower()
        assert "#ffe066" in low
        assert "border-radius" in low

    def test_caption_pop_多重音词交替亮色(self):
        html = TEMPLATES["caption_pop"](
            text="防晒还能防老", words=self._WORDS2,
            accent_words=["防晒", "防老"], duration=2.0,
        )
        low = html.lower()
        assert "#ffe066" in low and "#ff4d4d" in low

    def test_caption_pop_传color时accent块消费盘内色(self):
        """2026-10-27 第七次 demo o6/o10 实证：palette 规则要求每段
        params.color 来自色盘，但 accent 块写死黄红循环无视 color →
        LLM 传 color="#4ECDC4" 时 accent 块必须用它（不再出现黄红）。"""
        html = TEMPLATES["caption_pop"](
            text="防晒还能防老", words=self._WORDS2,
            accent_words=["防晒", "防老"], color="#4ECDC4", duration=2.0,
        )
        low = html.lower()
        assert "#4ecdc4" in low
        assert "#ffe066" not in low and "#ff4d4d" not in low

    def test_caption_pop_同accent词命中多词同色(self):
        """2026-10-27 第七次 demo o23 实证：accent_words=['特别多'] 被
        分词成'特别'+'多'，黄红交替把语义单元拆成两个色块。修后同一
        accent 词命中的所有 word 必须同色（c 类一致）。"""
        words = [{"text": "效果", "start": 0.0, "end": 0.4},
                 {"text": "特别", "start": 0.4, "end": 0.7},
                 {"text": "多", "start": 0.7, "end": 1.0}]
        html = TEMPLATES["caption_pop"](
            text="效果特别多", words=words,
            accent_words=["特别多"], duration=1.2,
        )
        # '特别' 和 '多' 都是 accent，且同一 c 类（同色）
        import re as _re
        cls_special = _re.search(r'class="word accent (c\d)"[^>]*>特别', html)
        cls_duo = _re.search(r'class="word accent (c\d)"[^>]*>多', html)
        assert cls_special and cls_duo
        assert cls_special.group(1) == cls_duo.group(1)

    def test_section_card_num块消费盘内色(self):
        """2026-10-27 第七次 demo o6 实证：section_card 编号块写死
        accent_color 默认黄，params.color 传红被无视 → color 非空时
        编号块必须用它。"""
        html = TEMPLATES["section_card"](
            number="01", title="空调直吹", color="#FF4D4D", duration=2.0,
        )
        low = html.lower()
        assert "#ff4d4d" in low

    def test_section_card_默认黄不变(self):
        html = TEMPLATES["section_card"](number="01", title="空调直吹", duration=2.0)
        assert "#ffe066" in html.lower()

    def test_section_card_长标题字号自适应单行(self):
        """2026-10-27 第八次 demo round2 o10 实证：7 字标题"出汗别猛灌冰水"
        在固定 54px + max-width 56% 下折行"…猛灌冰"+"水"孤字断义。
        修后长标题缩字号保持单行，不产生孤字行。"""
        html7 = TEMPLATES["section_card"](number="02", title="出汗别猛灌冰水", duration=2.0)
        html5 = TEMPLATES["section_card"](number="01", title="空调直吹", duration=2.0)
        html10 = TEMPLATES["section_card"](number="03", title="长时间暴晒会加速皮肤老化", duration=2.0)
        # 短标题保持原字号
        assert "font-size: 54px" in html5
        # 长标题缩字号 + 禁止折行
        assert "font-size: 44px" in html7
        assert "font-size: 36px" in html10
        assert "white-space: nowrap" in html7

    def test_title_card_长标题字号自适应(self):
        """2026-10-27 第八次 demo round2 o23 实证："夏天舒服过 健康第一位"
        10 字在 80px 下"位"字孤行。修后按字数缩字号避免孤字。"""
        html10 = TEMPLATES["title_card"](title="夏天舒服过 健康第一位", duration=2.0)
        html8 = TEMPLATES["title_card"](title="夏天千万别大意", duration=2.0)
        assert "font-size: 60px" in html10
        assert "font-size: 80px" in html8

    def test_caption_pop_无重音词白字黑边(self):
        html = TEMPLATES["caption_pop"](text="测试文本", duration=2.0)
        assert "color: #FFFFFF" in html
        assert "-webkit-text-stroke" in html

    def test_caption_karaoke_当前词黄底色块(self):
        """CapCut 风卡拉OK：当前词 = 亮黄底色块 + 黑字。"""
        html = TEMPLATES["caption_karaoke"](text="今天天气真不错", words=_WORDS, duration=2.0)
        low = html.lower()
        assert "#ffe066" in low
        assert "background" in low

    def test_stat_card_亮色渐变卡(self):
        html = TEMPLATES["stat_card"](label="防晒指数", value="SPF50", duration=2.0)
        low = html.lower()
        assert "linear-gradient" in low
        assert "#ff4d4d" in low  # 默认亮红渐变

    def test_keyword_icon_白底气泡卡(self):
        html = TEMPLATES["keyword_icon"](keyword="防晒", icon="☀️", duration=2.0)
        low = html.lower()
        assert "#ffffff" in low
        assert "border-radius" in low

    def test_keyword_icon_兼容emoji参数名(self):
        """LLM 习惯传 emoji 键名（prompt 说"emoji 图标"），必须兼容。"""
        html = TEMPLATES["keyword_icon"](keyword="贪凉", emoji="🥶", duration=2.0)
        assert "🥶" in html
        assert "📌" not in html  # 不能落到默认图钉

    def test_keyword_icon_副标题渲染(self):
        """LLM 传 subtitle（如"帽子 · 防晒衣 · 防晒霜"）不能被丢弃。"""
        html = TEMPLATES["keyword_icon"](
            keyword="防晒三件套", icon="🧢", subtitle="帽子 · 防晒衣 · 防晒霜", duration=2.0,
        )
        assert "帽子 · 防晒衣 · 防晒霜" in html

    def test_keyword_highlight_标记笔扫出(self):
        html = TEMPLATES["keyword_highlight"](keyword="一定要防晒", duration=2.0)
        low = html.lower()
        assert "#ffe066" in low
        assert "scalex" in low

    def test_title_card_亮色下划线扫出(self):
        html = TEMPLATES["title_card"](title="三分钟学会防晒", duration=2.0)
        low = html.lower()
        assert "#ffe066" in low
        assert "scalex" in low


class TestV3NewTemplates:
    def test_新模板已注册(self):
        assert "section_card" in TEMPLATES
        assert "emoji_sticker" in TEMPLATES

    def test_section_card_巨型编号色块(self):
        html = TEMPLATES["section_card"](number="01", title="硬防晒攻略", duration=2.0)
        assert "window.seekTo" in html
        assert "01" in html
        low = html.lower()
        assert "#ffe066" in low
        assert "transparent" in low

    def test_section_card_兼容缺省参数(self):
        """LLM 可能只给 title（含"第一"），number 缺省不报错。"""
        html = TEMPLATES["section_card"](title="第一招：硬防晒", duration=2.0)
        assert "window.seekTo" in html

    def test_emoji_sticker_大表情弹跳(self):
        html = TEMPLATES["emoji_sticker"](emoji="🤯", duration=2.0)
        assert "🤯" in html
        assert "window.seekTo" in html
        assert "transparent" in html

    @pytest.mark.parametrize("name", ["section_card", "emoji_sticker"])
    def test_新模板_无绿幕_参数兼容(self, name):
        html = _render(name, bg_color="#00FF00")
        assert "00FF00" not in html
        assert "window.seekTo" in html


# ── 5. v4 画面互动 + 整屏冲击 + 微动 ──────────────────────

class TestV4NewTemplates:
    def test_新模板已注册(self):
        for name in ("arrow_point", "circle_mark", "full_card", "split_compare"):
            assert name in TEMPLATES

    @pytest.mark.parametrize("name,extra", [
        ("arrow_point", {"label": "看这里"}),
        ("circle_mark", {}),
        ("full_card", {"text": "三秒记住"}),
        ("split_compare", {"bad_text": "只涂一次", "good_text": "两小时补涂"}),
    ])
    def test_新模板_渲染契约(self, name, extra):
        html = _render(name, **extra)
        assert "window.seekTo" in html
        assert "transparent" in html
        assert "Math.sin" in html  # v4 持续微动
        assert "requestAnimationFrame" not in html
        html_bg = _render(name, bg_color="#00FF00", **extra)
        assert "00FF00" not in html_bg

    def test_arrow_point_箭头与标签(self):
        html = TEMPLATES["arrow_point"](label="往这看", direction="up", duration=2.0)
        assert "往这看" in html
        assert "polygon" in html.lower()

    def test_arrow_point_方向改变输出(self):
        up = TEMPLATES["arrow_point"](direction="up", duration=2.0)
        right = TEMPLATES["arrow_point"](direction="right", duration=2.0)
        assert up != right

    def test_arrow_point_兼容text键名(self):
        """LLM 习惯传 text 键名，必须落到 label。"""
        html = TEMPLATES["arrow_point"](text="重点", duration=2.0)
        assert "重点" in html

    def test_circle_mark_画圈动画(self):
        low = TEMPLATES["circle_mark"](duration=2.0).lower()
        assert "ellipse" in low
        assert "stroke-dashoffset" in low  # 手绘感"画"圈

    def test_circle_mark_可选标签(self):
        html = TEMPLATES["circle_mark"](label="问题在这", duration=2.0)
        assert "问题在这" in html

    def test_full_card_整屏闪卡(self):
        """整屏色块 + 大字砸入 + 短驻留收束淡出。"""
        low = TEMPLATES["full_card"](text="三秒记住", duration=1.5).lower()
        assert "三秒记住" in low
        assert "position: fixed" in low
        assert "fade" in low  # 收束淡出

    def test_full_card_亮底自动黑字(self):
        """亮色背景自动配黑字（对比度保障）。"""
        html = TEMPLATES["full_card"](text="亮色卡", color="#FFE066", duration=1.5)
        assert "#111111" in html

    def test_full_card_兼容title键名(self):
        html = TEMPLATES["full_card"](title="收束卡", duration=1.5)
        assert "收束卡" in html

    def test_split_compare_左右分屏(self):
        html = TEMPLATES["split_compare"](
            bad_text="熬夜追剧", good_text="早睡护肤", duration=2.5,
        )
        assert "熬夜追剧" in html and "早睡护肤" in html
        assert "❌" in html and "✅" in html

    def test_split_compare_兼容旧键名(self):
        html = TEMPLATES["split_compare"](
            wrong_text="错误做法", right_text="正确做法", duration=2.5,
        )
        assert "错误做法" in html and "正确做法" in html

    def test_新模板_主题色参数兼容(self):
        """palette 透传 + color 参数均不报错且生效。"""
        html = TEMPLATES["full_card"](
            text="A", color="#4ECDC4",
            palette=["#4ECDC4", "#FFE066", "#FF4D4D"], duration=1.5,
        )
        assert "#4ecdc4" in html.lower()


class TestV4IdleMotion:
    @pytest.mark.parametrize("name", sorted(TEMPLATES.keys()))
    def test_全模板_入场后持续微动(self, name):
        """v4：微动由 seekTo 的 t 相位驱动（确定性），全模板覆盖。"""
        html = _render(name)
        assert "Math.sin" in html, f"{name} 无持续微动"
        assert "requestAnimationFrame" not in html


# ── 6. LLM 键名习惯兼容（v4.1 修复：text/sub 别名） ────────

class TestParamAliases:
    """LLM 常传 text/sub 键名，旧模板签名只收 keyword/label/title —
    别名缺失 → 文字为空 → 元素塌缩成小色块（v4 demo 实测事故）。"""

    def test_keyword_icon_收text(self):
        html = TEMPLATES["keyword_icon"](text="贪凉", emoji="🥶", duration=2.0)
        assert "贪凉" in html

    def test_keyword_highlight_收text(self):
        html = TEMPLATES["keyword_highlight"](text="加速皮肤老化", duration=2.0)
        assert "加速皮肤老化" in html

    def test_stat_card_收text_sub(self):
        html = TEMPLATES["stat_card"](
            text="气温一直飙升", sub="正式入夏", duration=2.6,
        )
        assert "气温一直飙升" in html
        assert "正式入夏" in html

    def test_stat_card_color为主题色进卡(self):
        """LLM color=盘内主题色 → 卡片主色，文字保持白（不红字配红卡）。"""
        html = TEMPLATES["stat_card"](
            text="销量翻倍", color="#4ECDC4", duration=2.6,
        )
        assert "#4ECDC4" in html
        assert "#FFFFFF" in html  # 文字仍白

    def test_title_card_收text_sub(self):
        html = TEMPLATES["title_card"](text="标题", sub="副标题", duration=3.0)
        assert "标题" in html
        assert "副标题" in html

    def test_lower_third_收text(self):
        html = TEMPLATES["lower_third"](text="口播素材", duration=2.2)
        assert "口播素材" in html

    def test_number_roll_收text(self):
        html = TEMPLATES["number_roll"](text="涨粉", duration=3.0)
        assert "涨粉" in html

    def test_section_card_收text(self):
        html = TEMPLATES["section_card"](number="01", text="防晒", duration=2.8)
        assert "防晒" in html

    def test_keyword_highlight_color进标记笔(self):
        html = TEMPLATES["keyword_highlight"](
            text="金句", color="#FFE066", duration=2.0,
        )
        assert "#FFE066" in html


# ── 7. JS 选择器目标审计（v4.2 回归：o17 title_card 崩溃事故） ──

class TestJSSelectorTargets:
    """worker 逐帧调 window.seekTo(t) — JS 里 querySelector 到 null
    再取 .style 会让 page.evaluate 抛 TypeError → worker rc=1 →
    整段叠加被跳过（v4 demo o17 事故：LLM 没传 subtitle，
    .subtitle 条件渲染不存在 → sub.style 崩溃）。
    通用防线：JS 引用的每个 class 选择器必须出现在 DOM 中。
    """

    @staticmethod
    def _assert_selectors_exist(name: str, html: str):
        import re

        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.S)
        assert body_match, f"{name}: 无 body"
        body = body_match.group(1)
        js = "".join(re.findall(r"<script>(.*?)</script>", html, re.S))
        selectors = re.findall(
            r"querySelector(?:All)?\(\s*['\"]([^'\"]+)['\"]", js,
        )
        assert selectors, f"{name}: seekTo 未引用任何元素（微动丢失？）"
        for sel in selectors:
            if not sel.startswith("."):
                continue
            # '.caption' → caption；'.half.bad' → half/bad；
            # '.ring ellipse' → 段 'ring ellipse' 取段首 \w+ = ring，
            # 空格后的后代 tag（ellipse）不是 class，天然不参与检查
            parts = [
                m.group(0)
                for seg in sel.split(".")[1:]
                if (m := re.match(r"\w+", seg))
            ]
            # null guard 豁免：变量有 if (x) 保护时引用条件渲染元素合法
            # （v4.2 修复模式：if (sub) sub.style.opacity = p3;）
            var_match = re.search(
                rf"const (\w+) = document\.querySelector(?:All)?\(\s*['\"]"
                + re.escape(sel) + "['\"]",
                js,
            )
            if var_match and re.search(
                rf"if\s*\(\s*{var_match.group(1)}\s*\)", js,
            ):
                continue
            for cls in parts:
                assert re.search(rf'class="[^"]*\b{re.escape(cls)}\b', body), (
                    f"{name}: JS 引用 .{cls}（来自 {sel}）但 DOM 中不存在该 class，"
                    f"且无 null guard → worker rc=1"
                )

    @pytest.mark.parametrize("name", sorted(TEMPLATES.keys()))
    def test_默认参数_选择器齐全(self, name):
        self._assert_selectors_exist(name, _render(name))

    @pytest.mark.parametrize("name", sorted(TEMPLATES.keys()))
    def test_极简参数_选择器齐全(self, name):
        """LLM 只传必填文案、缺 subtitle/label 等可选参数时的场景。"""
        self._assert_selectors_exist(
            name, _render(name, text="测试文案", sub="副标题"),
        )


# ── 8. text_color 显式色对比度防线（2026-10-28 G1 旁路收口） ──

class TestTextColorContrastGuard:
    """G1 真机验证发现的对比度防线唯一漏洞：full_card 显式传 text_color
    直接生效、绕过 _text_on 的 WCAG 自动选色（LLM 传白字压橙底仅 2.8:1
    畅通无阻）。收口：显式色必须过 WCAG — tc 同时用于 110px 大字
    （≥48px 档需 3.0:1）与 38px 副标题（<48px 档需 4.5:1），按"本卡
    最严适用档"校验；不达标回退 _text_on 自动选色（黑白取优恒 ≥4.5）。
    """

    def test_显式达标色_保留(self):
        """深底白字 17:1，尊重 LLM 显式选择。"""
        html = TEMPLATES["full_card"](
            text="深底白字", color="#1A1A2E", text_color="#FFFFFF", duration=1.5,
        )
        assert "#FFFFFF" in html

    def test_显式低对比_回退自动选色(self):
        """白字压橙底 #FF8A00 = 2.36:1（<3.0 大字下限）→ 回退黑字 8.9:1。"""
        html = TEMPLATES["full_card"](
            text="橙底白字", color="#FF8A00", text_color="#FFFFFF", duration=1.5,
        )
        assert "#FFFFFF" not in html
        assert "#111111" in html

    def test_有副标题_按正文档45校验(self):
        """白字压 #FF4D4D = 3.27:1：无 sub 走大字档 3.0 放行；
        有 sub 时 38px 副标题需 4.5 → 必须回退（同色同防线，不分大小字）。"""
        no_sub = TEMPLATES["full_card"](
            text="仅大字", color="#FF4D4D", text_color="#FFFFFF", duration=1.5,
        )
        assert "#FFFFFF" in no_sub
        with_sub = TEMPLATES["full_card"](
            text="大字", sub="副标题同色", color="#FF4D4D",
            text_color="#FFFFFF", duration=1.5,
        )
        assert "#FFFFFF" not in with_sub
        assert "#111111" in with_sub

    def test_无显式色_自动选色回归保护(self):
        """不传 text_color 时行为不变（亮黄底自动黑字）。"""
        html = TEMPLATES["full_card"](text="默认路径", color="#FFE066", duration=1.5)
        assert "#111111" in html

    def test_非法显式色_回退自动选色(self):
        """非 6 位 hex 的显式色无法判定 → 回退自动选色，不盲信。"""
        html = TEMPLATES["full_card"](
            text="非法色", color="#FFE066", text_color="不是颜色", duration=1.5,
        )
        assert "#111111" in html
