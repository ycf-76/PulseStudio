"""Studio 工作区脚手架与 timeline.json 校验契约。

职责（纯结构/时间合法性，无风格常量——颜色/字体/动效参数不在此模块）：
  - create_workspace：outputs/studio/<film_id>/ 下 7 个子目录（幂等）
  - validate_timeline：防御式校验，任何输入都不抛异常，返回中文错误列表
    （错误信息内嵌字段路径，供创作 agent 自读自修）
  - save_timeline / load_timeline：timeline.json 落 ws["analysis"]，utf-8 + ensure_ascii=False
"""

from __future__ import annotations

import json
import math
from pathlib import Path

_STUDIO_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "studio"

_SUBDIRS = ("inputs", "analysis", "scene", "assets", "review", "out")

# .cache/：IDE 文件树默认隐藏点目录——抽帧（.cache/frames）与渲染逐帧 PNG
# （.cache/render）是纯中间产物，不进可见工作区。渲染帧在 run 结束时整体删除。
_CACHE_DIRNAME = ".cache"


def create_workspace(film_id: str) -> dict[str, Path]:
    """建 outputs/studio/<film_id>/ 工作区（幂等）。

    返回各路径 dict：
      - 常规键 = 目录名（inputs/analysis/scene/assets/review/out）
      - ws["frames"] = .cache/render（渲染器逐帧截图目录，review 同样读它；
        首次渲染前不存在，渲染器自建）
      - ws[".cache"] = .cache 根（抽帧目标为 .cache/frames，由抽帧工具自建）
    """
    root = _STUDIO_ROOT / film_id
    ws = {name: root / name for name in _SUBDIRS}
    cache_root = root / _CACHE_DIRNAME
    ws[".cache"] = cache_root
    ws["frames"] = cache_root / "render"
    for name, p in ws.items():
        if name != "frames":  # .cache/render 惰性：渲染器自建，run 结束整体删除
            p.mkdir(parents=True, exist_ok=True)
    return ws


