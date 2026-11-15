#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PPT 展示用卡片截图：调用项目双渲染链路，落盘到 ppt/cards。

链路一（结构化）：compose_deck(theme_id + recipe_id) → renderer 编译 HTML → Playwright 截图
链路二（自由风格）：compose_deck(style_hint) → LLM 直出 HTML → Playwright 截图

用法：
    cd backend
    python scripts/gen_ppt_cards.py            # 全部
    python scripts/gen_ppt_cards.py --only 1   # 只跑第 1 组
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT_DIR = ROOT.parent / "ppt" / "cards"
HTML_DIR = ROOT.parent / "outputs" / "ppt-card-html"

DECK_SPECS = [
    {
        "slug": "knowledge_swiss",
        "chain": "structured",
        "theme_id": "swiss",
        "recipe_id": "knowledge_list",
        "topic": "让人越活越清醒的 6 个思维模型",
        "keep": [0, 1, 3],
        "content": """
思维模型不是万能公式，而是遇到复杂问题时能立刻调用的「思考脚手架」。下面 6 个，是我反复用、也反复验证有效的。

1. 第一性原理
把问题拆到不可再拆的事实，再从事实往上重新推导，而不是照着别人的做法改。马斯克算电池成本时，不看行业报价，直接算镍、铝、碳的原料价。用法：遇到「行业都这么做」，先问一句「物理上最少需要什么」。

2. 二阶思维
不只看动作的即时结果，还要看结果引发的结果。降价能带来订单（一阶），但会让老客户等促销、让渠道压货（二阶）。用法：做决定前写下「然后呢」，连问三层。

3. 逆向思维
与其问「怎样成功」，不如问「怎样一定失败」，然后避开。想做好内容，先列出 10 种让人划走的写法：开头铺垫、标题含糊、通篇形容词。避开清单比追求清单更好执行。

4. 能力圈
知道自己懂什么，比知道自己不懂什么更难。圈内的决策靠直觉也靠谱，圈外的决策必须靠数据和请教。用法：给每个重要决定标注「圈内 / 圈外」，圈外的一律放慢。

5. 复利思维
真正拉开差距的不是单次强度，而是可持续的频率。每天写 300 字，一年是 11 万字；每周读一本书，五年是 260 本。用法：把目标改成「最小可持续动作」，先保证不断，再谈加速。

6. 概率思维
世界大部分结果由概率决定，不由努力决定。做 10 次期望值为正的事，允许其中 4 次失败，长期一定赢。用法：把「这次成不成」换成「这件事我能不能重复 100 次」。

避坑：
- 别把模型当结论，它只负责提问
- 别一次用三个模型，会互相打架
- 用完记一句「这次它帮我避开了什么」
""",
    },
    {
        "slug": "story_inkwash",
        "chain": "structured",
        "theme_id": "inkwash",
        "recipe_id": "timeline_story",
        "topic": "《长安的荔枝》：一颗荔枝的千里奔袭",
        "keep": [0, 1, 3],
        "content": """
天宝十四载，长安城的小吏李善德被同僚设局，接下了一个不可能完成的差事：把岭南的鲜荔枝运到长安，赶在六月一日贵妃生辰之前。

天宝十四载 二月
李善德在酒桌上被推为「荔枝使」。荔枝「一日色变，二日香变，三日味变」，而岭南到长安五千余里，常规走法要一个月。他算完这笔账，第一反应是写休书。

三月
他亲赴广州，从当地老农口中问出真相：荔枝离枝即衰，四日之后不可食。想送到长安，必须在四日内跑完五千里的路——这在当时几乎等于要一条命。

四月
他开始做实验：分枝植瓮保根、盐洗、冰镇、换马接力，四条路线同时上路，让驿卒记录每一段荔枝的状态。二十天后，他手里有了一张所有人都没见过的运输表。

五月
路线定稿：梅关道入江西，经湖南、湖北、襄阳，最后入关中。驿站换马不换人，昼夜不停。为了多带一瓮冰，他砍掉了自己一半的行装。

六月一日
一骑红尘，荔枝送到长安，色香未变。代价写在另一本账上：沿途驿马累毙、农户逃役、驿站亏空。一颗荔枝背后，是一整套系统的透支。

尾声
李善德没有升官，被贬岭南。他在那里种下荔枝树，说了一句话：「这一路，我算尽了所有变量，唯独没算人。」

三句读后：
- 真正的难题从来不是技术，是让系统为你让路
- 「不可能」多半是没算过的代名词
- 所有精致的成果，背后都有一笔没人愿意看的账
""",
    },
    {
        "slug": "life_journal",
        "chain": "structured",
        "theme_id": "journal",
        "recipe_id": "knowledge_list",
        "topic": "周末不摆烂：6 小时回血指南",
        "keep": [0, 1, 2],
        "content": """
周五晚上想通的一件事：休息不是把时间清空，而是把注意力换个方向。刷两小时手机不是休息，是让大脑继续加班。

周六 09:00 出门走一条没走过的路
不带目的地，走 40 分钟。陌生的街景会让注意力从「待办」切到「观察」，这是最便宜的换脑方式。

周六 10:00 手冲一杯，不看屏幕
磨豆、注水、等滴完，全程 4 分钟。手上有动作、眼前有结果，这种「可控的小事」最能压住焦虑。

周六 11:00 做一顿只洗一个锅的饭
番茄鸡蛋面、一锅炖、烤箱蔬菜，都行。做饭的价值不在吃，在于「我亲手完成了一件事」。

周六 13:30 睡 25 分钟，不要超过 30
超过 30 分钟会进深睡，醒来更沉。定好闹钟，闭眼躺平即可，不必强迫自己睡着。

周六 15:00 整理一个 1 平方米
只整理书桌一角或一个抽屉。范围小到不可能失败，完成感却真实存在。

周六 16:00 写下周的一件小事
只写一件，写在纸上贴起来。周日晚上不会因为「什么都没准备」而提前焦虑。

三条提醒：
- 别把周末排满，留两段空白给自己
- 别用「效率」衡量休息，休息没有 KPI
- 周日晚上 9 点后不看工作消息
""",
    },
    {
        "slug": "press_freeform",
        "chain": "free",
        "style_hint": (
            "复古报刊头版风格：米黄新闻纸底、纯黑油墨、粗衬线大标题、报头刊名与日期栏、"
            "细线分栏、小字号正文、铅字印刷质感；零圆角、零彩色、零阴影，只有黑白与纸色。"
            "版面必须密：头版为「报头 + 日期栏 + 主标题 + 导语 + 三栏正文 + 数据栏 + 页脚」"
            "七段结构，三栏正文每栏至少两段文字；正文版为「刊头 + 分栏标题 + 四到六条要点 + 页脚」，"
            "任何一页都必须纵向铺满画高，不留大段空白"
        ),
        "topic": "城市周报 · AI 走进日常的这一年",
        "keep": [0, 1, 2],
        "content": """
【头版头条】AI 走进日常的这一年：从演示厅到菜市场

一年前，AI 还是发布会上的演示；一年后，它在菜市场的称重秤上、在外卖平台的调度里、在小学的作文批改中。变化不在于技术多强，而在于它开始承担「没人愿意干的那部分活」。

【二版 · 观察】被替代的不是人，是人的某一段工作

最先消失的不是岗位，而是岗位里最枯燥的那 30%：填表、对账、转写、初稿。留下的人要做判断、要担责任、要跟人打交道。三种能力变得值钱：提问、验收、承担后果。

【三版 · 现场】一家小公司的三个月

城南一家十二人的设计工作室，三月开始用 AI 做初稿，六月把交付周期从两周压到五天。老板说了一句实话：「客户没变多，但我们敢接的活变多了。」代价是，每个人都要重新学怎么审稿。

【四版 · 声音】三种态度

乐观派说这是印刷术时刻；谨慎派说这是把判断权外包；实用派说先把手上的活干完再谈。三种态度都不算错，错的是只用一种态度看一年。

【副刊 · 手记】慢一点也没关系

技术每年都在换名字，人的适应速度还是那么慢。慢不是落后，慢是把自己也算进系统里。
""",
    },
    {
        "slug": "meme_freeform",
        "chain": "free",
        "style_hint": (
            "网络梗图海报风格：粗黑描边、荧光撞色贴纸、超大号黑体标题、手写批注感、"
            "网格或点阵背景，像表情包拼贴但排版精致克制，不用渐变不用玻璃拟态"
        ),
        "topic": "当代青年精神状态图鉴",
        "keep": [0, 1, 2],
        "content": """
症状一：周一综合征
周日晚上 9 点开始心慌，明明什么都没发生。诊断：不是讨厌工作，是讨厌「无法退出」的感觉。处方：周日晚上只安排一件确定会完成的小事，把不确定感挤掉一点。

症状二：手机长在手上
一天解锁 96 次，每次不到 30 秒。诊断：不是上瘾，是在等一个「有人找我」的证据。处方：把通知全关，主动发一条消息给朋友，把等待变成发起。

症状三：社交电量告急
群里 99+ 未读，一个都不想回。诊断：社交不是能力问题，是电量问题。处方：给社交设额度，一周两次深聊，其余用「已看到，晚点回」体面处理。

症状四：努力羞耻
想努力又怕被看见努力，偷偷卷偷偷躺。诊断：怕的不是努力，是努力之后依然普通。处方：把目标改成「今天做完了什么」，而不是「我比别人强多少」。

症状五：三分钟热度
买了课、办了卡、列了计划，第七天全线崩。诊断：热度不是问题，设计才是。处方：把计划砍到「不做会难受」的程度，先活过第 21 天。

最后一句：
精神状态不需要被治好，只需要被看见。看见之后，它自己会松一点。
""",
    },
    {
        "slug": "anime_illustrated",
        "chain": "illustrated",
        "theme_id": "cream",
        "recipe_id": "timeline_story",
        "image_layout": "top",
        "topic": "那个夏天，我们都在等一场雨",
        "image_prompt": (
            "日系动漫插画，夏日午后，湛蓝天空与大片积雨云，电车轨道延伸到远方，"
            "电线杆与电线，路边向日葵，阳光穿过树叶的光斑，一位穿白衬衫的少年背影，"
            "赛璐璐上色，干净利落的线条，清爽通透的光影，高饱和蓝天，电影感构图，"
            "竖幅，无文字，无边框"
        ),
        "content": """
第一幕 · 蝉鸣开始的下午
六月的第一声蝉叫响起来的时候，教室里的风扇正转到第三档。所有人都在假装听讲，其实都在等窗外那朵云长大。

第二幕 · 那条走了三年的路
从学校到家，一千二百步。夏天走起来像三千步。路边的向日葵一年比一年高，我们一年比一年沉默。

第三幕 · 说好的那场雨
天气预报说了三天要下雨，三天都没下。我们把伞背进书包，又原样背回家。等待本身，成了那个夏天唯一的正经事。

第四幕 · 雨真的来了
它在最后一节晚自习下的。整栋楼的人涌到走廊上，没人打伞。雨很大，我们笑得像考完试。

第五幕 · 后来
后来我去了很远的城市，那里也下雨，但雨落下来的声音不一样。有些夏天不会结束，只是被换了个地方继续下。
""",
    },
    {
        "slug": "spongebob_illustrated",
        "chain": "illustrated",
        "theme_id": "dopamine",
        "recipe_id": "knowledge_list",
        "image_layout": "top",
        "topic": "比奇堡打工人生存手册",
        "image_prompt": (
            "卡通海底世界插画，柠檬黄与海洋蓝撞色，粗黑描边，珊瑚礁、海草、气泡、贝壳、沙地，"
            "漂浮的小鱼与海星，元素密集铺满整个画面，细节丰富，扁平高饱和上色，"
            "明亮活泼的儿童绘本质感，无文字，无边框"
        ),
        "content": """
第一条 · 蟹堡王早班综合症
凌晨五点起床煎肉饼，围裙比人还精神。诊断：你不是热爱工作，你是害怕停下来之后听见自己。处方：把第一件事固定成给自己做的，哪怕只有十分钟。

第二条 · 章鱼哥式怨气
嘴上骂骂咧咧，手上活一样没少。诊断：怨气不是懒，是「我值得更好的」这句话没处说。处方：每周找一个具体的、能改的小事，把它改掉。

第三条 · 派大星式躺平
计划表写满一页，执行率百分之三。诊断：不是没能力，是目标太大，大到不敢开始。处方：把任务砍到「五分钟能做完」，先让身体相信这件事不难。

第四条 · 珊迪式过度准备
方案改了七版，一个都没上线。诊断：完美主义是拖延最体面的借口。处方：定一个「够用就好」的及格线，先交出去。

第五条 · 痞老板式执念
盯着别人的秘方，忘了自己的店。诊断：比较带来的不是动力，是持续的低烧。处方：关掉那些让你心慌的列表，只留一个自己的进度条。

最后一句：
在海底待久了会发现，没有谁是真的躺平，大家只是用各自的方式在呼吸。
""",
    },
]


