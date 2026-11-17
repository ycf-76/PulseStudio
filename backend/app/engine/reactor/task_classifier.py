"""Task classifier — replaces hardcoded signal tuples / regex in session.py & router.

Each skill declares its task_type, sub_type, and trigger_words in
manifest.yaml (or via class attributes).  TaskClassifier aggregates them
at startup and provides a pure-function classify() that replaces the
inline _creation_signals / _operation_signals tuples and the scattered
trigger-word tuples in api/routers/chat_agent.py.

Adding a new skill = adding a new manifest entry.  Zero edits to
session.py, prompt.py, or router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskType(Enum):
    PURE_QA = "pure_qa"
    OPERATION = "operation"
    CREATION = "creation"


@dataclass
class SkillManifest:
    name: str
    task_type: TaskType
    trigger_words: list[str] = field(default_factory=list)
    sub_type: str = ""
    noise_words: list[str] = field(default_factory=list)


@dataclass
class TaskClassifier:
    """Code-side task classifier — replaces session.py hardcoded signal tuples.

    Initialized once from all skill manifests; classify() called per turn.
    """

    _creation_triggers: set[str] = field(default_factory=set)
    _operation_triggers: set[str] = field(default_factory=set)
    _manifests: list[SkillManifest] = field(default_factory=list)

    @classmethod
    def from_manifests(cls, manifests: list[SkillManifest]) -> TaskClassifier:
        creation: set[str] = set()
        operation: set[str] = set()
        for m in manifests:
            target = (
                creation
                if m.task_type == TaskType.CREATION
                else operation if m.task_type == TaskType.OPERATION else None
            )
            if target is not None:
                target.update(t for t in m.trigger_words if isinstance(t, str) and len(t) >= 2)
        return cls(
            _creation_triggers=creation, _operation_triggers=operation, _manifests=list(manifests)
        )

    def classify(self, user_message: str) -> TaskType:
        msg = user_message.lower()
        if any(isinstance(t, str) and len(t) >= 2 and t in msg for t in self._operation_triggers):
            return TaskType.OPERATION
        if any(isinstance(t, str) and len(t) >= 2 and t in msg for t in self._creation_triggers):
            return TaskType.CREATION
        return TaskType.PURE_QA

    def has_creation_signal(self, user_message: str) -> bool:
        msg = user_message.lower()
        return any(isinstance(t, str) and len(t) >= 2 and t in msg for t in self._creation_triggers)

    def has_operation_signal(self, user_message: str) -> bool:
        msg = user_message.lower()
        return any(
            isinstance(t, str) and len(t) >= 2 and t in msg for t in self._operation_triggers
        )

    def has_signal(self, user_message: str, sub_type: str) -> bool:
        """Check if the message matches any trigger word of manifests with the given sub_type."""
        msg = user_message.lower()
        for m in self._manifests:
            if m.sub_type == sub_type:
                if any(isinstance(t, str) and len(t) >= 2 and t in msg for t in m.trigger_words):
                    return True
        return False

    def get_noise_words(self, sub_type: str) -> list[str]:
        """Aggregate noise_words from all manifests with the given sub_type."""
        result: list[str] = []
        for m in self._manifests:
            if m.sub_type == sub_type:
                result.extend(m.noise_words)
        return result


def build_classifier_from_skills(skills: list[Any] | None) -> TaskClassifier:
    """Build a TaskClassifier from ReactorSession._skills list.

    Each skill may expose:
      - task_type: str ("creation" | "operation" | "pure_qa")
      - trigger_words: list[str]
    Falls back to DEFAULT_MANIFESTS for built-in skills not in the list.
    """
    manifests: list[SkillManifest] = list(DEFAULT_MANIFESTS)

    if skills:
        for s in skills:
            name = getattr(s, "name", "")
            if not name:
                continue
            tt_str = getattr(s, "task_type", None) or "creation"
            try:
                tt = TaskType(tt_str)
            except ValueError:
                tt = TaskType.CREATION
            triggers = getattr(s, "trigger_words", None) or []
            triggers = [str(t) for t in triggers if isinstance(t, str) and t]
            manifests.append(
                SkillManifest(
                    name=name,
                    task_type=tt,
                    trigger_words=list(triggers),
                )
            )

    return TaskClassifier.from_manifests(manifests)


DEFAULT_MANIFESTS: list[SkillManifest] = [
    SkillManifest(
        name="content_analysis",
        task_type=TaskType.CREATION,
        sub_type="analysis",
        trigger_words=["分析一下", "分析这篇", "分析这个", "拆解一下", "拆解这篇"],
    ),
    SkillManifest(
        name="local_file_search",
        task_type=TaskType.OPERATION,
        sub_type="file_search",
        trigger_words=[
            "找文件",
            "查文件",
            "本地文件",
            "本地文件夹",
            "本地文档",
            "本地目录",
            "搜索文件",
            "查找文件",
        ],
        noise_words=[
            "帮我",
            "请",
            "查找",
            "搜索",
            "搜一下",
            "找一下",
            "寻找",
            "看看",
            "找",
            "查",
            "本地文件",
            "本地文件夹",
            "本地文档",
            "本地目录",
            "文件夹",
            "工作区",
            "文件",
            "文档",
            "目录",
            "中的",
            "里面",
            "里",
            "一个",
            "那份",
            "那篇",
            "这个",
            "有没有",
        ],
    ),
]
