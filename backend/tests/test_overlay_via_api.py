"""Test talking_head_overlay via HTTP API with a real video asset."""

import json
import os

import httpx
import pytest

BASE = "http://localhost:8000"


def find_video_asset():
    client = httpx.Client(timeout=10)
    r = client.get(f"{BASE}/api/video-assets")
    assert r.status_code == 200
    assets = r.json().get("data", [])
    print(f"Found {len(assets)} video assets")
    if not assets:
        return None
    a = assets[0]
    print(
        f"  Using: id={a['asset_id']}  file={a['filename']}  size={a['file_size']/1024/1024:.1f}MB"
    )
    return a


@pytest.fixture(scope="module")
def asset():
    """真实视频素材 + 本地服务端到端依赖，默认跳过。

    该测试会打真实 /api/v1/chat/agent（可能跑 30-120s）并依赖本机已启动的
    8000 服务与已上传素材，不适合进默认单测批次。设 RUN_E2E=1 才启用。
    """
    if os.getenv("RUN_E2E") != "1":
        pytest.skip("端到端测试默认跳过（设 RUN_E2E=1 且本地服务已启动时启用）")
    try:
        found = find_video_asset()
    except httpx.HTTPError as exc:
        pytest.skip(f"本地服务不可用：{exc}")
    if not found:
        pytest.skip("无可用视频素材")
    return found


def test_chat_agent_with_video(asset):
    asset_id = asset["asset_id"]
    client = httpx.Client(timeout=300)

    print()
    print("=" * 60)
    print("POST /api/v1/chat/agent with video_asset_ids")
    print("=" * 60)

    payload = {
        "message": "请用这个视频做一个口播演示，把人物放在右下角画中画，主画面留给演示内容",
        "mode": "auto",
        "video_asset_ids": [asset_id],
        "auto_approve": True,
    }

    print(f"  video_asset_ids: {payload['video_asset_ids']}")
    print("  auto_approve: True")
    print("  Waiting for agent response (may take 30-120s)...")
    print()

    try:
        r = client.post(f"{BASE}/api/v1/chat/agent", json=payload, timeout=300)
        print(f"  Status: {r.status_code}")

        if r.status_code != 200:
            print(f"  Error: {r.text[:500]}")
            return

        data = r.json()
        print()
        print("=" * 60)
        print("RESPONSE")
        print("=" * 60)

        session_id = data.get("session_id", "")
        status = data.get("status", "")
        message = data.get("message", "")
        output = data.get("output", {})
        thinking = data.get("thinking", "")
        turn_id = data.get("turn_id", "")

        print(f"  session_id: {session_id}")
        print(f"  turn_id: {turn_id}")
        print(f"  status: {status}")
        print()

        if thinking:
            print(f"  Thinking ({len(thinking)} chars):")
            print(f"    {thinking[:500]}")
            print()

        if message:
            print(f"  Message ({len(message)} chars):")
            print(f"    {message[:500]}")
            print()

        if output:
            output_str = json.dumps(output, ensure_ascii=False, indent=2)
            print(f"  Output ({len(output_str)} chars):")
            if len(output_str) > 2000:
                print(f"    {output_str[:2000]}...")
            else:
                print(f"    {output_str}")

        # Check for video-related results
        if output and isinstance(output, dict):
            video_url = output.get("video_url") or output.get("output_url")
            video_path = output.get("video_path") or output.get("output_path")
            if video_url:
                print(f"\n  VIDEO URL: {video_url}")
            if video_path:
                print(f"  VIDEO PATH: {video_path}")

    except httpx.ReadTimeout:
        print("  TIMEOUT: Agent took too long (>300s)")
    except Exception as exc:
        print(f"  Request failed: {exc}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asset = find_video_asset()
    if asset:
        test_chat_agent_with_video(asset)
    else:
        print("No video assets available for testing")
