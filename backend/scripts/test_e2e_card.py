#!/usr/bin/env python3
"""端到端测试：通过 ChatAgent 走完整 ReAct 循环生成卡片。

模拟前端发消息 → Reactor 解析意图 → skill_call card_xiaohongshu → 返回卡片。

用法：
    cd backend
    python scripts/test_e2e_card.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT.parent / "outputs" / "test-e2e-card"


async def main() -> int:
    from app.config import get_settings
    settings = get_settings()

    api_key = settings.deepseek_api_key
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set")
        return 1

    from app.core.prompt_skills import scan_and_register_prompt_skills
    scan_and_register_prompt_skills()

    from app.core.skill_registry import SkillRegistry
    registry = SkillRegistry.instance()
    produce_skills = list(registry._skills.get("produce", {}).keys())
    print(f"Available produce skills: {produce_skills}")

    if "card_xiaohongshu" not in produce_skills:
        print("ERROR: card_xiaohongshu not registered")
        return 1

    from app.agents.chat_agent import ChatAgent

    collected_events: list[dict] = []
    card_drafts: list[dict] = []

    async def emit_callback(node_id: str, event_type: str, payload: dict) -> None:
        collected_events.append({"type": event_type, "node": node_id, "payload_keys": list(payload.keys())})

        if event_type == "tool_result":
            result = payload.get("result", {})
            if isinstance(result, dict) and "card_draft" in result:
                card_drafts.append(result["card_draft"])
                pages = result["card_draft"].get("pages", [])
                print(f"  >> card_draft received: {len(pages)} pages")

        if event_type == "workflow_completed":
            print(f"  >> workflow completed")

        if event_type in ("agent_thinking", "tool_call", "tool_result"):
            short = str(payload)[:120]
            print(f"  [{event_type}] {short}...")

    agent = ChatAgent(emit_callback=emit_callback)

    user_message = (
        "帮我做一组小红书卡片，主题是 Codex CLI 保姆级教学，用极客终端风格，8张图。"
        "不需要确认偏好，直接用干货清单型骨架、顶图封面布局生成。"
    )

    print(f"=== 端到端卡片生成测试 ===")
    print(f"User message: {user_message}")
    print(f"Calling ChatAgent (ReAct loop)...\n")

    try:
        result = await agent.process(
            message=user_message,
            user_id="dev_test_user",
            session_id="test-e2e-card-session",
            creation_type="image_text",
            auto_approve=True,
        )
    except Exception as e:
        traceback.print_exc()
        print(f"\nERROR: ChatAgent.process failed: {e}")
        return 1

    print(f"\n=== ReAct loop finished ===")
    print(f"Total events: {len(collected_events)}")
    print(f"Card drafts found: {len(card_drafts)}")

    if not card_drafts:
        output = result.output if hasattr(result, "output") else str(result)
        print(f"\nNo card_draft in events. Agent output (first 2000 chars):")
        print(output[:2000] if isinstance(output, str) else str(output)[:2000])

        if "card_draft" in str(result):
            print("\n>>> card_draft IS in result but wasn't captured in events")
            print(">>> This means the skill ran but the result wasn't emitted as tool_result")

        return 1

    all_pages = []
    for draft in card_drafts:
        all_pages.extend(draft.get("pages", []))

    print(f"Total card pages: {len(all_pages)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(all_pages):
        html_content = page.get("htmlContent", "")
        page_type = page.get("type", "content")
        filename = f"card_{i+1:02d}_{page_type}.html"
        filepath = OUTPUT_DIR / filename
        filepath.write_text(html_content, encoding="utf-8")
        print(f"  Saved: {filename} ({len(html_content)} chars)")

    if all_pages:
        preview = _build_preview(all_pages, "E2E Card Test")
        (OUTPUT_DIR / "preview.html").write_text(preview, encoding="utf-8")
        print(f"\n  Preview: {OUTPUT_DIR / 'preview.html'}")

    return 0


def _build_preview(pages: list[dict], title: str) -> str:
    import html as html_mod
    frames = ""
    for i, page in enumerate(pages):
        html = page.get("htmlContent", "")
        page_type = page.get("type", "content")
        frames += f'''
        <div class="card-frame">
            <div class="card-label">Card {i+1} ({page_type})</div>
            <iframe srcdoc="{html_mod.escape(html)}"
                    style="width:1080px;height:1440px;transform:scale(0.45);transform-origin:top left;border:0">
            </iframe>
        </div>'''

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: system-ui; background: #1a1a2e; color: #eee; padding: 20px; }}
h1 {{ font-size: 20px; }}
.container {{ display: flex; flex-wrap: wrap; gap: 20px; }}
.card-frame {{ background: #222; border-radius: 8px; padding: 10px; overflow: hidden; }}
.card-label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
</style></head><body>
<h1>{title}</h1>
<p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<div class="container">{frames}</div>
</body></html>"""


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)