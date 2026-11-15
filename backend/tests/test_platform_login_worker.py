"""平台扫码登录子进程脚本定位回归测试。

背景：platform_login 迁入 capabilities 层后，脚本路径仍按「同目录」拼接，
指向了不存在的 capabilities/messaging/services/qr_worker.py，
导致 /api/accounts/bind 返回「子进程无输出」。
"""

from __future__ import annotations

import asyncio

from app.services import qr_worker
from capabilities.messaging.services import platform_login as login_service


def test_qr_worker_script_points_to_existing_file():
    """测试: 扫码登录子进程脚本定位
    场景: platform_login 迁入 capabilities 层后拼接 qr_worker 脚本路径
    预期: QR_WORKER_SCRIPT 指向真实存在的 app/services/qr_worker.py
    文档依据: docs/能力外置服务化执行计划.md#L26
    """
    script = login_service.QR_WORKER_SCRIPT
    assert script.name == "qr_worker.py"
    assert script.exists(), f"扫码脚本不存在: {script}"


def test_qr_worker_storage_dir_matches_login_service():
    """测试: 登录态落盘目录一致性
    场景: 子进程写 storageState，父进程校验同一目录
    预期: 两者解析到同一个 storage_states 目录，登录态不会写丢
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md
    """
    assert qr_worker.STORAGE_DIR == login_service.STORAGE_DIR


def test_start_qr_login_reports_missing_worker(monkeypatch, tmp_path):
    """测试: 脚本缺失时的失败反馈
    场景: QR_WORKER_SCRIPT 指向不存在的文件
    预期: 直接返回「扫码脚本缺失」错误，任务标记 failed，而不是静默输出「子进程无输出」
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md
    """
    missing = tmp_path / "qr_worker.py"
    monkeypatch.setattr(login_service, "QR_WORKER_SCRIPT", missing)

    task_id, qr_data = asyncio.run(login_service.start_qr_login("xiaohongshu", "test_user"))

    assert qr_data["error"].startswith("扫码脚本缺失")
    assert login_service.get_bind_task_status(task_id)["status"] == "failed"
