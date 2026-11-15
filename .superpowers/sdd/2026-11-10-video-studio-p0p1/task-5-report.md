# Task 5 报告：场景本地预览静态服务（studio_preview）

- 分支：`codex/design-workflow-orchestration-cards`
- 提交：`efce25c20fcb4832ea6fdeeb80e6818647895762` `feat(studio): 场景本地预览静态服务`
- 文件：
  - 新建 `backend/capabilities/video/services/studio_preview.py`
  - 新建 `backend/tests/test_studio_preview.py`

## 做了什么（类接口）

`class StudioPreviewServer`，仅标准库（`http.server` / `functools` / `threading` / `pathlib`），无新依赖，无风格常量：

- `__init__(ws: dict, port: int = 0)`
  - 校验：`ws` 缺 `scene` 键 → `ValueError("workspace 缺少 scene 键，无法启动预览服务")`；目录不存在 → `ValueError(f"场景目录不存在：{scene}")`（均为中文错误信息）
  - `directory` 用 `str(scene.resolve())` 传 str 绝对路径（Windows 中文路径安全）
- `start() -> str`
  - `functools.partial(SimpleHTTPRequestHandler, directory=...)` + `ThreadingHTTPServer(("127.0.0.1", port), handler)`，`daemon_threads = True`
  - daemon 线程跑 `serve_forever`；`port=0` 时从 `server.server_address[1]` 读回实际端口
  - 返回 `http://127.0.0.1:<port>/`（带尾部斜杠）；只做静态托管，`/` 默认 index.html；播放循环（rAF 调 seekTo）属场景代码职责
- `stop() -> None`
  - `shutdown()` + `server_close()` + 线程 `join(timeout=5)`，随后置空状态；幂等：未 start 或重复 stop 时直接返回不炸
- `url_for(rel: str) -> str`
  - 返回 `http://127.0.0.1:<port>/<rel>`（rel 前导 `/` 已归一）

## 测试输出关键行原文（失败 → 通过）

Step 2 失败（实现前）：

```
backend\tests\test_studio_preview.py:2: in test_preview_server_serves_scene
    from capabilities.video.services.studio_preview import StudioPreviewServer
E   ModuleNotFoundError: No module named 'capabilities.video.services.studio_preview'
=========================== short test summary info ===========================
FAILED backend\tests\test_studio_preview.py::test_preview_server_serves_scene - ModuleNotFoundError: No module named 'capabilities.video.services.studio_preview'
============================== 1 failed in 0.70s ==============================
```

Step 4 通过（实现后）：

```
backend\tests\test_studio_preview.py .                                   [100%]

============================== 1 passed in 0.68s ==============================
```

命令均为任务书指定：`backend/.venv/Scripts/python -m pytest backend/tests/test_studio_preview.py -v`（Python 3.12.4）。

## 自审发现

1. 提交时 git 输出了一条预先存在的索引损坏噪音：`error: invalid object 100644 12bd4f2d... for '_tmp_shot/inspect_shell.py'`。因按约束用了 `git commit --only <pathspec>`（临时索引重建），提交本身成功且只含本任务两个文件（2 files changed, 70 insertions）；该报错与本任务改动无关，是仓库既有问题，未做修复（超出本任务范围）。
2. `ThreadingHTTPServer` 类自身已默认 `daemon_threads = True`，实现中仍显式赋值一行以满足硬约束的可验证性，属冗余但无害。
3. `stop()` 额外做了 `thread.join(timeout=5)`（任务书只要求 shutdown+server_close 且幂等）；用于确保 serve_forever 线程真正退出，一行成本，不改变幂等语义。
4. 参数校验放在 `__init__`（fail-fast）；任务书未指明放在 `__init__` 还是 `start()`，二者语义对 Task 6（先建 ws 再起服务）等价。
5. Windows PowerShell 5 不支持 `&&`，`git add` 与 `git commit` 分两条命令执行；提交消息经 UTF-8 临时文件 `git commit -F` 传入（用后已删除），全程未运行 git status / git add -A / 无 pathspec commit。

## 偏离任务书的点

- 无实质偏离。测试代码逐字采用任务书 Step 1 原文；提交消息逐字采用任务书 Step 5 的 `feat(studio): 场景本地预览静态服务`，仅按仓库硬约束把 `-m` 改为 `-F <UTF-8 消息文件> --only <文件...>` 传递。
