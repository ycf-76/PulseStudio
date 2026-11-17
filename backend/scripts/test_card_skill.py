#!/usr/bin/env python3
"""独立测试 card_xiaohongshu skill 的 LLM 自由生成路径。

用法：
    cd backend
    python scripts/test_card_skill.py
    python scripts/test_card_skill.py --style 瑞士极简 --topic "Python异步编程" --out swiss
    python scripts/test_card_skill.py --batch   # 跑4种风格

产出：
    outputs/test-card-skill/<out>/  目录下每张卡片的 HTML + 合并预览页
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT_BASE = ROOT.parent / "outputs" / "test-card-skill"

BATCH_CONFIGS = [
    {
        "style": "瑞士极简",
        "topic": "Python 异步编程 5 个核心概念",
        "content": """
asyncio 是 Python 异步编程的标准库，5 个核心概念搞懂就能写高并发程序。

1. 事件循环 Event Loop
一切异步代码的引擎。asyncio.run() 启动循环，循环调度所有协程。理解循环就理解了异步的底层。

2. 协程 Coroutine
async def 定义的函数就是协程。调用它不会立刻执行，返回一个 coroutine 对象，必须 await 才运行。

3. await 挂起
await 让出控制权给事件循环，等 IO 完成再恢复。await 后面必须是 awaitable（协程/Task/Future）。

4. Task 并发调度
asyncio.create_task() 把协程包装成 Task 立即调度，不等 await。多个 Task 并发执行，GIL 下仍能 IO 并行。

5. async with / async for
异步上下文管理器和异步迭代器。aiohttp 的 session、asyncpg 的连接池都用 async with。

避坑：
- 别在协程里用 time.sleep()，用 asyncio.sleep()
- 别忘记 await，否则协程不执行且无报错
- 别在 async 函数里调同步阻塞 IO（requests/psycopg2），用 aiohttp/asyncpg
- 别手动创建事件循环，用 asyncio.run()
""",
        "out": "swiss",
    },
    {
        "style": "新中式墨韵",
        "topic": "中国茶道入门：从选茶到品鉴",
        "content": """
茶道不只是喝茶，是从选茶、备器、冲泡到品鉴的一整套审美体系。入门 5 步。

第一步：识茶
六大茶类：绿茶（不发酵）、白茶（微发酵）、黄茶（轻发酵）、乌龙（半发酵）、红茶（全发酵）、黑茶（后发酵）。发酵度决定口感和泡法。

第二步：选器
绿茶→玻璃杯（观色），乌龙→紫砂壶（聚香），红茶→白瓷盖碗（显汤）。器不对，茶味出不来。

第三步：控温
绿茶 80°C，乌龙 95°C，红茶 90°C，黑茶 100°C。温度高了苦涩，低了香气出不来。别用刚烧开的水泡所有茶。

第四步：注水
高冲低斟。注水时壶提高让水柱击茶醒香，出汤时壶低贴杯口保温度。盖碗注水沿壁缓入，不直冲茶叶。

第五步：品鉴
三口品：一口润唇识温度，二口含香辨滋味，三口咽下感回甘。好茶三泡后仍有余韵。

茶桌礼仪：
- 斟茶七分满（留三分情）
- 先客后主，先长后幼
- 壶嘴不对人（对人不敬）
- 空杯轻叩桌面致谢（叩指礼）
""",
        "out": "ink",
    },
    {
        "style": "奶油温柔",
        "topic": "新手烘焙：4 个零失败配方",
        "content": """
烘焙不难，选对配方最重要。4 个真正零失败的入门配方，每一步都写清楚了。

配方一：巴斯克芝士蛋糕
材料：奶油奶酪 250g / 淡奶油 200ml / 鸡蛋 2 个 / 糖 60g / 低筋面粉 10g
步骤：1) 奶酪室温软化搅拌顺滑 2) 加糖+蛋液拌匀 3) 加淡奶油+面粉拌匀 4) 倒入模具 5) 220°C 烤 25 分钟
秘诀：表面焦黑是正常的！这就是巴斯克烧焦感的灵魂。

配方二：云朵蛋（Cloud Eggs）
材料：鸡蛋 2 个 / 糖 15g / 塔塔汁几滴
步骤：1) 蛋白蛋黄分离 2) 蛋白+糖+塔塔汁打发到硬性发泡 3) 烤盘铺油纸堆成云朵状 4) 中间放蛋黄 5) 150°C 烤 8 分钟
秘诀：蛋白盆必须无油无水，一滴蛋黄都不能混进去。

配方三：免揉面包
材料：高筋粉 300g / 水 230ml / 盐 6g / 酵母 3g
步骤：1) 全部混合成湿面团 2) 盖保鲜膜室温发酵 18 小时 3) 折叠排气 4) 二次发酵 2 小时 5) 铸铁锅 230°C 烤 30 分钟盖盖 + 15 分钟开盖
秘诀：面团很湿很黏是正常的！别加粉。长发酵产生风味。

配方四：巧克力熔岩蛋糕
材料：黑巧 100g / 黄油 50g / 鸡蛋 2 个 / 糖 30g / 低筋面粉 30g
步骤：1) 黑巧+黄油隔水融化 2) 加糖+蛋液拌匀 3) 筛入面粉拌匀 4) 倒入模具 5) 200°C 烤 8-10 分钟
秘诀：烤时间看中心——轻晃有流动感就出炉。多 1 分钟就是普通蛋糕不是熔岩了。

通用避坑：
- 烤箱必须预热 10 分钟以上
- 面粉要过筛
- 鸡蛋用常温的
- 烤盘放中层
""",
        "out": "cream",
    },
    {
        "style": "多巴胺Y2K",
        "topic": "2025 最潮 5 种穿搭公式",
        "content": """