async def _launch_browser(playwright):
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"]
    try:
        return await playwright.chromium.launch(headless=True, args=args)
    except Exception as exc:
        print(f"[warn] 默认 Chromium 启动失败（{exc}），尝试本地 ms-playwright 缓存")
        cache = Path.home() / "AppData" / "Local" / "ms-playwright"
        for exe in sorted(cache.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True):
            return await playwright.chromium.launch(executable_path=str(exe), headless=True, args=args)
        raise


_UPLOAD_URL_RE = re.compile(
    r"/uploads/assets/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+\.(?:jpg|png|webp|gif)"
)


def _absolutize_uploads(html: str) -> str:
    """把卡片里的 /uploads 相对 URL 内联成 data URI。

    compose_deck 产出的是 `/uploads/assets/{user}/{id}.png`，这是给前端 HTTP
    服务用的。离线用 Playwright set_content 渲染时 base URL 是 about:blank：
    - 相对路径无法解析；
    - 换成 file:// 绝对路径也会被 Chromium 的跨协议策略拦掉。
    两者都表现为 <img> 静默加载失败 —— 整页只剩文字、图区全空。
    内联 base64 不依赖任何 base URL 与协议，是离线渲染唯一确定可行的做法。
    """
    from app.services.asset_library import url_to_base64

    def repl(m: re.Match) -> str:
        data = url_to_base64(m.group(0))
        return data if data else m.group(0)

    return _UPLOAD_URL_RE.sub(repl, html)


