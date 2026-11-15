"""端到端测试：澄清确认后 SSE 链路不断开（简化版）"""
import asyncio
import json
import time
import httpx

BASE = "http://localhost:8000"
TERMINAL_TYPES = frozenset({
    'workflow_completed', 'workflow_error', 'agent_loop_done',
    'workflow_terminated', 'workflow_failed', 'workflow_suspended',
    'workflow_cancelled', 'shortcut_completed',
})

async def main():
    timeout_cfg = httpx.Timeout(120.0, connect=10.0, read=30.0, write=10.0)
    async with httpx.AsyncClient(base_url=BASE, timeout=timeout_cfg) as client:
        # Step 0: SSE cookie
        print("Step 0: SSE session cookie")
        r = await client.post("/api/auth/sse-session")
        print(f"  sse-session: {r.status_code}")

        # Step 1
        print("Step 1: 创建会话")
        resp = await client.post("/api/chat/sessions", json={"title": "e2e-clarify"})
        if resp.status_code != 200:
            print(f"  FAIL: {resp.status_code} {resp.text[:300]}"); return
        rj = resp.json()
        data = rj.get("data", rj)
        session_id = data.get("id") or data.get("session_id")
        print(f"  OK: {session_id}")

        # Step 2: SSE
        print("\nStep 2: SSE + 发消息")
        all_events = []
        saw_clarification = False
        resume_time = 0

        async def read_sse():
            nonlocal saw_clarification
            et_buf = ""
            data_buf = ""
            async with client.stream("GET", f"/api/sse/chat/{session_id}",
                                     headers={"Accept": "text/event-stream"}, timeout=120.0) as r:
                print(f"  SSE connected: {r.status_code}")
                async for line in r.aiter_lines():
                    if line.startswith("event: "):
                        et_buf = line[7:].strip()
                    elif line.startswith("data: "):
                        data_buf = line[6:]
                    elif line.strip() == "" and data_buf:
                        try:
                            p = json.loads(data_buf)
                            et = p.get("type") or p.get("event_type") or et_buf
                            all_events.append({"type": et, "data": p, "ts": time.time()})
                            if et in ("agent_clarification_required", "clarification_required"):
                                saw_clarification = True
                        except Exception:
                            pass
                        data_buf = ""; et_buf = ""

        sse_task = asyncio.create_task(read_sse())
        await asyncio.sleep(1)

        # Step 3: 发消息
        print("  sending message...")
        resp = await client.post("/api/v1/chat/agent", json={
            "message": "帮我做一期小红书图文", "session_id": session_id,
        }, timeout=120.0)
        agent_data = resp.json()
        status = agent_data.get("status")
        print(f"  POST /agent -> {status}")

        # Step 4: 等澄清
        print("\nStep 4: 等澄清事件")
        for _ in range(30):
            if saw_clarification:
                break
            await asyncio.sleep(1)
        print(f"  {'OK' if saw_clarification else 'TIMEOUT'} clarification event")

        # Step 5: 检查暂停期间终态事件
        print("\nStep 5: 暂停期间终态事件检查")
        await asyncio.sleep(2)
        pause_terminal = []
        for e in all_events:
            if e["type"] in TERMINAL_TYPES:
                er = e["data"].get("ended_reason", "")
                if er not in ("clarification_paused", "guardian_paused", "storyboard_paused"):
                    pause_terminal.append(e)
        print(f"  {'OK: 0' if not pause_terminal else f'FAIL: {len(pause_terminal)}'} terminal during pause")
        for e in pause_terminal:
            print(f"    - {e['type']}")

        # Step 6: 回答澄清
        print("\nStep 6: 回答澄清")
        batches = agent_data.get("clarification_batches", [])
        answers = {}
        if batches:
            for q in batches[0].get("questions", []):
                fn = q.get("field_name", q.get("id", ""))
                opts = q.get("options", [])
                answers[fn] = opts[0].get("value", "") if opts else "default"

        clarify_resp = await client.post("/api/v1/chat/clarify", json={
            "session_id": session_id,
            "loop_state_path": agent_data.get("loop_state_path", ""),
            "answers": answers,
            "batch_id": batches[0].get("batch_id", "") if batches else "",
            "action": "answer",
            "turn_id": agent_data.get("turn_id", ""),
        }, timeout=30)
        clarify_data = clarify_resp.json()
        print(f"  POST /clarify -> {clarify_data.get('status')}")
        resume_time = time.time()

        # Step 7: 等续跑完成
        print("\nStep 7: 等续跑完成 (max 90s)")
        saw_resume_start = False
        saw_workflow_completed = False
        saw_agent_loop_done = False
        checked_ids = set()

        start = time.time()
        while time.time() - start < 90:
            await asyncio.sleep(3)
            for i, e in enumerate(all_events):
                if i in checked_ids:
                    continue
                checked_ids.add(i)
                et = e["type"]
                if e["ts"] < resume_time:
                    continue
                if et == "workflow_started" and not saw_resume_start:
                    saw_resume_start = True
                    print(f"  resume: workflow_started")
                if et == "workflow_completed":
                    er = e["data"].get("ended_reason", "")
                    if er not in ("clarification_paused",):
                        saw_workflow_completed = True
                        print(f"  OK: workflow_completed (reason={er})")
                if et == "agent_loop_done":
                    saw_agent_loop_done = True
                    print(f"  OK: agent_loop_done")

            if saw_workflow_completed:
                break
            elapsed = time.time() - start
            if int(elapsed) % 15 < 3:
                print(f"  ... {elapsed:.0f}s, events={len(all_events)}")

        # Step 8: 结果
        print("\n" + "=" * 60)
        print("RESULT:")
        print(f"  total events: {len(all_events)}")
        print(f"  terminal during pause: {len(pause_terminal)}")
        print(f"  resume started: {saw_resume_start}")
        print(f"  workflow_completed after resume: {saw_workflow_completed}")
        print(f"  agent_loop_done after resume: {saw_agent_loop_done}")

        types = {}
        for e in all_events:
            t = e["type"]
            types[t] = types.get(t, 0) + 1
        print("\n  event stats:")
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            m = " [TERMINAL]" if t in TERMINAL_TYPES else ""
            print(f"    {t}: {c}{m}")

        print("\n" + "=" * 60)
        if pause_terminal:
            print("FAIL: terminal events during pause")
        elif not saw_resume_start:
            print("INCOMPLETE: resume not started")
        elif saw_workflow_completed:
            print("PASS: no terminal during pause, resume OK, workflow_completed OK")
        else:
            print("INCOMPLETE: resume did not complete in 90s")

        sse_task.cancel()
        try:
            await sse_task
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())