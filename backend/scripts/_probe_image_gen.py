#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(r"D:\My_Project\多智能体小红书发布平台\ppt\_probe")


async def main():
    from capabilities.image_gen.adapters.image_gen import get_default_adapter

    adapter = get_default_adapter()
    print("adapter =", type(adapter).__name__ if adapter else None)
    if adapter is None:
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    prompt = (
        "卡通插画，明亮的海洋蓝与柠檬黄配色，粗黑描边，圆润夸张的角色造型，"
        "海底沙地背景，珊瑚与水草，高饱和扁平上色，儿童动画片质感，无文字"
    )
    try:
        blobs = await adapter.generate(prompt, size="1080x1440", count=1)
    except Exception as exc:
        print("generate failed:", type(exc).__name__, exc)
        return 1
    print("returned images:", len(blobs), [len(b) for b in blobs])
    for i, b in enumerate(blobs):
        p = OUT / f"probe_{i}.png"
        p.write_bytes(b)
        print("saved", p, p.stat().st_size)
    return 0


sys.exit(asyncio.run(main()))