穿搭不是跟风买爆款，是掌握公式后自由组合。5 个 2025 最潮穿搭公式，每个都能变出 10+ 套。

公式一：Oversize 西装 + 短 Top
大廓形西装外套 + 紧身短上衣 + 高腰阔腿裤。上宽下紧，腰线在短 Top 下缘。颜色：西装选亮色（电蓝/荧光绿），内搭纯白。

公式二：工装连体裤 + 运动鞋
一条工装连体裤解决整套搭配。解开上两颗扣子露出内搭，脚踩厚底运动鞋。加一顶棒球帽完成度拉满。

公式三：百褶裙 + 堆堆袜 + 乐福鞋
学院风回潮。百褶裙（格纹/纯色）+ 过膝堆堆袜 + 厚底乐福鞋。上身穿针织背心或短卫衣。袜子和裙子同色系。

公式四：透视内搭 + 外穿背心
透视/网纱上衣做内搭，外面套粗针织背心或工装背心。若隐若现比全露更酷。下装配皮裤或工装裤。

公式五：头重脚轻法
上半身叠穿（T恤+衬衫+外套三层），下半身极简（直筒裤+薄底鞋）。视觉重心在上，显高显瘦。

配饰加分项：
- 银色粗链项链（Y2K 灵魂）
- 袜套（套在鞋口外的短袜套）
- 迷你包（巴掌大挂腰间）
- 墨镜（不管阴天晴天）

避坑：
- 别全身超过 4 种颜色
- 别同时穿超过 2 种流行元素
- 别买快时尚爆款，选小众设计或古着
""",
        "out": "dopamine",
    },
]


async def run_single(style: str, topic: str, content: str, out_dir: str) -> int:
    from app.config import get_settings

    settings = get_settings()

    api_key = settings.deepseek_api_key
    base_url = (
        settings.deepseek_base_url
        if hasattr(settings, "deepseek_base_url")
        else "https://api.deepseek.com"
    )
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set")
        return 1

    from app.adapters.deepseek import DeepSeekAdapter

    llm = DeepSeekAdapter(
        api_key=api_key,
        base_url=base_url,
        model="deepseek-chat",
        temperature=0.7,
    )

    from app.core.prompt_skills import scan_and_register_prompt_skills

    scan_and_register_prompt_skills()

    from app.core.skill_registry import SkillRegistry

    registry = SkillRegistry.instance()

    skill_cls = registry.get("produce", "card_xiaohongshu")
    if not skill_cls:
        print("ERROR: card_xiaohongshu skill not found")
        return 1

    skill = skill_cls()

    print(f"\n{'='*60}")
    print(f"Style: {style} | Topic: {topic}")
    print(f"{'='*60}")

    result = await skill.execute(
        {
            "topic": topic,
            "content": content.strip(),
            "platform": "小红书",
            "style": style,
            "llm": llm,
        }
    )

    if "error" in result:
        print(f"ERROR: {result['error']}")
        return 1

    card_draft = result.get("card_draft")
    if not card_draft:
        print("WARNING: No card_draft in result.")
        print(result.get("report", "")[:2000])
        return 1

    pages = card_draft.get("pages", [])
    print(f"Generated {len(pages)} card pages")

    output_dir = OUTPUT_BASE / out_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(pages):
        html_content = page.get("htmlContent", "")
        page_type = page.get("type", "content")
        filename = f"card_{i+1:02d}_{page_type}.html"
        filepath = output_dir / filename
        filepath.write_text(html_content, encoding="utf-8")
        print(f"  Saved: {filename} ({len(html_content)} chars)")

    preview_html = _build_preview(pages, topic, style)
    preview_path = output_dir / "preview.html"
    preview_path.write_text(preview_html, encoding="utf-8")
    print(f"  Preview: {preview_path}")

    return 0


def _build_preview(pages: list[dict], title: str, style: str = "") -> str:
    import html as html_mod

    frames = ""
    for i, page in enumerate(pages):
        html = page.get("htmlContent", "")
        page_type = page.get("type", "content")
        frames += f"""
        <div class="card-frame">
            <div class="card-label">Card {i+1} ({page_type})</div>
            <iframe srcdoc="{html_mod.escape(html)}"
                    style="width:1080px;height:1440px;transform:scale(0.45);transform-origin:top left;border:0">
            </iframe>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{style} - {title}</title>
<style>
body {{ font-family: system-ui; background: #1a1a2e; color: #eee; padding: 20px; }}
h1 {{ font-size: 20px; }}
h2 {{ font-size: 14px; color: #888; }}
.container {{ display: flex; flex-wrap: wrap; gap: 20px; }}
.card-frame {{ background: #222; border-radius: 8px; padding: 10px; overflow: hidden; }}
.card-label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
</style></head><body>
<h1>{style} | {title}</h1>
<p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<div class="container">{frames}</div>
</body></html>"""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", default=None)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--content", default=None)
    parser.add_argument("--out", default="default")
    parser.add_argument("--batch", action="store_true", help="Run 4 styles in batch")
    args = parser.parse_args()

    if args.batch:
        for cfg in BATCH_CONFIGS:
            rc = await run_single(cfg["style"], cfg["topic"], cfg["content"], cfg["out"])
            if rc != 0:
                print(f"FAILED: {cfg['style']}")
        return 0

    if args.style and args.topic and args.content:
        return await run_single(args.style, args.topic, args.content, args.out)

    default = BATCH_CONFIGS[0]
    return await run_single(default["style"], default["topic"], default["content"], default["out"])


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
