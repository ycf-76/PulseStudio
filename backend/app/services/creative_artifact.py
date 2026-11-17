"""CreativeArtifact：统一的创作对象。

目标：让「对话式创作」和「工作流创作」共用同一份创作上下文，避免：

- 作品分析被压平成一段 prompt；
- 图文规划只有页面类型，没有页面角色；
- 前端只保存图片 URL，丢失组件级内容；
- 下一次创作无法继承上一次的视觉决策。

结构：

    CreativeArtifact
    ├── source        来源作品/会话/工作流
    ├── analysis      作品分析引用与结论
    ├── brief         创作简报：目标、受众、语气、方向、保留/避免模式
    ├── storyboard    分镜：每页的语义角色、组件、内容、图片角色、理由
    ├── assets        素材
    ├── quality       质量检查
    ├── review        人工审核
    └── history       变更历史

当前阶段以 JSON 文件持久化，后续可平滑迁移到数据库。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_STORE_DIR = Path(__file__).resolve().parents[2] / "data" / "creative_artifacts"

# 读-改-写互斥：抢不到锁的最长等待秒数（测试里会 monkeypatch 调小）
_LOCK_TIMEOUT = 5.0

# 平台文件锁（Windows msvcrt / POSIX fcntl），同一文件不同句柄互斥
try:
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _release_lock(fh) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

except ImportError:  # POSIX
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _release_lock(fh) -> None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


def _lock_path(artifact_id: str) -> Path:
    safe = "".join(c for c in artifact_id if c.isalnum() or c in "-_")[:64]
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    return _STORE_DIR / f".{safe}.lock"


@asynccontextmanager
async def artifact_lock(artifact_id: str, timeout: float | None = None):
    """同一 artifact 的读-改-写互斥。

    超时后抛 RuntimeError 而不是死等，防止两个协程互相卡死。
    """
    effective_timeout = _LOCK_TIMEOUT if timeout is None else timeout
    path = _lock_path(artifact_id)
    fh = open(path, "a+b")
    deadline = time.monotonic() + effective_timeout
    try:
        while True:
            if _try_lock(fh):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"artifact {artifact_id} 正在被其他操作修改，请稍后重试")
            await asyncio.sleep(0.01)
        yield
    finally:
        _release_lock(fh)
        fh.close()


# 页面类型 → 默认语义角色
_PAGE_ROLE_MAP: dict[str, str] = {
    "cover": "hook",
    "image_page": "hook",
    "pain_quote": "pain",
    "quote": "summary",
    "big_quote": "summary",
    "content": "explanation",
    "list": "evidence",
    "dark_panel": "evidence",
    "numbered_cards": "evidence",
    "icon_text": "evidence",
    "newspaper": "evidence",
    "steps": "action",
    "timeline": "process",
    "compare": "contrast",
    "qa": "explanation",
    "stat_card": "evidence",
    "profile": "credibility",
    "code_panel": "explanation",
    "end_page": "action",
}

# 语义角色 → 中文说明，用于给用户展示「为什么这样设计」
_ROLE_LABELS: dict[str, str] = {
    "hook": "开场抓注意力",
    "pain": "引发共鸣/痛点",
    "explanation": "解释说明",
    "evidence": "给出证据/要点",
    "contrast": "形成对比",
    "process": "展示过程演变",
    "action": "给出行动步骤",
    "credibility": "建立信任",
    "summary": "总结/金句收尾",
}

# 语义角色 → 推荐图片角色
_ROLE_IMAGE_ROLE: dict[str, str] = {
    "hook": "hero",
    "pain": "supporting",
    "explanation": "none",
    "evidence": "none",
    "contrast": "supporting",
    "process": "supporting",
    "action": "none",
    "credibility": "portrait",
    "summary": "atmosphere",
}

# 语义角色 → 设计理由
_ROLE_RATIONALE: dict[str, str] = {
    "hook": "第一页需要在缩略图里快速说清主题和利益点",
    "pain": "先建立共鸣，让读者觉得内容与自己有关",
    "explanation": "把关键概念讲清楚，避免只有结论没有过程",
    "evidence": "用要点或数据增强可信度",
    "contrast": "用差异制造信息增量，避免平铺直叙",
    "process": "让读者看到变化过程，增强代入感",
    "action": "给出可执行步骤，提高收藏率",
    "credibility": "增强账号或人物可信度",
    "summary": "用一句话收束，便于记忆和转发",
}


def role_label(role: str) -> str:
    return _ROLE_LABELS.get(role, "内容表达")


def infer_role(page_type: str, index: int, total: int) -> str:
    """根据页面类型与位置推断语义角色。"""
    if index == 0:
        return "hook"
    if total > 1 and index == total - 1:
        if page_type in ("end_page", "quote", "big_quote"):
            return "summary"
        if page_type in ("steps", "list", "numbered_cards"):
            return "action"
    return _PAGE_ROLE_MAP.get(page_type, "explanation")


class SourceRef(BaseModel):
    """创作来源。"""

    work_id: str = ""
    session_id: str = ""
    workflow_id: str = ""
    platform: str = "xiaohongshu"


class AnalysisRef(BaseModel):
    """作品分析引用：只存结论与来源，不复制整篇报告。"""

    analysis_id: str = ""
    source_work_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    keep_patterns: list[str] = Field(default_factory=list)
    avoid_patterns: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CreativeBrief(BaseModel):
    """创作简报：把分析结果翻译成可执行创作方向。"""

    goal: str = ""
    audience: str = ""
    tone: str = ""
    topic: str = ""
    title: str = ""
    visual_direction: str = "叙事型"
    template_id: str = ""
    keep_patterns: list[str] = Field(default_factory=list)
    avoid_patterns: list[str] = Field(default_factory=list)


class PageComponent(BaseModel):
    """页面使用的组件。"""

    semantic_type: str = ""
    page_type: str = "content"
    template_id: str = ""
    decoration: str = ""


class StoryboardPage(BaseModel):
    """分镜页：比 card page 多一层「为什么这样设计」。"""

    page_id: str = ""
    index: int = 0
    role: str = "explanation"
    role_label: str = ""
    rationale: str = ""
    source_evidence: str = ""
    visual_goal: str = ""
    component: PageComponent = Field(default_factory=PageComponent)
    image_role: str = "none"
    content: dict[str, Any] = Field(default_factory=dict)
    image_url: str = ""
    html_url: str = ""


class QualityIssue(BaseModel):
    code: str = ""
    message: str = ""
    page_index: int = -1
    level: str = "warning"


class QualityReport(BaseModel):
    status: str = "pending"
    score: int = 0
    checks: list[str] = Field(default_factory=list)
    issues: list[QualityIssue] = Field(default_factory=list)


class ReviewState(BaseModel):
    status: str = "pending"
    feedback: str = ""
    approved_pages: list[int] = Field(default_factory=list)
    rejected_pages: list[int] = Field(default_factory=list)


class ArtifactHistoryEntry(BaseModel):
    at: float = 0.0
    action: str = ""
    detail: str = ""


class CreativeArtifact(BaseModel):
    """统一创作对象。"""

    artifact_id: str = ""
    version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    owner_user_id: str = ""  # 拥有者；空值时 get_owned_artifact 走 session 兜底校验（兼容存量数据）
    card_deck: dict[str, Any] | None = None  # 结构化卡片状态（CardDeck dump），卡片编辑链路读写
    source: SourceRef = Field(default_factory=SourceRef)
    analysis: AnalysisRef = Field(default_factory=AnalysisRef)
    brief: CreativeBrief = Field(default_factory=CreativeBrief)
    storyboard: list[StoryboardPage] = Field(default_factory=list)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    quality: QualityReport = Field(default_factory=QualityReport)
    review: ReviewState = Field(default_factory=ReviewState)
    history: list[ArtifactHistoryEntry] = Field(default_factory=list)

    def touch(self, action: str = "", detail: str = "") -> None:
        now = time.time()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        if action:
            self.history.append(ArtifactHistoryEntry(at=now, action=action, detail=detail))


def build_storyboard(
    pages: list[dict[str, Any]],
    *,
    template_id: str = "",
    analysis: AnalysisRef | None = None,
) -> list[StoryboardPage]:
    """把 card_draft.pages 提升为带语义角色的分镜。"""
    total = len(pages)
    storyboard: list[StoryboardPage] = []
    for index, raw in enumerate(pages):
        page = dict(raw or {})
        page_type = page.get("type") or "content"
        role = page.get("role") or infer_role(page_type, index, total)
        content = {k: v for k, v in page.items() if k not in ("type", "role", "id")}
        storyboard.append(
            StoryboardPage(
                page_id=page.get("id") or f"p{index + 1}",
                index=index,
                role=role,
                role_label=role_label(role),
                rationale=page.get("rationale") or _ROLE_RATIONALE.get(role, ""),
                source_evidence=page.get("source_evidence", ""),
                visual_goal=page.get("visual_goal") or role_label(role),
                component=PageComponent(
                    semantic_type=role,
                    page_type=page_type,
                    template_id=template_id,
                    decoration=page.get("decoration", "") or "",
                ),
                image_role=page.get("image_role") or _ROLE_IMAGE_ROLE.get(role, "none"),
                content=content,
                image_url=page.get("imageUrl") or page.get("image_url") or "",
                html_url=page.get("htmlUrl") or page.get("html_url") or "",
            )
        )
    if analysis and analysis.keep_patterns and storyboard:
        storyboard[0].source_evidence = storyboard[0].source_evidence or analysis.keep_patterns[0]
    return storyboard


def build_artifact(
    *,
    artifact_id: str = "",
    owner_user_id: str = "",
    card_draft: dict[str, Any] | None = None,
    brief: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> CreativeArtifact:
    """从现有产物构造 CreativeArtifact。"""
    draft = card_draft or {}
    pages = draft.get("pages") or []
    template_id = draft.get("suggested_template") or draft.get("template") or ""

    artifact = CreativeArtifact(
        artifact_id=artifact_id or f"ca_{uuid.uuid4().hex[:12]}",
        owner_user_id=owner_user_id,
        # card_draft 里若带结构化卡片状态（CardDeck dump，composer.to_card_draft 产出），
        # 原样落进 card_deck，供卡片编辑链路（operations.get_owned_card）消费
        card_deck=draft.get("deck"),
        source=SourceRef(**(source or {})),
        analysis=AnalysisRef(**(analysis or {})),
        brief=CreativeBrief(**(brief or {})),
        storyboard=build_storyboard(
            pages, template_id=template_id, analysis=AnalysisRef(**(analysis or {}))
        ),
    )
    if template_id:
        artifact.brief.template_id = template_id
    artifact.touch(action="create", detail=f"pages={len(pages)}")
    return artifact


def _path_for(artifact_id: str) -> Path:
    safe = "".join(c for c in artifact_id if c.isalnum() or c in "-_")[:64]
    return _STORE_DIR / f"{safe}.json"


def save_artifact(artifact: CreativeArtifact) -> CreativeArtifact:
    """原子落盘：先写 tmp 再 os.replace，失败时旧文件保持原样。"""
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    artifact.touch(action="save")
    final = _path_for(artifact.artifact_id)
    tmp = final.with_name(f"{final.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(
            artifact.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return artifact


def _read_json_lenient(path: Path) -> dict[str, Any]:
    """读产物 JSON：容忍 BOM 与字符串内的裸控制字符。

    存量产物里出现过 LLM 直接输出的换行/制表符被原样写进字符串的情况，
    严格模式 json.loads 会整条判为损坏（"Invalid control character"），
    产物就此变成不可访问的孤儿。strict=False 只放宽控制字符，不改变语义。
    """
    return json.loads(path.read_text(encoding="utf-8-sig"), strict=False)


def load_artifact(artifact_id: str) -> CreativeArtifact | None:
    path = _path_for(artifact_id)
    if not path.exists():
        return None
    try:
        data = _read_json_lenient(path)
        return CreativeArtifact(**data)
    except Exception:
        return None


def delete_artifact(artifact_id: str) -> bool:
    path = _path_for(artifact_id)
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True


def _summarize(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """把落盘 JSON 压成列表项。列表/详情共用同一套推导，避免两处漂移。"""
    storyboard = data.get("storyboard") or []
    page_images = [p.get("image_url", "") for p in storyboard if p.get("image_url")]
    cover_url = page_images[0] if page_images else ""
    first_page_html = ""
    if storyboard:
        first_content = storyboard[0].get("content") or {}
        first_page_html = first_content.get("htmlContent", "")
    if not cover_url:
        card_draft = data.get("card_draft") or {}
        cd_pages = card_draft.get("pages") or []
        for p in cd_pages:
            img = p.get("imageUrl") or ""
            if img:
                cover_url = img
                page_images = [img] + [
                    pp.get("imageUrl", "") for pp in cd_pages if pp.get("imageUrl") and pp is not p
                ]
                break
        if not cover_url:
            for p in cd_pages:
                pngs = p.get("pngUrls") or []
                if pngs:
                    cover_url = pngs[0]
                    break
    card_draft_pages = []
    for sb in storyboard:
        page_item = {
            "id": sb.get("page_id", ""),
            "type": (sb.get("component") or {}).get("page_type", "content"),
            "role": sb.get("role", ""),
            "content": sb.get("content") or {},
        }
        if sb.get("image_url"):
            page_item["imageUrl"] = sb["image_url"]
        if sb.get("html_url"):
            page_item["htmlUrl"] = sb["html_url"]
        card_draft_pages.append(page_item)
    card_draft = {
        "title": (data.get("brief") or {}).get("title", ""),
        "suggested_template": (data.get("brief") or {}).get("template_id", ""),
        "pages": card_draft_pages,
        # 结构化卡片本体：有则前端详情页可直接进编辑态，无则只能看图
        "deck": data.get("card_deck") or None,
    }
    all_page_htmls = []
    for sb in storyboard:
        content = sb.get("content") or {}
        html = content.get("htmlContent", "")
        if html:
            all_page_htmls.append(html)
    return {
        "artifact_id": data.get("artifact_id", path.stem),
        "title": (data.get("brief") or {}).get("title", ""),
        "topic": (data.get("brief") or {}).get("topic", ""),
        "page_count": len(storyboard),
        "updated_at": data.get("updated_at", 0),
        "cover_url": cover_url,
        "page_images": page_images,
        "first_page_html": first_page_html,
        "card_draft": card_draft,
        "all_page_htmls": all_page_htmls,
        # 归属与来源：前端据此把产物挂回对应会话/作品，不再全量混在一起
        "owner_user_id": data.get("owner_user_id", ""),
        "source": data.get("source") or {},
    }


def _iter_summaries(limit: int | None = None) -> list[dict[str, Any]]:
    if not _STORE_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(_STORE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = _read_json_lenient(path)
        except (OSError, ValueError) as exc:
            logger.warning("跳过损坏的创作产物 %s: %s", path.name, exc)
            continue
        items.append(_summarize(data, path))
        if limit is not None and len(items) >= limit:
            break
    return items


def list_artifacts(limit: int = 50) -> list[dict[str, Any]]:
    """无归属过滤的全量列表。仅供内部/调试使用，API 层必须走 list_artifacts_for_user。"""
    return _iter_summaries(limit=limit)


async def session_owner(session_id: str) -> str:
    """查会话归属用户；查不到或数据库不可用时返回空串（视为不可验证）。"""
    if not session_id:
        return ""
    try:
        from app.services.chat_session import get_session

        session = await get_session(session_id)
    except Exception as exc:  # noqa: BLE001 - 归属查询失败必须降级为"不可验证"，不能放行
        logger.warning("查询会话归属失败 session=%s: %s", session_id, exc)
        return ""
    return str((session or {}).get("user_id") or "")


async def verify_owner(artifact: CreativeArtifact, user_id: str) -> None:
    """校验产物归属：优先 owner_user_id，存量无主产物回落到会话归属。

    通过返回 None；不通过抛 PermissionError，由 API 层映射成 403。
    """
    if not user_id:
        raise PermissionError("缺少用户身份，不能访问创作产物")
    if artifact.owner_user_id:
        if artifact.owner_user_id != user_id:
            raise PermissionError("无权访问该作品")
        return
    if not artifact.source.session_id:
        raise PermissionError("作品缺少可验证的所有者")
    if await session_owner(artifact.source.session_id) != user_id:
        raise PermissionError("无权访问该作品")


async def list_artifacts_for_user(
    user_id: str,
    limit: int = 50,
    *,
    work_id: str = "",
    session_id: str = "",
) -> list[dict[str, Any]]:
    """按用户 + 可选作品/会话维度隔离的产物列表。

    - 归属：owner_user_id 命中当前用户；存量无主产物回落到会话归属校验。
    - 维度：work_id / session_id 命中则只返回该作品或该会话的产物。
    """
    if not user_id:
        return []
    items: list[dict[str, Any]] = []
    for summary in _iter_summaries():
        source = summary.get("source") or {}
        if work_id and source.get("work_id") != work_id:
            continue
        if session_id and source.get("session_id") != session_id:
            continue
        owner = summary.get("owner_user_id") or ""
        if owner != user_id:
            if owner or not source.get("session_id"):
                continue
            if await session_owner(source["session_id"]) != user_id:
                continue
        items.append(summary)
        if len(items) >= limit:
            break
    return items


def _apply_card_draft(
    artifact: CreativeArtifact,
    card_draft: dict[str, Any],
    pages: list[dict[str, Any]],
    title: str,
    template_id: str,
) -> None:
    """把最新一份 card_draft 覆盖进已有产物（保留归属与历史）。"""
    if card_draft.get("deck"):
        artifact.card_deck = card_draft["deck"]
    artifact.storyboard = build_storyboard(pages, template_id=template_id)
    if title:
        artifact.brief.title = title
        artifact.brief.topic = title
    artifact.brief.template_id = template_id
    artifact.version += 1


async def save_card_draft_artifact(
    *,
    user_id: str,
    card_draft: dict[str, Any],
    session_id: str = "",
    work_id: str = "",
    platform: str = "xiaohongshu",
) -> str:
    """把一次创作产出的 card_draft 落成创作产物，返回 artifact_id。

    落盘后「我的空间 · 平台生成」下次拉取即可扫到，不再依赖前端本地草稿。

    复用顺序（同一份创作不重复建产物）：
    1. work_id 形如 `artifact:ca_x` → 直接续写那一条（先鉴权）；
    2. work_id 命中某条产物的 source.work_id → 续写那一条；
    3. 同一会话且未绑定作品的产物 → 续写那一条（多轮反复出稿只更新同一作品）。
    """
    if not user_id or not isinstance(card_draft, dict):
        return ""
    pages = card_draft.get("pages") or []
    if not pages:
        return ""

    title = str(card_draft.get("title") or "").strip()
    template_id = str(card_draft.get("suggested_template") or card_draft.get("template") or "")

    existing_id = ""
    if work_id.startswith("artifact:"):
        candidate = work_id[len("artifact:") :]
        candidate_artifact = load_artifact(candidate)
        if candidate_artifact is not None:
            try:
                await verify_owner(candidate_artifact, user_id)
                existing_id = candidate
            except PermissionError:
                logger.warning("续写创作产物被拒（非本人作品）: %s", candidate)
    if not existing_id and work_id:
        found = await list_artifacts_for_user(user_id, limit=1, work_id=work_id)
        if found:
            existing_id = str(found[0].get("artifact_id") or "")
    if not existing_id and session_id:
        found = await list_artifacts_for_user(user_id, limit=20, session_id=session_id)
        for summary in found:
            if str((summary.get("source") or {}).get("work_id") or ""):
                continue
            existing_id = str(summary.get("artifact_id") or "")
            break

    if existing_id:
        async with artifact_lock(existing_id):
            artifact = load_artifact(existing_id)
            if artifact is not None:
                _apply_card_draft(artifact, card_draft, pages, title, template_id)
                save_artifact(artifact)
                return artifact.artifact_id

    artifact = build_artifact(
        owner_user_id=user_id,
        card_draft=card_draft,
        brief={"title": title, "topic": title},
        source={"session_id": session_id, "work_id": work_id, "platform": platform},
    )
    save_artifact(artifact)
    return artifact.artifact_id


def to_card_draft(artifact: CreativeArtifact) -> dict[str, Any]:
    """反向输出给卡片编辑器使用，保证不破坏现有渲染链路。"""
    pages: list[dict[str, Any]] = []
    for page in artifact.storyboard:
        item: dict[str, Any] = {
            "id": page.page_id,
            "type": page.component.page_type,
            "role": page.role,
            "rationale": page.rationale,
            "source_evidence": page.source_evidence,
            "image_role": page.image_role,
        }
        item.update(page.content)
        if page.image_url:
            item["imageUrl"] = page.image_url
        if page.html_url:
            item["htmlUrl"] = page.html_url
        pages.append(item)
    return {
        "title": artifact.brief.title,
        "suggested_template": artifact.brief.template_id,
        "pages": pages,
        # 编辑区装载用：deck 是唯一可回写的结构，pages 只是它的只读投影
        "deck": artifact.card_deck,
        "artifact_id": artifact.artifact_id,
        "version": artifact.version,
    }