async def _render_html(page, html: str, out_path: Path, name: str) -> dict:
    await page.set_content(_absolutize_uploads(html), wait_until="load", timeout=20000)
    try:
        await page.evaluate("document.fonts.ready")
    except Exception:
        pass
    await page.wait_for_timeout(500)
    metrics = await page.evaluate(
        """() => {
            const card = document.querySelector('article') || document.body;
            const h1 = document.querySelector('h1');
            return {
                scrollH: document.body.scrollHeight,
                scrollW: document.body.scrollWidth,
                cardH: Math.round(card.getBoundingClientRect().height),
                cardW: Math.round(card.getBoundingClientRect().width),
                h1Font: h1 ? getComputedStyle(h1).fontFamily : '',
                h1Weight: h1 ? getComputedStyle(h1).fontWeight : '',
            };
        }"""
    )
    overflow = metrics["scrollH"] > 1442 or metrics["scrollW"] > 1082
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(out_path), type="png")
    print(f"  ✓ {name}  ({metrics['cardW']}x{metrics['cardH']})  h1={metrics['h1Font']} {metrics['h1Weight']}"
          + ("  ⚠ 溢出" if overflow else ""))
    return metrics


async def _ensure_tables() -> None:
    import app.db.models  # noqa: F401  注册所有 ORM 表
    from app.db.session import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _illustrate(prompt: str, slug: str) -> list[str]:
    """生一张插画并入库为素材，返回 asset_ids。"""
    import io

    from PIL import Image

    from app.services.asset_library import save_asset
    from capabilities.image_gen.adapters.image_gen import get_default_adapter

    adapter = get_default_adapter()
    if adapter is None:
        raise RuntimeError("未配置生图能力（DASHSCOPE_API_KEY 缺失）")
    blobs = await adapter.generate(prompt, size="1024x1024", count=1)
    if not blobs:
        raise RuntimeError("生图返回空结果")
    fmt = (Image.open(io.BytesIO(blobs[0])).format or "PNG").upper()
    ctype = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "image/png")
    asset = await save_asset(
        "local", f"{slug}.{fmt.lower()}", ctype, blobs[0], source="ai"
    )
    print(f"  插画已入库 asset_id={asset['asset_id']} url={asset['original_url']}")
    return [asset["asset_id"]]


