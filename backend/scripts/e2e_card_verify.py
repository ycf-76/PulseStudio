#!/usr/bin/env python3
"""端到端卡片验证：生成 HTML → playwright 渲染 PNG → card_audit 像素级质检。

用法：
    python scripts/e2e_card_verify.py --style 瑞士极简 --topic "Python异步" --out swiss-e2e
    python scripts/e2e_card_verify.py --all   # 跑全部 9 种风格
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT_BASE = ROOT.parent / "outputs" / "e2e-card-verify"

STYLES = [
    (
        "瑞士极简",
        "Python设计模式5个最常用",
        "1.单例 全局唯一实例。2.工厂 用工厂方法替代直接new。3.观察者 一对多通知。4.策略 运行时切换算法。5.装饰器 不改原类动态加功能。",
    ),
    (
        "杂志编辑",
        "如何读懂一本书",
        "主动阅读三问：1.作者在答什么问题 2.用什么论据支撑 3.跟我已知有什么关联。",
    ),
    (
        "新中式墨韵",
        "二十四节气之立春",
        "立春三候：一候东风解冻，二候蛰虫始振，三候鱼陟负冰。春气始至，万物将苏。",
    ),
    (
        "奶油温柔",
        "周末早午餐食谱",
        "1.班尼迪克蛋 水波蛋+荷兰酱+英式松饼。2.法式吐司 鸡蛋奶液浸泡煎至金黄。3.牛油果吐司 水波蛋+辣酱+柠檬汁。",
    ),
    (
        "多巴胺Y2K",
        "Z世代职场生存5法则",
        "1.别等准备好再开始 2.用AI放大自己不是替代 3.跳槽不如内部转岗 4.副业是安全网不是退路 5.人脉大于技能大于学历",
    ),
    (
        "高奢黑金",
        "顶级咖啡豆产地指南",
        "1.埃塞耶加雪菲 花果香、柑橘调。2.巴拿马翡翠庄园 茉莉、蜜糖。3.肯尼亚AA 黑醋栗、番茄。",
    ),
    (
        "手账贴纸",
        "每日习惯追踪法",
        "1.习惯堆叠 新习惯绑在已有习惯后。2.环境设计 把需要的放眼前、诱惑的藏起来。3.两分钟降门槛 新习惯先缩到2分钟能做完。",
    ),
    (
        "极客终端",
        "Git工作流3种模型",
        "1.GitFlow 功能分支+release分支。2.GitHubFlow PR为主+main永远可部署。3.TrunkBased 所有人在main上开发+短命feature flag。",
    ),
    (
        "植物清新",
        "春季养生5件事",
        "1.早起不赖床 顺应春气。2.少酸多甘 省酸增甘以养脾气。3.踏青散步 舒肝解郁。4.喝花茶 玫瑰菊花疏肝。5.夜卧早起 11点前入睡。",
    ),
]


async def generate_cards(style: str, topic: str, content: str, out_dir: str) -> list[Path]:
    from scripts.test_card_skill import run_single

    rc = await run_single(style, topic, content, out_dir)
    if rc != 0:
        print(f"FAILED: card generation for {style}")
        return []
    html_dir = ROOT.parent / "outputs" / "test-card-skill" / out_dir
    return sorted(html_dir.glob("card_*.html"))


def render_to_png(html_files: list[Path], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    png_files: list[Path] = []
    for html_path in html_files:
        png_name = html_path.stem + ".png"
        png_path = out_dir / png_name
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "render_card.py"),
            "--html",
            str(html_path),
            "--out",
            str(png_path),
            "--selector",
            "body",
            "--width",
            "1080",
            "--height",
            "1440",
            "--scale",
            "2",
            "--wait",
            "1500",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and png_path.exists():
            png_files.append(png_path)
        else:
            print(f"  WARN: render failed for {html_path.name}: {result.stderr.strip()[:200]}")
    return png_files


def audit_pngs(png_files: list[Path]) -> dict:
    results = {"pass": 0, "fail": 0, "details": []}
    for png_path in png_files:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "card_audit.py"),
            "audit",
            "-f",
            str(png_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        passed = result.returncode == 0
        if passed:
            results["pass"] += 1
        else:
            results["fail"] += 1
        results["details"].append(
            {
                "file": png_path.name,
                "pass": passed,
                "output": result.stdout.strip(),
            }
        )
    return results


async def run_e2e(style: str, topic: str, content: str, out_name: str) -> None:
    print(f"\n{'='*60}")
    print(f"风格: {style} | 选题: {topic}")
    print(f"{'='*60}")

    print("\n[1/3] 生成 HTML 卡片...")
    html_files = await generate_cards(style, topic, content, out_name)
    if not html_files:
        print("FAILED: no HTML files generated")
        return
    print(f"  生成 {len(html_files)} 张 HTML 卡片")

    png_dir = OUTPUT_BASE / out_name
    print(f"\n[2/3] Playwright 渲染 PNG → {png_dir}...")
    png_files = render_to_png(html_files, png_dir)
    if not png_files:
        print("FAILED: no PNG files rendered")
        return
    print(f"  渲染 {len(png_files)} 张 PNG")

    print(f"\n[3/3] card_audit 像素级质检...")
    audit_results = audit_pngs(png_files)
    total = audit_results["pass"] + audit_results["fail"]
    print(f"  结果: {audit_results['pass']}/{total} PASS")

    for d in audit_results["details"]:
        status = "✅" if d["pass"] else "❌"
        print(f"  {status} {d['file']}")
        if not d["pass"]:
            for line in d["output"].split("\n"):
                if line.strip().startswith("!!"):
                    print(f"     {line.strip()}")

    if audit_results["fail"] == 0:
        print(f"\n🎉 {style} 全部通过像素级质检！")
    else:
        print(f"\n⚠️ {style} 有 {audit_results['fail']} 张未通过质检")


async def main() -> int:
    parser = argparse.ArgumentParser(description="端到端卡片验证")
    parser.add_argument("--style", default=None)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--content", default=None)
    parser.add_argument("--out", default="default")
    parser.add_argument("--all", action="store_true", help="跑全部 9 种风格")
    args = parser.parse_args()

    if args.all:
        for style, topic, content in STYLES:
            out_name = style.replace(" ", "") + "-e2e"
            await run_e2e(style, topic, content, out_name)
        print("\n" + "=" * 60)
        print("全部 9 种风格端到端验证完成")
        print("=" * 60)
        return 0

    if args.style and args.topic and args.content:
        await run_e2e(args.style, args.topic, args.content, args.out)
        return 0

    s, t, c = STYLES[0]
    await run_e2e(s, t, c, s.replace(" ", "") + "-e2e")
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
