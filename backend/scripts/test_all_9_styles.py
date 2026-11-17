#!/usr/bin/env python3
"""全 9 风格批量验证。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_card_skill import run_single

STYLES = [
    (
        "瑞士极简",
        "Python设计模式5个最常用",
        "1.单例 全局唯一实例。2.工厂 用工厂方法替代直接new。3.观察者 一对多通知。4.策略 运行时切换算法。5.装饰器 不改原类动态加功能。",
        "swiss-v3",
    ),
    (
        "杂志编辑",
        "如何读懂一本书",
        "主动阅读三问：1.作者在答什么问题 2.用什么论据支撑 3.跟我已知有什么关联。",
        "editorial-v3",
    ),
    (
        "新中式墨韵",
        "二十四节气之立春",
        "立春三候：一候东风解冻，二候蛰虫始振，三候鱼陟负冰。春气始至，万物将苏。",
        "ink-v3",
    ),
    (
        "奶油温柔",
        "周末早午餐食谱",
        "1.班尼迪克蛋 水波蛋+荷兰酱+英式松饼。2.法式吐司 鸡蛋奶液浸泡煎至金黄。3.牛油果吐司 水波蛋+辣酱+柠檬汁。",
        "cream-v3",
    ),
    (
        "多巴胺Y2K",
        "Z世代职场生存5法则",
        "1.别等准备好再开始 2.用AI放大自己不是替代 3.跳槽不如内部转岗 4.副业是安全网不是退路 5.人脉大于技能大于学历",
        "dopamine-v3",
    ),
    (
        "高奢黑金",
        "顶级咖啡豆产地指南",
        "1.埃塞耶加雪菲 花果香、柑橘调。2.巴拿马翡翠庄园 茉莉、蜜糖。3.肯尼亚AA 黑醋栗、番茄。",
        "luxe-v3",
    ),
    (
        "手账贴纸",
        "每日习惯追踪法",
        "1.习惯堆叠 新习惯绑在已有习惯后。2.环境设计 把需要的放眼前、诱惑的藏起来。3.两分钟降门槛 新习惯先缩到2分钟能做完。",
        "journal-v4",
    ),
    (
        "极客终端",
        "Git工作流3种模型",
        "1.GitFlow 功能分支+release分支。2.GitHubFlow PR为主+main永远可部署。3.TrunkBased 所有人在main上开发+短命feature flag。",
        "terminal-v3",
    ),
    (
        "植物清新",
        "春季养生5件事",
        "1.早起不赖床 顺应春气。2.少酸多甘 省酸增甘以养脾气。3.踏青散步 舒肝解郁。4.喝花茶 玫瑰菊花疏肝。5.夜卧早起 11点前入睡。",
        "botanical-v3",
    ),
]


async def main():
    for style, topic, content, out in STYLES:
        print(f"\n>>> Testing: {style}")
        rc = await run_single(style, topic, content, out)
        if rc != 0:
            print(f"FAILED: {style}")
    print("\n=== ALL 9 STYLES DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