async def _run_deck(spec: dict, index: int, llm, browser) -> list[Path]:
    from app.cards.composer import compose_deck

    topic = spec["topic"]
    print(f"\n[{index}] {spec['slug']} | {spec['chain']} | {topic}")
    kwargs = dict(topic=topic, content=spec["content"].strip(), user_id="local", llm=llm)
    if spec.get("image_prompt"):
        kwargs.update(asset_ids=await _illustrate(spec["image_prompt"], spec["slug"]))
        kwargs.update(theme_id=spec["theme_id"], recipe_id=spec["recipe_id"],
                      image_layout=spec.get("image_layout", "top"))
    elif spec["chain"] == "structured":
        kwargs.update(theme_id=spec["theme_id"], recipe_id=spec["recipe_id"])
    else:
        kwargs.update(style_hint=spec["style_hint"])

    draft = await compose_deck(**kwargs)
    pages = draft["pages"]
    print(f"  链路={draft.get('template')}  页数={len(pages)}")

    # 选页：封面 + 正文页，跳过 end_cta 收尾页（CTA 天然撑不满画幅，PPT 里不好看）
    # 结构化链路 role 是 cover/body/ending，自由风格链路 type 是 cover/content/ending
    keep = [i for i, p in enumerate(pages) if p.get("type") == "cover"][:1]
    keep += [i for i, p in enumerate(pages) if p.get("type") in ("body", "content")][:2]
    if len(keep) < 2:
        keep += [i for i, p in enumerate(pages) if i not in keep]

    context = await browser.new_context(
        viewport={"width": 1080, "height": 1440}, device_scale_factor=2
    )
    page = await context.new_page()
    saved: list[Path] = []
    for seq, page_index in enumerate(keep, 1):
        item = pages[page_index]
        html = item.get("htmlContent", "")
        if not html:
            continue
        name = f"{index:02d}_{spec['slug']}_{seq:02d}_{item.get('type', 'content')}"
        html_path = HTML_DIR / f"{name}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        out_path = OUT_DIR / f"{name}.png"
        await _render_html(page, html, out_path, name)
        density = item.get("density") or {}
        if density:
            print(f"    密度审计: fill={density.get('fill')} min_fill={density.get('min_fill')} "
                  f"problems={density.get('problems')}")
        saved.append(out_path)
    await context.close()
    return saved


