"""Test talking_head_overlay skill with a real video."""

import asyncio
import os
import sys

# 仅补 backend/ 到 sys.path，保证 standalone 运行（python tests/test_overlay_live.py）
# 时 app / capabilities 可导入。
# 严禁在模块级 os.chdir：pytest 收集阶段会 import 本文件，CWD 一旦被改，
# 同一次会话里所有用相对路径（Path("app/...")）的契约测试都会 FileNotFoundError。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from app.services.asset_library import list_video_assets, url_to_local_path
    from capabilities.video.services.video_orchestrator import resolve_video_assets

    print("=" * 60)
    print("Step 1: Find a usable video asset")
    print("=" * 60)
    assets = await list_video_assets("dev_test_user", limit=10)
    if not assets:
        print("  No video assets found!")
        return

    for a in assets:
        local = url_to_local_path(a["original_url"]) or ""
        size_mb = a["file_size"] / 1024 / 1024
        print(
            f"  id={a['asset_id']}  file={a['filename']}  size={size_mb:.1f}MB  local={local[:80] if local else 'MISSING'}"
        )

    usable = [a for a in assets if url_to_local_path(a["original_url"])]
    if not usable:
        print("  No video assets with valid local path!")
        return

    test_asset = usable[0]
    test_id = test_asset["asset_id"]
    test_path = url_to_local_path(test_asset["original_url"])
    print(f"\n  Selected: id={test_id}")
    print(f"  Path: {test_path}")

    print()
    print("=" * 60)
    print("Step 2: Probe video with ffprobe")
    print("=" * 60)
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                test_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            import json

            info = json.loads(result.stdout)
            fmt = info.get("format", {})
            streams = info.get("streams", [])
            print(f"  Duration: {float(fmt.get('duration', 0)):.1f}s")
            print(f"  Format: {fmt.get('format_long_name', 'unknown')}")
            for s in streams:
                codec_type = s.get("codec_type")
                codec = s.get("codec_name")
                if codec_type == "video":
                    print(f"  Video: {codec} {s.get('width')}x{s.get('height')}")
                elif codec_type == "audio":
                    print(f"  Audio: {codec} {s.get('sample_rate')}Hz")
        else:
            print(f"  ffprobe error: {result.stderr[:200]}")
    except FileNotFoundError:
        print("  ffprobe not found!")
    except Exception as exc:
        print(f"  ffprobe failed: {exc}")

    print()
    print("=" * 60)
    print("Step 3: Test talking_head_overlay skill")
    print("=" * 60)
    try:
        from capabilities.video.skills.talking_head_overlay import (
            GateMode,
            OverlayInput,
            TalkingHeadOverlaySkill,
        )

        skill = TalkingHeadOverlaySkill()
        params = {
            "video_path": test_path,
            "gate_mode": GateMode.AUTO_CONFIRM.value,
            "auto_confirm_low_risk": True,
        }

        print(f"  Skill: {skill.display_name}")
        print(f"  Params: video_path={test_path}")
        print(
            f"  gate_mode={params['gate_mode']}, auto_confirm_low_risk={params['auto_confirm_low_risk']}"
        )
        print()
        print("  Executing skill...")

        result_parts = []
        async for event in skill.execute(params):
            result_parts.append(event)
            step = event.get("step", "")
            status = event.get("status", "")
            msg = event.get("message", "")
            if step or status:
                print(f"    [{step or status}] {msg}")
            elif event.get("success") is not None:
                if event["success"]:
                    print(f"    SUCCESS: {event.get('message', '')}")
                    if event.get("output_url"):
                        print(f"    Output: {event['output_url']}")
                    if event.get("output_path"):
                        print(f"    Local: {event['output_path']}")
                else:
                    print(f"    FAILED: {event.get('error', 'unknown')}")

        if not result_parts:
            print("  No events yielded from skill")

    except Exception as exc:
        import traceback

        print(f"  Error: {exc}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
