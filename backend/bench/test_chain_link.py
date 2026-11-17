"""Quick chain-link test: call LLM through the full ReAct loop for 3 scenarios."""

import asyncio
import json
import sys

import httpx


async def test():
    base = "http://localhost:8000/api/v1/chat/agent"
    tests = [
        ("pure_qa", {"message": "你好，自我介绍一下自己", "mode": "auto"}),
        ("operation", {"message": "发到我邮箱可以吗", "mode": "auto"}),
        ("creation", {"message": "帮我做一张卡片", "mode": "auto"}),
    ]
    async with httpx.AsyncClient(timeout=90) as c:
        for label, payload in tests:
            print(f"\n{'='*60}")
            print(f"TEST [{label}]: {payload['message']}")
            print(f"{'='*60}")
            try:
                r = await c.post(base, json=payload)
                print(f"HTTP status: {r.status_code}")
                if r.status_code == 200:
                    data = r.json()
                    print(f"response keys: {list(data.keys())}")
                    msg = data.get("message", data.get("summary", ""))
                    if msg:
                        print(f"message (first 500): {str(msg)[:500]}")
                    intent = data.get("intent")
                    if intent:
                        print(f"intent: {json.dumps(intent, ensure_ascii=False)[:300]}")
                    wf_status = data.get("status", data.get("workflow_status", ""))
                    print(f"workflow_status: {wf_status}")
                    session_id = data.get("session_id", "")
                    print(f"session_id: {session_id}")
                else:
                    print(f"error body: {r.text[:800]}")
            except Exception as e:
                print(f"exception: {type(e).__name__}: {e}")

    print("\n\nDONE. All 3 scenarios tested.")


if __name__ == "__main__":
    asyncio.run(test())