async def _audit(paths: list[Path]) -> None:
    from card_audit import analyze

    print("\n===== 成图密度质检（card_audit）=====")
    failed = []
    for p in paths:
        r = analyze(str(p))
        tag = "PASS" if r["pass"] else "FAIL"
        print(f"{tag} {p.name}  跨度{r['span']*100:.0f}% 底部空{r['bottom_dead']*100:.0f}% "
              f"最大空白带{r['max_gap']*100:.0f}% 四带{r['bands']}")
        if not r["pass"]:
            for prob in r["problems"]:
                print(f"    !! {prob}")
            failed.append(p.name)
    if failed:
        print(f"\n需重做的卡片：{failed}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, default=None, help="只跑指定序号（1 起）")
    args = parser.parse_args()

    from app.config import get_settings
    settings = get_settings()
    if not settings.deepseek_api_key:
        print("ERROR: DEEPSEEK_API_KEY 未配置")
        return 1

    from app.adapters.deepseek import DeepSeekAdapter
    llm = DeepSeekAdapter(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model="deepseek-chat",
        temperature=0.75,
    )

    from playwright.async_api import async_playwright
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await _ensure_tables()

    specs = DECK_SPECS
    if args.only:
        specs = [DECK_SPECS[args.only - 1]]
    all_saved: list[Path] = []
    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright)
        try:
            for spec in specs:
                i = DECK_SPECS.index(spec) + 1
                try:
                    all_saved += await _run_deck(spec, i, llm, browser)
                except Exception as exc:
                    print(f"  [FAIL] 第 {i} 组失败：{type(exc).__name__}: {exc}")
        finally:
            await browser.close()

    print(f"\n共生成 {len(all_saved)} 张 → {OUT_DIR}")
    await _audit(all_saved)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
