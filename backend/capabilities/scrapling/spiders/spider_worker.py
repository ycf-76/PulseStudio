"""Spider 运行子进程脚本。

由 runner.py 通过 subprocess.Popen 启动，
通过 stdout 输出 JSON 行与父进程通信：
  - 唯一行：{"ok": true, "items": [...]} 或 {"error": "..."}
"""
from __future__ import annotations

import json
import sys
import traceback as _tb
from pathlib import Path

STORAGE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "app" / "storage_states"

SPIDER_MAP = {
    "xiaohongshu": "capabilities.scrapling.spiders.xhs_spider.XhsSpider",
    "xiaohongshu_search": "capabilities.scrapling.spiders.xhs_search_spider.XhsSearchSpider",
    "douyin": "capabilities.scrapling.spiders.douyin_spider.DouyinSpider",
    "bilibili": "capabilities.scrapling.spiders.bilibili_spider.BilibiliSpider",
}


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _emit(data: dict) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _get_spider_class(platform: str):
    class_path = SPIDER_MAP.get(platform)
    if not class_path:
        return None

    module_path, class_name = class_path.rsplit(".", 1)
    try:
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except Exception as e:
        raise ImportError(f"Failed to load spider class {class_path}: {e}") from e


def main() -> None:
    if len(sys.argv) < 4:
        _emit({"error": "用法: spider_worker.py <platform> <platform_uid> <cookies_json> [user_id] [force] [detail_budget]"})
        return

    platform = sys.argv[1]
    platform_uid = sys.argv[2]
    cookies_json = sys.argv[3]
    user_id = sys.argv[4] if len(sys.argv) > 4 else ""
    force = (sys.argv[5] if len(sys.argv) > 5 else "0").strip().lower() in ("1", "true", "yes")
    budget_arg = sys.argv[6] if len(sys.argv) > 6 else ""

    try:
        cookies = json.loads(cookies_json) if cookies_json else {}
    except Exception:
        cookies = {}

    try:
        spider_cls = _get_spider_class(platform)
    except ImportError as e:
        _emit({"error": f"Spider类加载失败({platform}): {e}\n{_tb.format_exc()[-800:]}"})
        return
    if not spider_cls:
        _emit({"error": f"不支持的平台：{platform}"})
        return

    try:
        spider = spider_cls(
            user_id=user_id,
            platform_uid=platform_uid,
            cookies=cookies,
        )
        # 详情页刷新策略由父进程注入：force=全量刷新（忽略冷却/重试上限），
        # detail_budget=单轮抓取上限（不设上限会跑满超时被强杀）
        if hasattr(spider, "force"):
            spider.force = force
        if budget_arg and hasattr(spider, "detail_budget"):
            try:
                spider.detail_budget = int(budget_arg)
            except ValueError:
                _emit({"error": f"detail_budget 参数非法：{budget_arg}"})
                return
        items = spider.start()

        # 登录态失效时 Spider 会跳过全部解析并返回空列表，
        # 若仍按 ok=True 上报，前端会误报"同步完成"，实际一条都没写。
        if getattr(spider, "login_failed", False):
            _emit({"error": "登录态已失效（被重定向到登录页），请到「平台账号」重新扫码登录后再同步"})
            return

        serializable = []
        for item in items:
            d = {}
            for k, v in item.items():
                try:
                    json.dumps(v)
                    d[k] = v
                except Exception:
                    d[k] = str(v)
            serializable.append(d)

        _emit({
            "ok": True,
            "items": serializable,
            "saved": int(getattr(spider, "saved_count", 0)),
            "deferred": int(getattr(spider, "detail_deferred", 0)),
        })
    except Exception as e:
        _emit({"error": str(e)[:500]})


if __name__ == "__main__":
    main()