def _is_num(v: object) -> bool:
    """数字且非 bool、非 NaN/Inf（bool 是 int 子类；json.loads 默认放行 NaN/Infinity，
    NaN>0 恒 False 会溜过 >0 检查。int 不走 isfinite——超大 int 转 float 会 OverflowError）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if isinstance(v, float):
        return math.isfinite(v)
    return True


def _type_err(field: str, v: object) -> str:
    """数字字段类型错的消息：非有限 float 单独点名，避免「应为数字，实际为 float」误导自修。"""
    if isinstance(v, float) and not math.isfinite(v):
        return f"{field} 必须为有限数字（NaN/Inf 不可用）"
    return f"{field} 应为数字，实际为 {type(v).__name__}"


def validate_timeline(tl: dict) -> list[str]:
    """校验 timeline 结构与时间合法性，返回错误列表（空 = 通过）。

    防御式：tl 可能缺键、类型错、triggers 挂错位置——所有分支返回错误字符串，
    绝不抛异常。规则：
      必填 fps>0 / duration>0 / canvas{w,h}
      speech[] 每项含 start/end/text 且 end>start
      beats[] 每项含 id/t_start/t_end/title/visual_task 且 t_end>t_start，
              beats 按 t_start 升序不重叠
      triggers[].t 落在其所属 beat 的 [t_start,t_end] 内（trigger 挂在 beat 的 triggers 字段下）
    """
    if not isinstance(tl, dict):
        return [f"timeline 应为对象(dict)，实际为 {type(tl).__name__}"]
    errs: list[str] = []

    if "fps" not in tl:
        errs.append("缺少 fps")
    elif not _is_num(tl["fps"]):
        errs.append(_type_err("fps", tl["fps"]))
    elif tl["fps"] <= 0:
        errs.append(f"fps 必须 > 0，实际为 {tl['fps']}")

    if "duration" not in tl:
        errs.append("缺少 duration")
    elif not _is_num(tl["duration"]):
        errs.append(_type_err("duration", tl["duration"]))
    elif tl["duration"] <= 0:
        errs.append(f"duration 必须 > 0，实际为 {tl['duration']}")

    if "canvas" not in tl:
        errs.append("缺少 canvas")
    elif not isinstance(tl["canvas"], dict):
        errs.append(f"canvas 应为对象(dict)，实际为 {type(tl['canvas']).__name__}")
    else:
        canvas = tl["canvas"]
        for dim in ("w", "h"):
            if dim not in canvas:
                errs.append(f"canvas.{dim} 缺失")
            elif not _is_num(canvas[dim]):
                errs.append(_type_err(f"canvas.{dim}", canvas[dim]))
            elif canvas[dim] <= 0:
                errs.append(f"canvas.{dim} 必须 > 0，实际为 {canvas[dim]}")

    if "speech" not in tl:
        errs.append("缺少 speech")
    elif not isinstance(tl["speech"], list):
        errs.append(f"speech 应为列表，实际为 {type(tl['speech']).__name__}")
    else:
        for i, seg in enumerate(tl["speech"]):
            tag = f"speech[{i}]"
            if not isinstance(seg, dict):
                errs.append(f"{tag} 应为对象(dict)，实际为 {type(seg).__name__}")
                continue
            for field in ("start", "end", "text"):
                if field not in seg:
                    errs.append(f"{tag}.{field} 缺失")
            for tfield in ("start", "end"):
                if tfield in seg and not _is_num(seg[tfield]):
                    errs.append(_type_err(f"{tag}.{tfield}", seg[tfield]))
            if _is_num(seg.get("start")) and _is_num(seg.get("end")) and seg["end"] <= seg["start"]:
                errs.append(
                    f"{tag}.end 必须 > start（实际 end={seg['end']}, start={seg['start']}）"
                )
            if "text" in seg and not isinstance(seg["text"], str):
                errs.append(f"{tag}.text 应为字符串，实际为 {type(seg['text']).__name__}")

    if "beats" not in tl:
        errs.append("缺少 beats")
    elif not isinstance(tl["beats"], list):
        errs.append(f"beats 应为列表，实际为 {type(tl['beats']).__name__}")
    else:
        prev: dict | None = None
        for i, beat in enumerate(tl["beats"]):
            tag = f"beats[{i}]"
            if not isinstance(beat, dict):
                errs.append(f"{tag} 应为对象(dict)，实际为 {type(beat).__name__}")
                prev = None
                continue
            for field in ("id", "t_start", "t_end", "title", "visual_task"):
                if field not in beat:
                    errs.append(f"{tag}.{field} 缺失")
            for sfield in ("id", "title", "visual_task"):
                if sfield in beat and not isinstance(beat[sfield], str):
                    errs.append(f"{tag}.{sfield} 应为字符串，实际为 {type(beat[sfield]).__name__}")
            for tfield in ("t_start", "t_end"):
                if tfield in beat and not _is_num(beat[tfield]):
                    errs.append(_type_err(f"{tag}.{tfield}", beat[tfield]))
            if (
                _is_num(beat.get("t_start"))
                and _is_num(beat.get("t_end"))
                and beat["t_end"] <= beat["t_start"]
            ):
                errs.append(
                    f"{tag}.t_end 必须 > t_start（实际 t_end={beat['t_end']}, t_start={beat['t_start']}）"
                )
            if (
                prev is not None
                and _is_num(prev.get("t_end"))
                and _is_num(beat.get("t_start"))
                and beat["t_start"] < prev["t_end"]
            ):
                errs.append(
                    f"{tag}.t_start 与上一 beat 重叠（须按 t_start 升序且不重叠，上一 beat t_end={prev['t_end']}）"
                )
            _check_triggers(errs, i, beat)
            prev = beat if _is_num(beat.get("t_start")) else None

    if "triggers" in tl:
        errs.append("顶层 triggers 挂错位置：trigger 应挂在各 beat 的 triggers 字段下")

    return errs


def _check_triggers(errs: list[str], beat_i: int, beat: dict) -> None:
    """校验单个 beat 的 triggers（挂在 beat.triggers 下），追加错误到 errs。"""
    if "triggers" not in beat:
        return
    tag = f"beats[{beat_i}].triggers"
    triggers = beat["triggers"]
    if not isinstance(triggers, list):
        errs.append(f"{tag} 应为列表，实际为 {type(triggers).__name__}")
        return
    t_start = beat.get("t_start")
    t_end = beat.get("t_end")
    span_ok = _is_num(t_start) and _is_num(t_end)
    for j, trig in enumerate(triggers):
        ttag = f"{tag}[{j}]"
        if not isinstance(trig, dict):
            errs.append(f"{ttag} 应为对象(dict)，实际为 {type(trig).__name__}")
            continue
        if "t" not in trig:
            errs.append(f"{ttag}.t 缺失")
        elif not _is_num(trig["t"]):
            errs.append(_type_err(f"{ttag}.t", trig["t"]))
        elif span_ok and not (t_start <= trig["t"] <= t_end):
            errs.append(f"{ttag}.t={trig['t']} 超出所属 beat [t_start,t_end]=[{t_start},{t_end}]")


def _timeline_path(ws: dict) -> Path:
    return Path(ws["analysis"]) / "timeline.json"


def save_timeline(ws: dict, tl: dict) -> Path:
    """timeline.json 落 ws["analysis"] 目录（utf-8 + ensure_ascii=False），返回文件路径。"""
    p = _timeline_path(ws)
    p.write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_timeline(ws: dict) -> dict:
    """读回 ws["analysis"]/timeline.json。"""
    return json.loads(_timeline_path(ws).read_text(encoding="utf-8"))
