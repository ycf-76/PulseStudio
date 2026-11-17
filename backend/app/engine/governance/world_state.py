"""WorldState: 结构化世界状态，独立于对话历史。

对标 Codex WorldState (codex-rs/core/src/context/world_state/mod.rs):
  - 分 Section 管理，每个 Section 独立 snapshot/diff
  - update_world_state() -> (fragments, rollout_item)
  - render_history_diff(previous) -> 只注入变化部分
  - 即使对话历史全清，WorldState 仍准确
  - 技能产出自动写入对应 Section，不靠对话历史传递

Codex 核心方法 (ref: context_manager/history.rs::update_world_state):
  let snapshot = world_state.snapshot();
  let fragments = world_state.render_history_diff(
      self.world_state_baseline.as_ref(), self.raw_items()
  );
  let rollout_item = self.world_state_baseline.as_ref().map_or_else(
      || Some(WorldStateItem::full(snapshot.clone().into_object())),
      |previous| snapshot.merge_patch_from(previous).map(WorldStateItem::patch),
  );
  self.world_state_baseline = Some(snapshot);
  (fragments, rollout_item)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class WorldStateSection(ABC):
    """对标 Codex WorldStateSection trait。

    每个 Section 必须实现:
      - ID: 唯一标识（持久化用，必须稳定）
      - snapshot(): 返回当前状态的序列化快照
      - render_diff(previous): 相比上一轮，LLM 需要知道什么新信息
        - 无变化返回 None（零 token 开销）
        - 有变化返回结构化文本，注入对话历史
    """

    @property
    @abstractmethod
    def ID(self) -> str: ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]: ...

    @abstractmethod
    def render_diff(self, previous: dict[str, Any] | None) -> str | None: ...


class CopywriteSection(WorldStateSection):
    """文案产出状态。"""

    def __init__(self) -> None:
        self._title: str = ""
        self._content: str = ""
        self._tags: list[str] = []
        self._style: str = ""
        self._platform: str = ""

    @property
    def ID(self) -> str:
        return "copywrite"

    def update(self, result: dict[str, Any]) -> None:
        self._title = result.get("title", self._title)
        self._content = result.get("content", self._content)
        self._tags = result.get("tags", self._tags) or self._tags
        self._style = result.get("style", self._style)
        self._platform = result.get("platform", self._platform)

    def snapshot(self) -> dict[str, Any]:
        return {
            "title": self._title,
            "content": self._content[:500],
            "tags": self._tags,
            "style": self._style,
            "platform": self._platform,
        }

    def render_diff(self, previous: dict[str, Any] | None) -> str | None:
        current = self.snapshot()
        if not current["title"] and not current["content"]:
            return None
        if previous == current:
            return None
        parts = ["[文案产出状态]"]
        if not previous or not previous.get("title"):
            parts.append(f"标题: {current['title']}")
            parts.append(f"正文: {current['content']}")
            parts.append(f"标签: {', '.join(current['tags'])}")
            if current["style"]:
                parts.append(f"风格: {current['style']}")
            if current["platform"]:
                parts.append(f"平台: {current['platform']}")
        else:
            if current["title"] != previous.get("title"):
                parts.append(f"标题已更新: {current['title']}")
            if current["content"] != previous.get("content"):
                parts.append(f"正文已更新: {current['content'][:200]}")
            if current["tags"] != previous.get("tags"):
                parts.append(f"标签已更新: {', '.join(current['tags'])}")
        return "\n".join(parts) if len(parts) > 1 else None


class CardDraftSection(WorldStateSection):
    """卡片草稿状态。"""

    def __init__(self) -> None:
        self._pages: list[dict[str, Any]] = []
        self._recipe: str = ""
        self._theme: str = ""
        self._file_path: str = ""
        self._asset_ids: list[str] = []

    @property
    def ID(self) -> str:
        return "card_draft"

    def update(self, draft: dict[str, Any]) -> None:
        self._pages = draft.get("pages", self._pages) or self._pages
        self._recipe = draft.get("recipe", self._recipe)
        self._theme = draft.get("theme", self._theme)
        self._file_path = draft.get("file_path", draft.get("html_path", self._file_path))
        self._asset_ids = draft.get("asset_ids", self._asset_ids) or self._asset_ids
        if not self._pages and draft.get("page_count", 0) > 0:
            self._pages = [{"title": t} for t in draft.get("page_titles", [])]

    def snapshot(self) -> dict[str, Any]:
        return {
            "page_count": len(self._pages),
            "recipe": self._recipe,
            "theme": self._theme,
            "file_path": self._file_path,
            "asset_ids": self._asset_ids,
            "page_titles": [p.get("title", "") for p in self._pages[:5]],
        }

    def render_diff(self, previous: dict[str, Any] | None) -> str | None:
        current = self.snapshot()
        if current["page_count"] == 0:
            return None
        if previous == current:
            return None
        parts = ["[卡片草稿状态]"]
        if not previous or previous.get("page_count", 0) == 0:
            parts.append(f"已生成 {current['page_count']} 页图文卡片")
            parts.append(f"配方: {current['recipe']}")
            parts.append(f"主题: {current['theme']}")
            if current["file_path"]:
                parts.append(f"文件路径: {current['file_path']}")
            if current["asset_ids"]:
                parts.append(f"资源ID: {', '.join(current['asset_ids'])}")
            if current["page_titles"]:
                parts.append(f"页面标题: {', '.join(current['page_titles'])}")
        else:
            if current["page_count"] != previous.get("page_count"):
                parts.append(f"页数变更: {previous.get('page_count', 0)} → {current['page_count']}")
            if current["file_path"] != previous.get("file_path"):
                parts.append(f"文件路径: {current['file_path']}")
        return "\n".join(parts) if len(parts) > 1 else None


class AssetSection(WorldStateSection):
    """已产出资源注册表。"""

    def __init__(self) -> None:
        self._assets: list[dict[str, Any]] = []

    @property
    def ID(self) -> str:
        return "assets"

    def add(self, asset: dict[str, Any]) -> None:
        self._assets.append(asset)

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self._assets),
            "items": [
                {"id": a.get("id", ""), "type": a.get("type", ""), "path": a.get("path", "")}
                for a in self._assets[-10:]
            ],
        }

    def render_diff(self, previous: dict[str, Any] | None) -> str | None:
        current = self.snapshot()
        if current["count"] == 0:
            return None
        if previous == current:
            return None
        parts = [f"[已产出资源] 共 {current['count']} 个"]
        for item in current["items"]:
            parts.append(f"  - {item['type']}: {item['id']} ({item['path']})")
        return "\n".join(parts)


class TaskProgressSection(WorldStateSection):
    """任务进度追踪。"""

    def __init__(self) -> None:
        self._phase: str = "idle"
        self._completed_steps: list[str] = []
        self._pending_steps: list[str] = []

    @property
    def ID(self) -> str:
        return "task_progress"

    def update(
        self, phase: str, completed: list[str] | None = None, pending: list[str] | None = None
    ) -> None:
        self._phase = phase
        if completed is not None:
            self._completed_steps = completed
        if pending is not None:
            self._pending_steps = pending

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": self._phase,
            "completed": self._completed_steps,
            "pending": self._pending_steps,
        }

    def render_diff(self, previous: dict[str, Any] | None) -> str | None:
        current = self.snapshot()
        if current["phase"] == "idle" and not current["completed"]:
            return None
        if previous == current:
            return None
        parts = [f"[任务进度] 阶段: {current['phase']}"]
        if current["completed"]:
            parts.append(f"已完成: {', '.join(current['completed'])}")
        if current["pending"]:
            parts.append(f"待完成: {', '.join(current['pending'])}")
        return "\n".join(parts)


class EnvironmentSection(WorldStateSection):
    """环境信息（平台、模型等）。"""

    def __init__(self) -> None:
        self._platform: str = ""
        self._model: str = ""
        self._workspace: str = ""

    @property
    def ID(self) -> str:
        return "environment"

    def update(self, platform: str = "", model: str = "", workspace: str = "") -> None:
        if platform:
            self._platform = platform
        if model:
            self._model = model
        if workspace:
            self._workspace = workspace

    def snapshot(self) -> dict[str, Any]:
        return {"platform": self._platform, "model": self._model, "workspace": self._workspace}

    def render_diff(self, previous: dict[str, Any] | None) -> str | None:
        current = self.snapshot()
        if not current["platform"] and not current["model"]:
            return None
        if previous == current:
            return None
        parts = ["[环境信息]"]
        if current["platform"]:
            parts.append(f"平台: {current['platform']}")
        if current["model"]:
            parts.append(f"模型: {current['model']}")
        if current["workspace"]:
            parts.append(f"工作区: {current['workspace']}")
        return "\n".join(parts) if len(parts) > 1 else None


class UserProfileSection(WorldStateSection):
    """创作者画像摘要。"""

    def __init__(self) -> None:
        self._domain: str = ""
        self._style: str = ""
        self._tone: str = ""

    @property
    def ID(self) -> str:
        return "user_profile"

    def update(self, domain: str = "", style: str = "", tone: str = "") -> None:
        if domain:
            self._domain = domain
        if style:
            self._style = style
        if tone:
            self._tone = tone

    def snapshot(self) -> dict[str, Any]:
        return {"domain": self._domain, "style": self._style, "tone": self._tone}

    def render_diff(self, previous: dict[str, Any] | None) -> str | None:
        current = self.snapshot()
        if not current["domain"]:
            return None
        if previous == current:
            return None
        parts = ["[创作者画像]"]
        parts.append(f"领域: {current['domain']}")
        if current["style"]:
            parts.append(f"风格: {current['style']}")
        if current["tone"]:
            parts.append(f"调性: {current['tone']}")
        return "\n".join(parts) if len(parts) > 1 else None


class WorldState:
    # 二期纯净化：技能名 → 状态区语义 的映射由能力侧注册（register_skill_sections），
    # 内核默认空映射——world_state 只认识自己的 section（copywrite/card），
    # 不认识任何业务技能名。未注册技能走通用 assets 提取。
    _skill_section_map: dict[str, str] = {}
    _section_map_loaded: bool = False

    @classmethod
    def register_skill_sections(cls, mapping: dict[str, str]) -> None:
        """能力侧注册：技能名 → world_state section（"copywrite" | "card"）。"""
        cls._skill_section_map.update(mapping)

    @classmethod
    def _ensure_section_map(cls) -> None:
        """懒加载能力侧映射（延迟 import：agents 包顶层反向依赖 engine，与
        build_reactor_session 的延迟装配同一手法；只尝试一次防重复开销）。"""
        if cls._section_map_loaded:
            return
        cls._section_map_loaded = True
        try:
            from app.agents.state_sections import SKILL_SECTIONS

            cls.register_skill_sections(SKILL_SECTIONS)
        except Exception:
            pass  # 能力侧不可用时 world_state 仍可工作（只丢 section 增强）

    """对标 Codex WorldState: 分 Section 的结构化状态对象。

    核心方法:
      - update_world_state() -> (fragments, rollout_item)
        对标 Codex ContextManager::update_world_state()
      - take_snapshot() -> 所有 Section 的快照
      - ingest_tool_output() -> 从工具返回值自动更新对应 Section
      - from_snapshot() -> 从持久化的快照恢复
    """

    def __init__(self) -> None:
        self._sections: dict[str, WorldStateSection] = {}
        self._register(CopywriteSection())
        self._register(CardDraftSection())
        self._register(AssetSection())
        self._register(TaskProgressSection())
        self._register(EnvironmentSection())
        self._register(UserProfileSection())

    def _register(self, section: WorldStateSection) -> None:
        self._sections[section.ID] = section

    def section(self, section_id: str) -> WorldStateSection | None:
        return self._sections.get(section_id)

    @property
    def copywrite(self) -> CopywriteSection:
        return self._sections["copywrite"]  # type: ignore

    @property
    def card_draft(self) -> CardDraftSection:
        return self._sections["card_draft"]  # type: ignore

    @property
    def assets(self) -> AssetSection:
        return self._sections["assets"]  # type: ignore

    @property
    def task_progress(self) -> TaskProgressSection:
        return self._sections["task_progress"]  # type: ignore

    @property
    def environment(self) -> EnvironmentSection:
        return self._sections["environment"]  # type: ignore

    @property
    def user_profile(self) -> UserProfileSection:
        return self._sections["user_profile"]  # type: ignore

    def take_snapshot(self) -> dict[str, Any]:
        """所有 Section 的快照，用于持久化和下一轮 diff 比较。

        对标 Codex WorldState::snapshot()。
        """
        return {section_id: section.snapshot() for section_id, section in self._sections.items()}

    def update_world_state(
        self,
        previous_snapshot: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        """对标 Codex ContextManager::update_world_state()。

        返回:
          - fragments: diff 渲染的文本，注入对话历史
          - rollout_item: 当前快照（全量或 patch），用于持久化
        """
        current_snapshot = self.take_snapshot()

        fragments_parts: list[str] = []
        for section_id, section in self._sections.items():
            prev = previous_snapshot.get(section_id) if previous_snapshot else None
            diff = section.render_diff(prev)
            if diff:
                fragments_parts.append(diff)

        fragments = "\n\n".join(fragments_parts)

        if previous_snapshot is None:
            rollout_item = {"type": "full", "snapshot": current_snapshot}
        else:
            rollout_item = {"type": "patch", "snapshot": current_snapshot}

        return fragments, rollout_item

    def ingest_tool_output(self, tool_name: str, output: dict[str, Any]) -> None:
        """从工具返回值自动更新对应 Section。

        对标 Codex: 工具执行后，ContextManager 自动更新 WorldState。
        这是"事实写入 WorldState"的核心路径——
        不靠对话历史传递，而是靠结构化状态。
        """
        if tool_name == "skill_call":
            skill_name = output.get("skill", "")
            result = output.get("result", output)

            # section 由能力侧注册（register_skill_sections），内核零业务名
            self._ensure_section_map()
            section = self._skill_section_map.get(skill_name, "")
            if section == "copywrite":
                if isinstance(result, dict) and (result.get("title") or result.get("content")):
                    self.copywrite.update(result)
                    self.task_progress.update(
                        "copywrite_done",
                        completed=["文案生成"],
                        pending=(
                            ["卡片制作"] if not self.card_draft.snapshot()["page_count"] else []
                        ),
                    )

            elif section == "card":
                draft = result.get("card_draft", result)
                if isinstance(draft, dict) and (draft.get("pages") or draft.get("page_count")):
                    self.card_draft.update(draft)
                    self.task_progress.update(
                        "card_done",
                        completed=["文案生成", "卡片制作"],
                        pending=[],
                    )

            if isinstance(result, dict):
                file_path = result.get("file_path") or result.get("html_path")
                if file_path:
                    self.assets.add(
                        {
                            "id": result.get("asset_id", ""),
                            "type": "card_html",
                            "path": file_path,
                        }
                    )
                for img in result.get("images", []):
                    if isinstance(img, dict):
                        self.assets.add(
                            {
                                "id": img.get("id", ""),
                                "type": "image",
                                "path": img.get("path", img.get("url", "")),
                            }
                        )

        elif tool_name == "image_gen":
            # 裸 image_gen 工具（非 skill_call 包装）的产出也进 assets
            for img in output.get("images", []):
                if isinstance(img, dict):
                    self.assets.add(
                        {
                            "id": img.get("id", ""),
                            "type": "image",
                            "path": img.get("path", img.get("url", "")),
                        }
                    )

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "WorldState":
        """从持久化的快照恢复 WorldState。"""
        ws = cls()
        if "copywrite" in snapshot:
            ws.copywrite.update(snapshot["copywrite"])
        if "card_draft" in snapshot:
            ws.card_draft.update(snapshot["card_draft"])
        if "assets" in snapshot:
            for item in snapshot["assets"].get("items", []):
                ws.assets.add(item)
        if "task_progress" in snapshot:
            tp = snapshot["task_progress"]
            ws.task_progress.update(
                tp.get("phase", "idle"),
                tp.get("completed", []),
                tp.get("pending", []),
            )
        if "environment" in snapshot:
            env = snapshot["environment"]
            ws.environment.update(
                platform=env.get("platform", ""),
                model=env.get("model", ""),
                workspace=env.get("workspace", ""),
            )
        if "user_profile" in snapshot:
            up = snapshot["user_profile"]
            ws.user_profile.update(
                domain=up.get("domain", ""),
                style=up.get("style", ""),
                tone=up.get("tone", ""),
            )
        return ws
