"""Studio 轻量工具循环（Video Studio P2 Task 3）— LLM 多轮 JSON-action 协议。

职责：把「读打法 → 拆内容 → 写场景 → 渲染 → 质检 → 修复 → 交付」交给带工具的
创作 agent 自主完成。循环本体只做协议与预算：
  - 每轮 LLM 输出一个 JSON action（{"action": "...", "args": {...}}），循环执行后
    把结果（按 UTF-8 字节截断至 ≤4KB、保持 JSON 可解析）作为下一条 user 消息回喂；
  - 工具白名单闭包绑定 workspace：读限 inputs/rules/，写限 scene/（path 相对
    工作区根），拒绝 .. / 绝对路径 / 盘符 / 反斜杠 / 协议前缀逃逸；
  - 协议纪律：非 JSON / 缺 action / 未知 action / args 非法 → 计一轮、错误回喂、不崩；
  - 预算硬顶：总轮数 ≤ max_turns；渲染 ≤ 3 次；render→review→fix ≤ 3 轮；
    超限如实返回 ok=False（error 含最近 review 错误摘要），不宣称完成；
  - finish 前置：已成功 render 且最近一次 review errors 为空，否则拒绝并回喂原因。

红线自证：本模块零风格内容——system prompt 只含工作区结构 / 工具协议 / 场景
契约（seekTo 确定性、禁随机）/ 纪律；打法风格只存在于 inputs/rules/ 下的文本，
agent 经 list_rules / read_rules_file 分阶段自行读取。

阻塞调用（whisper / 抽帧 / 渲染 / 质检 / 预览服务启动）一律 asyncio.to_thread
包裹；person_matting.matte_person 本身为 async，直接 await。
预览服务生命周期绑定本次 run：返回时自动 stop（实时预览只发生在创作期间，
残留的守护线程服务不再有用）。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from capabilities.video.adapters.html_to_video import resolve_ffmpeg
from capabilities.video.adapters.person_matting import matte_person
from capabilities.video.adapters.studio_assets import (
    extract_frames,
    probe_bottom_elements,
    probe_duration,
)
from capabilities.video.adapters.studio_render import render_scene
from capabilities.video.adapters.studio_transcript_fix import fix_homophones
from capabilities.video.adapters.whisper_transcribe import transcribe
from capabilities.video.services.studio_preview import StudioPreviewServer
from capabilities.video.services.studio_review import review_workspace
from capabilities.video.services.studio_workspace import save_timeline, validate_timeline

logger = logging.getLogger(__name__)

_FEEDBACK_LIMIT = 4096      # 回喂结果截断目标（字符数，约 4KB）
_MAX_RENDER_ATTEMPTS = 3    # 渲染尝试硬顶
_MAX_REWORK_ROUNDS = 3      # render→review→fix 修复轮硬顶
_FRAME_FPS = 24.0           # prepare_assets("frames") 的抽帧帧率（计划规定）

_SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# generate_image：比例 → 万相 size（_WANX_SIZE_MAP 认可的横竖方三档）
_IMAGE_RATIO_SIZE = {"1:1": "1024*1024", "16:9": "1280*720", "9:16": "720*1280"}
_GEN_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")

_MIX_GAIN_DEFAULT = 0.9     # SFX 单次命中默认增益（避免压过口播）
_MIX_T_MAX = 7200.0         # 无 timeline 时的时间上界（秒）


def _mix_sfx_sync(video_path: str, events: list[dict], out_path: Path) -> None:
    """口播音轨 + 定点 SFX → 单条混合音轨（aac）。

    amix duration=first 以口播轨时长为准；normalize=0 防止 amix 把口播
    压低一半。每个事件用 volume+adelay 定点注入，纯 ffmpeg 无中间落盘。
    """
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(video_path)]
    filters = []
    for i, ev in enumerate(events, start=1):
        cmd += ["-i", str(Path(_SFX_DIR) / ev["file"])]
        ms = int(round(ev["t"] * 1000))
        g = ev["gain"]
        filters.append(f"[{i}:a]volume={g:.3f},adelay={ms}|{ms}[sfx{i}]")
    mix_in = "[0:a]" + "".join(f"[sfx{i}]" for i in range(1, len(events) + 1))
    filters.append(
        f"{mix_in}amix=inputs={len(events) + 1}:duration=first:normalize=0[aout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[aout]",
            "-c:a", "aac", "-b:a", "128k", str(out_path)]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(cmd, capture_output=True, creationflags=creationflags)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace")[-600:]
        raise RuntimeError(f"ffmpeg amix failed (rc={proc.returncode}): {tail}")


def _get_image_adapter():
    """万相文生图适配器（延迟初始化；测试 monkeypatch 本函数打桩）。"""
    from app.config import get_settings
    from capabilities.image_gen.adapters.image_gen import WanxAdapter

    settings = get_settings()
    api_key = getattr(settings, "dashscope_api_key", "") or ""
    if not api_key:
        raise RuntimeError("dashscope_api_key 未配置（backend/.env），生图工具不可用")
    model = getattr(settings, "wanx_model", "") or "wanx2.1-t2i-turbo"
    return WanxAdapter(api_key=api_key, model=model)

_SYSTEM_PROMPT = """\
你是「视频工作室」创作 agent：在固定工作区内，通过工具循环把一支口播原片
加工成成片（读打法 → 拆内容 → 写场景 → 渲染 → 质检 → 修复 → 交付）。
你每轮只输出一个 JSON 对象，不输出任何其他文字。

# 工作区目录（键 → 目录）
- inputs/    输入物料；inputs/rules/ 下是打法方法论（纯文本规则，分阶段读）
- analysis/  分析产物：speech.json（转录）、timeline.json（时间轴）
- scene/     场景工程：index.html 及其相对引用的 js/css/资产
- assets/    素材产物：人物抠像帧轨、SFX 文件清单等
- .cache/    隐藏缓存：抽帧（.cache/frames）、渲染逐帧截图（.cache/render）；
             纯中间产物，场景只允许引用 .cache/frames/ 下的抽帧 jpg
- review/    质检产物：contact sheet 等
- out/       成片：final.mp4

# 工具协议
每轮输出：{"action": "<名字>", "args": {...}}（缺 args 视为 {}）。
循环执行后回喂：{"turn": N, "action": "...", "ok": true/false, "result": ...}。

可用 action：
- list_rules                      枚举 inputs/rules/ 文件清单
- read_rules_file(path)           读 inputs/rules/ 下的相对路径（如 "method.md"）
- transcribe                      转录原片语音（词级时间戳）→ analysis/speech.json（只跑一次，重调返回缓存）
- prepare_assets(kind)            kind ∈ {"frames","matting","sfx_list"}：抽帧 / 人物抠像 / SFX 文件清单
- generate_image(prompt,name,ratio) 万相文生图 → assets/gen/<name>.png；ratio ∈ {"1:1","16:9","9:16"}；
                                  scene 用返回的 example 相对路径引用
- mix_sfx(events)                 定点音效混音：events=[{file,t,gain?},...]，file 逐字来自
                                  sfx_list；→ out/audio_mixed.m4a（重跑覆盖旧轨）
- save_timeline(timeline)         校验并落盘时间轴到 analysis/timeline.json；校验错误列表会原样回喂
- write_scene_file(path, content) 写场景文件；path 相对工作区根且必须位于 scene/ 下（如 "scene/index.html"）
- render(audio)                   按 timeline 的 duration/fps/canvas 渲染场景；audio ∈
                                  {"original","mixed"}（默认 original）→ out/final.mp4
- review                          质检成片：contact sheet + 响度 + 帧完整性 + 时长偏差；完整 errors+loudness 回喂
- start_preview                   启动本地实时预览，回喂 preview_url
- finish(summary)                 交付成片。前置：已成功 render 且最近一次 review 的 errors 为空；
                                  review 必须发生在最近一次成功 render 之后；render 后再改场景/时间轴
                                  须重新 render → review，否则 finish 被拒
- fail(reason)                    如实放弃并终止

# timeline 契约（save_timeline 校验规则）
{"fps":>0, "duration":>0, "canvas":{"w":>0,"h":>0},
 "speech":[{"start","end","text"}...]（end>start）,
 "beats":[{"id","t_start","t_end","title","visual_task","triggers":[{"t",...}]}...]}
beats 按 t_start 升序且互不重叠；trigger.t 必须落在所属 beat 的 [t_start, t_end] 内。

# 场景契约（确定性硬要求）
scene/index.html 必须定义全局函数 window.seekTo(t)：给定秒数 t，画面状态完全确定，
渲染器逐帧调用它取帧。画面只允许由 t 决定：禁止 Math.random / Date / 真实时钟参与
画面状态；初始加载时自行调用一次 window.seekTo(0)。场景引用的资产放在 scene/ 下用相对路径。

# 纪律
- 先分阶段读打法（list_rules → read_rules_file 逐文件读），再动手。
- 语言纪律：场景内所有文字的语言/字形必须与转写文本（analysis/speech.json）一致，
  不得自行改用其他字形（如转写为简体则全片简体）。
- 素材纪律：口播源视频的真人画面是内容主体，写 scene 前必须先 prepare_assets(frames)
  （口播类必做；抠像轨可选 prepare_assets(matting)）。场景必须通过
  ../.cache/frames/ 或 ../assets/ 引用真实素材入画，素材路径必须逐字使用工具返回的 pattern/example，
  禁止自造命名；review 会对断链引用报 error。
- 图案素材纪律：需要插画/图表/示意图等非真人画面的图案素材时，先 generate_image
  生成（prompt 描述具体画面与风格，name 用短横线小写），再按返回路径引用入画。
  素材下限：整片至少 3 张 generate_image 生成的图案素材入画（真人画面与字幕
  之外的画面元素），口播内容无对应素材时必须生图补齐——禁止只靠纯色背景 +
  文字撑满全片。背景纪律：禁止大面积纯色/单色渐变留白，背景必须有构图元素
  （纹理、图形、生图素材、装饰形状等）铺满画面，视觉密度以"每个时段画面都有
  与当句口播对应的内容"为准。
  生图 prompt 禁止要求画面内出现文字/字母/数字（文生图文字不可控，必出乱码），
  画面文字一律由场景代码叠加；生成后若素材仍带乱码文字，重生成或改用无字构图。
  节奏手法服务于内容：重点句可放大主体（镜头推近聚焦），阐述句可演示对应图案
  素材，段落进出可用转场（位移/缩放/擦除/模糊淡入等确定性动效）——具体手法
  跟随打法规则与用户 brief，不凭空造素材、不堆砌装饰。
- 渲染性能纪律：逐帧渲染时浏览器每换一次 <img> 的 src 都要重新解码整幅图，
  24fps 逐帧换 src 会拖垮渲染（超时=渲染作废，预算只有 3 次）。人物帧轨的
  src 切换必须降频：按镜头/beat 切换，或至多 12fps 采样（每 2 帧换一次
  src，i=round(t*12)），动作平滑靠 CSS transform 过渡补足；撕裂/分屏等
  多个图层引用同一帧画面时必须共用同一 src 路径（浏览器缓存零重复解码）。
- 音效纪律：需要重点词/强调音效时，先 prepare_assets("sfx_list") 获取真实音效
  清单，再按词级时间戳定点 mix_sfx（file 逐字用清单里的文件名；render 必须传
  {"audio":"mixed"} 才会混入成片）。每个音效只挂一个明确语义的时间点，全片
  每 8-10 秒至多一次、gain ≤ 0.9 避免压过口播；重音选低频类（如 impact）。
  字幕层必须独立于抖动元素，保证字幕全程稳定可读；无明确语义对应点就不加
  音效，禁止整片铺满。
- 底层文字纪律：源素材可能自带烧录字幕/水印/台标（prepare_assets 的
  bottom_elements.suspected 会提示）；叠加文字默认避开底部 18% 区域，
  必须放底部时先加全宽暗色渐变压条压住底层，两层文字不得直接重叠。
- 预算硬顶：总轮数有限；渲染 ≤ 3 次；render→review→fix 超 3 轮即终止。
- review 的 errors 带字段路径与实测值，照单修复后重渲染；finish 前提不满足会被拒绝并回喂原因。
- 无法继续时用 fail(reason) 如实终止；以最近一次 review errors 为空为准，不宣称未验证的完成。
"""


_SPEECH_PUNCT_RE = re.compile(
    r"[\s，。！？、；：·…\u201c\u201d\u2018\u2019「」『』（）,.!?;:()\[\]{}\-—]")


def _norm_speech_text(s) -> str:
    """归一化口播文本：去标点/空白，仅留可比字符。"""
    return _SPEECH_PUNCT_RE.sub("", str(s or ""))


def _speech_text_matches(tl_text, src_text) -> bool:
    """单段字幕编排文本是否忠实于转录原文。

    合法形态：原文原样 / 仅标点差异 / 拆段（编排文本是原文子串）/ 高相似
    （SequenceMatcher ≥0.85，容纳少量纠错级差异）。
    """
    a, b = _norm_speech_text(tl_text), _norm_speech_text(src_text)
    if not a:
        return True  # 空文本由 validate_timeline 管辖
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _check_speech_texts(tl_speech, segs):
    """逐段比对 timeline.speech 与转录原文，返回不符清单。

    时间窗对齐：取与该段重叠最大的转录段；无重叠（纯空隙段）不校验文本。
    """
    mism = []
    for s in tl_speech:
        t0, t1 = float(s.get("start", 0)), float(s.get("end", 0))
        best, best_ov = None, 0.0
        for g in segs:
            ov = min(t1, float(g["end"])) - max(t0, float(g["start"]))
            if ov > best_ov:
                best_ov, best = ov, g
        if best is None or best_ov <= 0:
            continue
        if not _speech_text_matches(s.get("text", ""), best.get("text", "")):
            mism.append({
                "t": f"{t0:.1f}-{t1:.1f}",
                "got": _norm_speech_text(s.get("text", ""))[:18],
                "want": _norm_speech_text(best.get("text", ""))[:24],
            })
    return mism


# ── required_fx：视觉效果强度申报校验 ─────────────────────────────────────

_FX_TEAR_MIN_DX = 40.0     # 撕裂两侧位移下限（px）——低于此肉眼不可见
_FX_TEAR_MIN_ROT = 2.5     # 撕裂两侧旋转下限（度，绝对值）
_FX_TEAR_DUR = (0.3, 0.5)  # 撕裂时长窗口（s）：太短一闪而过，太长拖节奏
_FX_TEAR_N = (2, 4)        # 全片撕裂次数
_FX_TEAR_GAP = 5.0         # 相邻撕裂最小间隔（s）
_FX_SHAKE_AMP = (2.0, 4.0)  # 震动幅度窗口（px，brief 硬性要求）
_FX_SHAKE_DUR_MAX = 0.3    # 单次震动最长时长（s）
_FX_SHAKE_GAP = 7.5        # 相邻震动最小间隔（s，brief：每 8-10s 至多一次）
_FX_FX_SEP = 0.5           # 撕裂点与震动点最小错开（s）


def _num(v) -> float | None:
    """数值提取（bool 不算数值），非法返回 None。"""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _check_required_fx(tl) -> str | None:
    """required_fx 模式下校验 timeline.effects 视觉强度申报，不符返回错误说明。

    effects 契约：
      tears:   [{"t", "duration", "dx_px", "rot_deg"}, ...]（2–4 次）
      shakes:  [{"t", "duration", "amplitude_px"}, ...]（≥1 次）
    scene 必须按申报参数实际实现撕裂/震动（渲染抽帧会如实反映，肉眼可查）。
    """
    fx = tl.get("effects")
    if not isinstance(fx, dict):
        return ("timeline 缺少 effects 视觉效果申报（required_fx 硬性要求）。"
                "必须申报："
                'effects.tears=[{"t","duration","dx_px","rot_deg"},...] '
                f"（全片 {_FX_TEAR_N[0]}–{_FX_TEAR_N[1]} 次撕裂，两侧位移 dx_px ≥ "
                f"{_FX_TEAR_MIN_DX:g}px、|rot_deg| ≥ {_FX_TEAR_MIN_ROT:g}°、duration "
                f"{_FX_TEAR_DUR[0]:g}–{_FX_TEAR_DUR[1]:g}s、相邻间隔 ≥ "
                f"{_FX_TEAR_GAP:g}s）；"
                'effects.shakes=[{"t","duration","amplitude_px"},...] '
                f"（amplitude_px {_FX_SHAKE_AMP[0]:g}–{_FX_SHAKE_AMP[1]:g}px、"
                f"duration ≤ {_FX_SHAKE_DUR_MAX:g}s、相邻间隔 ≥ {_FX_SHAKE_GAP:g}s）。"
                "撕裂点与震动点错开 ≥ 0.5s；scene 必须按申报参数实际实现效果")
    tears = [e for e in (fx.get("tears") or []) if isinstance(e, dict)]
    shakes = [e for e in (fx.get("shakes") or []) if isinstance(e, dict)]
    errs = []
    if not (_FX_TEAR_N[0] <= len(tears) <= _FX_TEAR_N[1]):
        errs.append(f"tears 应申报 {_FX_TEAR_N[0]}–{_FX_TEAR_N[1]} 次（实际 {len(tears)}）")
    if not shakes:
        errs.append("shakes 应申报至少 1 次震动")
    tear_ts, shake_ts = [], []
    for i, e in enumerate(tears):
        dx, rot, d, t = (_num(e.get("dx_px")), _num(e.get("rot_deg")),
                         _num(e.get("duration")), _num(e.get("t")))
        if dx is None or dx < _FX_TEAR_MIN_DX:
            errs.append(f"tears[{i}].dx_px 应 ≥ {_FX_TEAR_MIN_DX:g}px（实际 {e.get('dx_px')}）")
        if rot is None or abs(rot) < _FX_TEAR_MIN_ROT:
            errs.append(f"tears[{i}].rot_deg 绝对值应 ≥ {_FX_TEAR_MIN_ROT:g}°（实际 {e.get('rot_deg')}）")
        if d is None or not (_FX_TEAR_DUR[0] <= d <= _FX_TEAR_DUR[1]):
            errs.append(f"tears[{i}].duration 应在 {_FX_TEAR_DUR[0]:g}–{_FX_TEAR_DUR[1]:g}s"
                        f"（实际 {e.get('duration')}）")
        if t is not None:
            tear_ts.append(t)
        else:
            errs.append(f"tears[{i}].t 缺失或非法")
    for a, b in zip(tear_ts, tear_ts[1:]):
        if b - a < _FX_TEAR_GAP:
            errs.append(f"相邻撕裂间隔应 ≥ {_FX_TEAR_GAP:g}s（{a:g}s→{b:g}s）")
    for i, e in enumerate(shakes):
        amp, d, t = (_num(e.get("amplitude_px")), _num(e.get("duration")),
                     _num(e.get("t")))
        if amp is None or not (_FX_SHAKE_AMP[0] <= amp <= _FX_SHAKE_AMP[1]):
            errs.append(f"shakes[{i}].amplitude_px 应在 {_FX_SHAKE_AMP[0]:g}–"
                        f"{_FX_SHAKE_AMP[1]:g}px（实际 {e.get('amplitude_px')}）")
        if d is None or not (0 < d <= _FX_SHAKE_DUR_MAX):
            errs.append(f"shakes[{i}].duration 应 >0 且 ≤ {_FX_SHAKE_DUR_MAX:g}s"
                        f"（实际 {e.get('duration')}）")
        if t is not None:
            shake_ts.append(t)
        else:
            errs.append(f"shakes[{i}].t 缺失或非法")
    for a, b in zip(shake_ts, shake_ts[1:]):
        if b - a < _FX_SHAKE_GAP:
            errs.append(f"相邻震动间隔应 ≥ {_FX_SHAKE_GAP:g}s（{a:g}s→{b:g}s）")
    for tt in tear_ts:
        for st in shake_ts:
            if abs(tt - st) < _FX_FX_SEP:
                errs.append(f"撕裂点 {tt:g}s 与震动点 {st:g}s 错开应 ≥ {_FX_FX_SEP:g}s")
    if errs:
        detail = "; ".join(errs[:6])
        return (f"effects 视觉效果申报未达标（{len(errs)} 项）：{detail}。"
                "请修正申报参数（并让 scene 实际按参数实现效果）后重新 save_timeline")
    return None


# 无参幂等工具：签名忽略 args（LLM 会塞幻觉参数抖动签名绕过重复拦截）
_SIG_PARAMLESS = frozenset({"transcribe", "start_preview"})


class StudioToolLoop:
    """闭包绑定 workspace 的轻量工具循环（构造注入 llm，skill 层负责 resolve）。"""

    def __init__(self, ws, llm, method_text, video_path, emit_progress,
                 max_turns=90, required_canvas=None, required_fx=False):
        self._ws = {k: Path(v) for k, v in ws.items()}
        self._llm = llm
        self._method_text = method_text or ""
        self._video_path = str(video_path)
        self._emit = emit_progress
        self._max_turns = int(max_turns)
        # brief 画幅硬约束（如 (1920,1080)）：save_timeline 校验 canvas 必须
        # 匹配。LLM 对复杂多约束 brief 的遵从度不稳定（实测违令出 720x1280），
        # 画幅属确定性验收项，平台侧硬校验而非靠提示。
        self._required_canvas = (
            (int(required_canvas[0]), int(required_canvas[1]))
            if required_canvas else None)
        # 视觉效果强度申报：save_timeline 校验 effects.tears/shakes 参数下限。
        # 实测 agent 写的撕裂 ±16px/1.1° 肉眼不可见——prompt 恳求无效，改用
        # 确定性校验强制申报（位移/旋转/时长/次数），scene 必须照参数实现。
        self._required_fx = bool(required_fx)
        # 循环状态
        self._turns = 0
        self._timeline: dict | None = None
        self._render_ok = False
        self._stale = False           # 成功 render 后又改了场景/时间轴 → 成片过期
        self._seq = 0                 # render/review 成功事件共用单调序号
        self._sig_counts = {}         # 同签名重复调用拦截状态（全局累计）
        self._render_seq = 0          # 最近一次成功 render 的序号
        self._review_seq: int | None = None  # 最近一次成功 review 的序号
        self._render_attempts = 0
        self._rework_rounds = 0
        self._last_review: dict | None = None
        self._speech_cache: dict | None = None
        self._preview_server = None
        self._preview_url: str | None = None

    # ── 主循环 ────────────────────────────────────────────────────────

    async def run(self) -> dict:
        """执行创作循环；返回 {"ok","video_path","review","turns","error"}。"""
        try:
            result = await self._loop()
        except Exception as exc:  # 兜底：循环崩溃也如实失败，不宣称完成
            logger.exception("studio tool loop crashed")
            result = self._fail_result(f"工具循环异常终止: {exc}")
        finally:
            self._stop_preview()
            self._cleanup_render_frames()
        if not result.get("ok"):
            self._safe_emit("fail", {"tool": "fail", "error": result.get("error"),
                                     "turns": result.get("turns")})
        return result

    async def _loop(self) -> dict:
        self._ensure_rules_installed()
        prompt = _SYSTEM_PROMPT
        if self._required_fx:
            # required_fx 附加契约：视觉效果必须先在 timeline 申报参数（平台
            # 硬校验），scene 再按申报参数实际实现——实测"恳求写夸张点"无效。
            prompt += (
                "\n# 视觉效果申报契约（required_fx）\n"
                "save_timeline 的 timeline 必须带 effects 字段（否则被拒）：\n"
                "  \"effects\": {\n"
                "    \"tears\":  [{\"t\":<s>, \"duration\":<s>, \"dx_px\":<px>, "
                "\"rot_deg\":<deg>}, ...],\n"
                "    \"shakes\": [{\"t\":<s>, \"duration\":<s>, "
                "\"amplitude_px\":<px>}, ...]\n"
                "  }\n"
                f"- tears：全片 {_FX_TEAR_N[0]}–{_FX_TEAR_N[1]} 次画面撕裂（用在"
                "夸张词/金句爆发点），两侧位移 dx_px ≥ "
                f"{_FX_TEAR_MIN_DX:g}px、旋转 |rot_deg| ≥ {_FX_TEAR_MIN_ROT:g}°"
                f"（两侧反向），duration {_FX_TEAR_DUR[0]:g}–{_FX_TEAR_DUR[1]:g}s"
                f"（位移/旋转在 duration 前半程快速展开并保持，勿 0.1s 内收拢），"
                f"相邻撕裂间隔 ≥ {_FX_TEAR_GAP:g}s；撕裂层用 clip-path 锯齿线把"
                "画面拆成两半错位，加发光裂缝线增强可读性。\n"
                f"- shakes：重点词被念出时的 0.2s 屏幕震动，amplitude_px "
                f"{_FX_SHAKE_AMP[0]:g}–{_FX_SHAKE_AMP[1]:g}px（垂直为主）、"
                f"duration ≤ {_FX_SHAKE_DUR_MAX:g}s、相邻间隔 ≥ {_FX_SHAKE_GAP:g}s。\n"
                f"- 撕裂点与震动点错开 ≥ {_FX_FX_SEP:g}s；字幕层必须独立于撕裂/"
                "震动元素保持稳定；scene 实际动效参数必须与申报一致（渲染帧可查）。"
            )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": self._intro_message()},
        ]
        while True:
            if self._turns >= self._max_turns:
                return self._fail_result(
                    f"轮数超限（max_turns={self._max_turns}），循环如实终止")
            try:
                raw = await self._llm.chat(messages, response_format={"type": "json_object"})
            except Exception as exc:
                return self._fail_result(f"LLM 调用失败: {exc}")
            reply = self._normalize_reply(raw)
            self._turns += 1
            action, args, err = self._parse_action(reply)
            assistant = {"role": "assistant", "content": reply}
            if err is not None:
                messages.append(assistant)
                messages.append({"role": "user",
                                 "content": self._feedback(action, False, {"error": err})})
                continue
            kind, payload = await self._execute(action, args)
            if kind == "stop":
                return payload
            ok, result = payload
            messages.append(assistant)
            # 同签名重复调用拦截（全局累计，非仅连续）：幂等工具结果不变，
            # 反复调用只会烧预算。实测两种病灶：同参数 transcribe 连续 12 次、
            # transcribe/read_rules A-B 交错循环 20 次——连续检测拦不住后者，
            # 必须按累计次数。第 2 次起回喂注入推进提示，累计第 4 次止损。
            # 签名归一化：无参幂等工具忽略 args——LLM 会塞幻觉参数抖动签名
            # （lang/model/foo 各不相同），让拦截失效，60 轮全烧在重复转录上。
            sig_args = {} if action in _SIG_PARAMLESS else args
            sig = json.dumps({"tool": action, "args": sig_args},
                             sort_keys=True, ensure_ascii=False)
            self._sig_counts[sig] = self._sig_counts.get(sig, 0) + 1
            sig_n = self._sig_counts[sig]
            feedback_content = self._feedback(action, ok, result)
            if sig_n >= 2 and ok:
                # 幂等重复调用（ok=True）：结果与首次完全一致，占位回喂，不再
                # 重复注入完整结果——实测同参数 transcribe 重复回喂词级时间戳
                # （单次数千 token）反复进上下文，是预算黑洞。失败（ok=False）
                # 不占位化：错误信息可能变化，且有诊断价值。
                result = {"repeated": True,
                          "note": "结果与第 1 次调用完全一致（已省略以省预算），请直接使用首次结果"}
                feedback_content = self._feedback(action, True, result)
            if sig_n >= 2:
                feedback_content += (
                    f"\n[系统提示] 你已用完全相同的参数第 {sig_n} 次调用同一工具，"
                    "结果不会再变。禁止再次重复调用；请立即推进到下一阶段"
                    "（plan → scene → render → review → deliver）。")
            if sig_n >= 4:
                sig_head = sig[:300]
                return self._fail_result(
                    f"同一工具同参数累计重复调用 {sig_n} 次，"
                    f"循环如实终止（疑似陷入重复，止损以省预算）。"
                    f"重复的调用签名：{sig_head}")
            messages.append({"role": "user", "content": feedback_content})

    # ── 协议解析与分发 ────────────────────────────────────────────────

    @staticmethod
    def _normalize_reply(raw) -> str:
        """LLM 返回归一化：str 原样；dict 信封取 content（真适配层如
        DeepSeekAdapter.chat 返回 {"content": str, "reasoning_content",
        "token_usage"}——Task 6 验收实证，协议层只关心正文）；其余类型兜底
        序列化，交给 _parse_action 按非法 JSON 计轮回喂，不崩循环。"""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict) and isinstance(raw.get("content"), str):
            return raw["content"]
        try:
            return json.dumps(raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(raw)

    def _parse_action(self, reply: str):
        """解析 LLM 回复 → (action, args, err)。任何违规都以 err 返回（计一轮）。"""
        try:
            obj = json.loads(reply)
        except (json.JSONDecodeError, ValueError):
            return None, None, ("输出不是合法 JSON。每轮必须只输出一个 JSON 对象："
                                '{"action": "...", "args": {...}}')
        if not isinstance(obj, dict):
            return None, None, f"输出 JSON 应为对象(dict)，实际为 {type(obj).__name__}"
        action = obj.get("action")
        if not isinstance(action, str) or not action.strip():
            return None, None, "缺少 action 字段（或不是非空字符串）"
        args = obj.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return action.strip(), None, f"args 应为对象(dict)，实际为 {type(args).__name__}"
        return action.strip(), args, None

    async def _execute(self, action: str, args: dict):
        """返回 ("stop", run_dict) 或 ("feedback", (ok, result))。"""
        if action == "finish":
            return self._finish(args)
        if action == "fail":
            return ("stop", self._fail_action(args))
        if action == "render" and (self._render_attempts >= _MAX_RENDER_ATTEMPTS
                                   or self._rework_rounds >= _MAX_REWORK_ROUNDS):
            return ("stop", self._budget_stop_result())
        handler = _TOOL_HANDLERS.get(action)
        if handler is None:
            known = "、".join(sorted(_TOOL_HANDLERS)) + "、finish、fail"
            return ("feedback", (False,
                                 {"error": f"未知 action：{action}（可用：{known}）"}))
        try:
            ok, result, extra = await handler(self, args)
        except Exception as exc:  # 工具实现兜底：任何异常都不崩循环
            logger.exception("tool %s crashed", action)
            ok, result, extra = False, {"error": f"工具 {action} 执行异常: {exc}"}, {}
        self._safe_emit(action, {"tool": action, "ok": ok, **extra})
        return ("feedback", (ok, result))

    # ── 终结动作 ──────────────────────────────────────────────────────

    def _finish(self, args):
        summary = args.get("summary")
        if summary is not None and not isinstance(summary, (str, dict)):
            return ("feedback", (False,
                                 {"error": f"summary 应为字符串或对象，实际为 {type(summary).__name__}"}))
        if not self._render_ok:
            return ("feedback", (False,
                                 {"error": "finish 被拒绝：尚未成功 render（先 write_scene_file → render）"}))
        if self._stale:
            return ("feedback", (False,
                                 {"error": "finish 被拒绝：最近一次成功 render 之后场景/时间轴又被修改"
                                           "（write_scene_file / save_timeline），out/final.mp4 已过期，"
                                           "需重新 render → review 后再 finish"}))
        if self._review_seq is None:
            return ("feedback", (False,
                                 {"error": "finish 被拒绝：尚未 review（render 后必须 review 且 errors 为空）"}))
        if self._review_seq <= self._render_seq:
            return ("feedback", (False,
                                 {"error": "finish 被拒绝：最近一次 review 发生在最近一次成功 render 之前，"
                                           "当前成片未经质检（需重新 review）"}))
        errs = self._last_review.get("errors") or []
        if errs:
            return ("feedback", (False,
                                 {"error": f"finish 被拒绝：最近 review 仍有 {len(errs)} 个错误，须修复后重试",
                                  "errors": errs[:5]}))
        video_path = self._ws["out"] / "final.mp4"
        extra = {"video_path": str(video_path)}
        if self._preview_url:
            extra["preview_url"] = self._preview_url
        self._safe_emit("deliver", {"tool": "finish", **extra})
        return ("stop", {"ok": True, "video_path": str(video_path),
                         "review": self._last_review, "turns": self._turns, "error": None})

    def _fail_action(self, args) -> dict:
        reason = args.get("reason")
        if reason is None:
            reason = "agent 未提供原因"
        elif not isinstance(reason, str):
            reason = json.dumps(reason, ensure_ascii=False)
        return {"ok": False, "video_path": None, "review": self._last_review,
                "turns": self._turns, "error": f"agent 主动终止：{reason}"}

    def _budget_stop_result(self) -> dict:
        err = "预算超限：渲染 ≤3 次、render→review→fix ≤3 轮已用尽，循环如实终止（不宣称完成）"
        errs = (self._last_review or {}).get("errors") or []
        if errs:
            err += "。最近 review 错误：" + "；".join(str(e) for e in errs[:8])
        return {"ok": False, "video_path": None, "review": self._last_review,
                "turns": self._turns, "error": err}

    def _fail_result(self, error: str) -> dict:
        return {"ok": False, "video_path": None, "review": self._last_review,
                "turns": self._turns, "error": error}

    # ── 回喂消息（截断至 ~4KB，保持 JSON 可解析） ─────────────────────

    def _feedback(self, action, ok: bool, result) -> str:
        payload = {"turn": self._turns, "action": action, "ok": ok, "result": result}
        text = json.dumps(payload, ensure_ascii=False)
        if self._fits(text):
            return text
        payload["result"] = self._slim_result(result)
        text = json.dumps(payload, ensure_ascii=False)
        if self._fits(text):
            return text
        # 兜底：按 UTF-8 字节预算二分出最大可容纳的原文前缀，保持 JSON 可解析
        total = len(text.encode("utf-8"))

        def build(n: int) -> str:
            return json.dumps({"turn": self._turns, "action": action, "ok": ok,
                               "result": {"truncated": True, "dropped_bytes": total,
                                          "preview": text[:n]}},
                              ensure_ascii=False)

        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._fits(build(mid)):
                lo = mid
            else:
                hi = mid - 1
        return build(lo)

    @staticmethod
    def _fits(text: str) -> bool:
        """回喂预算按 UTF-8 字节计（中文 3B/字，按字符数会放行 ~12KB）。"""
        return len(text.encode("utf-8")) <= _FEEDBACK_LIMIT

    def _slim_result(self, result):
        """超长结果瘦身：顶层长字符串截断、长列表截尾，保持结构可解析。"""
        if isinstance(result, str):
            return result[:2000] + "…(截断)"
        if isinstance(result, dict):
            slim = {}
            for k, v in result.items():
                if isinstance(v, str) and len(v) > 1200:
                    slim[k] = v[:1200] + "…(截断)"
                elif isinstance(v, list) and len(json.dumps(v, ensure_ascii=False)) > 2400:
                    keep = []
                    for item in v:
                        keep.append(item)
                        if len(json.dumps(keep, ensure_ascii=False)) > 2400:
                            break
                    slim[k] = keep + ["…(截断)"]
                else:
                    slim[k] = v
            return slim
        return str(result)[:2000]

    # ── 进度与预览 ────────────────────────────────────────────────────

    def _safe_emit(self, phase: str, payload: dict) -> None:
        try:
            if self._emit is not None:
                self._emit(phase, payload)
        except Exception:
            logger.warning("emit_progress failed for phase %s", phase, exc_info=True)

    def _stop_preview(self) -> None:
        if self._preview_server is not None:
            try:
                self._preview_server.stop()
            except Exception:
                logger.warning("preview server stop failed", exc_info=True)
            self._preview_server = None

    def _cleanup_render_frames(self) -> None:
        """run 结束删除渲染逐帧截图（.cache/render）：成片已合成，纯垃圾。

        抽帧（.cache/frames）保留——场景源码引用它，工作区可复渲；渲染帧
        每次数百至数千张 PNG，不删会把工作区磁盘撑爆。尽力而为不抛。
        """
        import shutil
        shutil.rmtree(Path(self._ws["frames"]), ignore_errors=True)

    def _ensure_rules_installed(self) -> None:
        """兜底：调用方未走 install_into_workspace 时，把 method_text 落为 rules/method.md。"""
        if not self._method_text.strip():
            return
        rules = self._ws["inputs"] / "rules"
        if rules.is_dir() and any(rules.iterdir()):
            return
        rules.mkdir(parents=True, exist_ok=True)
        (rules / "method.md").write_text(self._method_text, encoding="utf-8")

    def _intro_message(self) -> str:
        root = self._ws["scene"].resolve().parent
        return (
            f"工作区已就绪：{root}\n"
            f"原片：{self._video_path}\n"
            "打法文本在 inputs/rules/ 下（用 list_rules / read_rules_file 分阶段读取）。\n"
            "请开始：读打法 → transcribe → 按打法拆解内容 → save_timeline → "
            "write_scene_file 写场景（path 相对工作区根、必须位于 scene/ 下）→ render → "
            "review → 修复至 errors 为空 → finish(summary)。每轮只输出一个 JSON action。"
        )

    # ── 路径白名单 ────────────────────────────────────────────────────

    def _resolve_under(self, base: Path, rel):
        """白名单路径解析：返回 (resolved, None) 或 (None, 错误消息)。

        一律拒绝：反斜杠、协议前缀（file:// 等）、盘符、绝对路径、.. 逃逸；
        最终以 resolve() 后的包含关系兜底。
        """
        if not isinstance(rel, str) or not rel.strip():
            return None, "path 必须为非空字符串"
        if "\\" in rel:
            return None, f"路径被拒绝：{rel}（禁止反斜杠）"
        if "://" in rel.lower():
            return None, f"路径被拒绝：{rel}（禁止协议前缀）"
        if _DRIVE_RE.match(rel):
            return None, f"路径被拒绝：{rel}（禁止盘符/绝对路径）"
        p = PurePosixPath(rel)
        if p.is_absolute():
            return None, f"路径被拒绝：{rel}（禁止绝对路径）"
        if any(part == ".." for part in p.parts):
            return None, f"路径被拒绝：{rel}（禁止 .. 逃逸）"
        base_r = Path(base).resolve()
        resolved = (base_r / p).resolve()
        if resolved != base_r and base_r not in resolved.parents:
            return None, f"路径被拒绝：{rel}（越界：必须位于 {base_r} 下）"
        return resolved, None

    def _load_speech_cache(self):
        if self._speech_cache is not None:
            return self._speech_cache
        f = self._ws["analysis"] / "speech.json"
        if f.is_file():
            try:
                self._speech_cache = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            return self._speech_cache
        return None

    # ── 工具实现（返回 (ok, result, emit_extra)） ─────────────────────

    async def _t_list_rules(self, args):
        rules = self._ws["inputs"] / "rules"
        if not rules.is_dir():
            return False, {"error": f"rules 目录不存在: {rules}"}, {}
        files = sorted(p.relative_to(rules).as_posix()
                       for p in rules.rglob("*") if p.is_file())
        return True, {"files": files, "count": len(files)}, {"count": len(files)}

    async def _t_read_rules_file(self, args):
        rules = self._ws["inputs"] / "rules"
        resolved, err = self._resolve_under(rules, args.get("path"))
        if err:
            return False, {"error": err}, {}
        if not resolved.is_file():
            return False, {"error": f"文件不存在或不是文件: {args.get('path')}（位于 {rules}）"}, {}
        text = resolved.read_text(encoding="utf-8", errors="replace")
        return (True,
                {"path": args.get("path"), "bytes": len(text.encode("utf-8")), "content": text},
                {"path": args.get("path")})

    async def _t_transcribe(self, args):
        cached = self._load_speech_cache()
        if cached is not None:
            return True, self._slim_speech({**cached, "cached": True}), {"cached": True}
        try:
            segments = await asyncio.to_thread(transcribe, self._video_path)
            duration = await asyncio.to_thread(probe_duration, self._video_path)
        except Exception as exc:
            return False, {"error": f"transcribe 失败: {exc}"}, {}
        # 同音字纠错 pass（P2 验收回灌）：尽力而为，失败原样落盘 + warning，
        # 不阻塞 transcribe；corrections 随工具结果透出（agent 可见纠了什么）
        segments, corrections, fix_warnings = await fix_homophones(
            segments, self._llm, self._safe_emit)
        for w in fix_warnings:
            logger.warning("transcribe fix: %s", w)
        data = {
            "duration": duration,
            "cached": False,
            "corrections": corrections,
            "segments": [
                {"text": s.text, "start": s.start, "end": s.end,
                 "words": [{"text": w.text, "start": w.start, "end": w.end} for w in s.words]}
                for s in segments
            ],
        }
        out = self._ws["analysis"] / "speech.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._speech_cache = data
        return (True, self._slim_speech({**data, "speech_file": str(out)}),
                {"segments": len(data["segments"]), "duration": duration})

    @staticmethod
    def _slim_speech(data: dict) -> dict:
        """回喂瘦身：词级时间戳压缩为「词@秒」字符串流。

        词级 JSON 对象数组（每词 ~20 token）整份回喂是预算黑洞——48.5s 口播
        一次回喂数千 token，重复调用时上下文滚雪球。触发点定位只需 start
        （end≈下一词 start），完整版始终落盘 speech.json，scene 运行时 XHR
        同步读取（scene 端双保险实测可用）。
        """
        slim = {k: v for k, v in data.items() if k != "segments"}
        slim["segments"] = [
            {**seg, "words": " ".join(
                f"{w['text']}@{w['start']:.2f}" for w in seg.get("words", []))}
            for seg in data.get("segments", [])
        ]
        return slim

    async def _t_prepare_assets(self, args):
        kind = args.get("kind")
        if kind not in ("frames", "matting", "sfx_list"):
            return False, {"error": f"kind 非法：{kind!r}（只允许 frames / matting / sfx_list）"}, {}
        if kind == "frames":
            frames_src = self._ws[".cache"] / "frames"
            try:
                info = await asyncio.to_thread(
                    extract_frames, self._video_path, frames_src, _FRAME_FPS)
            except Exception as exc:
                return False, {"error": f"抽帧失败: {exc}"}, {}
            # 底部烧录元素探测（36.9s 实跑回灌：源片自带字幕与 karaoke 字幕打架）。
            # 尽力而为：probe 任何失败返回 error 字段不抛，不阻塞 prepare_assets。
            bottom = await asyncio.to_thread(probe_bottom_elements, str(info.frame_dir))
            if bottom.get("suspected"):
                self._safe_emit("prepare", {
                    "tool": "prepare_assets",
                    "message": "源素材底部疑似带烧录字幕/水印/台标：叠加文字请避开"
                               "底部 18% 区域，或用全宽暗色渐变压条压住底层文字。",
                })
            return (True,
                    {"kind": "frames", "count": info.count, "fps": info.fps,
                     "width": info.width, "height": info.height,
                     "frame_dir": str(info.frame_dir),
                     "pattern": info.pattern,
                     "example": "../.cache/frames/" + (info.pattern % 0),
                     "bottom_elements": bottom,
                     "hint": "scene 引用素材必须逐字使用此 pattern 与 ../.cache/frames/ 前缀"
                             "（.cache/ 是隐藏缓存目录：抽帧 jpg 与渲染器截图 png 都在"
                             "里面，禁止引用 .cache/render 下的 png），"
                             "禁止猜测其他命名；review 会对断链引用报 error"
                             "；若 bottom_elements.suspected=true，说明源素材底部"
                             "可能带烧录字幕/水印/台标，你的叠加文字必须避开底部"
                             " 18% 区域，或采用全宽暗色渐变压条确保可读性压住底层文字"},
                    {"count": info.count})
        if kind == "matting":
            try:
                res = await matte_person(self._video_path, self._ws["assets"])
            except Exception as exc:
                res = None
                logger.warning("matte_person raised: %s", exc)
            if res is None:
                return False, {"error": "人物抠像失败（RVM 降级），assets/ 无透明帧轨"}, {}
            return (True,
                    {"kind": "matting", "count": res.count, "fps": res.fps,
                     "width": res.width, "height": res.height,
                     "frame_dir": str(res.frame_dir),
                     "pattern": res.pattern,
                     "example": "../assets/frames/" + (res.pattern % 0),
                     "hint": "scene 引用素材必须逐字使用此 pattern 与 "
                             "../assets/frames/ 前缀，禁止猜测其他命名；"
                             "review 会对断链引用报 error"},
                    {"count": res.count})
        # sfx_list：只枚举 SFX 库文件名，不做任何风格筛选
        if not _SFX_DIR.is_dir():
            return True, {"kind": "sfx_list", "files": [], "count": 0,
                          "note": "SFX 库目录不存在"}, {"count": 0}
        files = sorted(p.name for p in _SFX_DIR.iterdir() if p.is_file())
        names = [Path(f).stem for f in files]
        return (True, {"kind": "sfx_list", "files": files, "names": names,
                       "count": len(files)}, {"count": len(files)})

    async def _t_generate_image(self, args):
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return False, {"error": "prompt 应为非空字符串"}, {}
        name = args.get("name")
        if not isinstance(name, str) or not _GEN_NAME_RE.fullmatch(name):
            return (False,
                    {"error": f"name 非法：{name!r}（小写字母开头，仅 [a-z0-9_-]，"
                              "≤48 字符；如 avocado-cut）"}, {})
        ratio = args.get("ratio", "1:1")
        size = _IMAGE_RATIO_SIZE.get(str(ratio))
        if size is None:
            return False, {"error": f"ratio 非法：{ratio!r}（只允许 1:1 / 16:9 / 9:16）"}, {}
        try:
            adapter = _get_image_adapter()
            images = await adapter.generate(prompt=prompt.strip(), size=size, count=1)
        except Exception as exc:
            return False, {"error": f"生图失败: {exc}"}, {}
        if not images or not images[0]:
            return False, {"error": "生图适配器返回空图片"}, {}
        gen_dir = self._ws["assets"] / "gen"
        gen_dir.mkdir(parents=True, exist_ok=True)
        out = gen_dir / f"{name}.png"
        out.write_bytes(images[0])
        return (True,
                {"path": str(out), "bytes": len(images[0]), "size": size,
                 "pattern": "assets/gen/<name>.png",
                 "example": f"../assets/gen/{name}.png",
                 "hint": "scene 引用必须逐字使用 example 的相对路径"
                         "（相对 scene/index.html）；同一 name 重复生成会覆盖旧图；"
                         "review 会对断链引用报 error"},
                {"path": str(out)})

    async def _t_save_timeline(self, args):
        tl = args.get("timeline")
        if not isinstance(tl, dict):
            return False, {"error": f"timeline 应为对象(dict)，实际为 {type(tl).__name__}"}, {}
        errs = validate_timeline(tl)
        if errs:
            return False, {"error": "timeline 校验未通过", "errors": errs}, {}
        if self._required_canvas:
            c = tl.get("canvas") or {}
            got = (int(c.get("w", 0)), int(c.get("h", 0)))
            if got != self._required_canvas:
                return False, {"error": (
                    f"canvas 必须为 {self._required_canvas[0]}x"
                    f"{self._required_canvas[1]}（brief 硬性画幅要求），"
                    f"实际 {got[0]}x{got[1]}。请按此画幅重新设计全部构图后再 "
                    "save_timeline")}, {}
        # 视觉效果强度申报（required_fx）：与画幅同理，确定性验收项平台侧硬
        # 校验——prompt 恳求"撕裂要夸张"实测无效（±16px 肉眼不可见）。
        if self._required_fx:
            fx_err = _check_required_fx(tl)
            if fx_err:
                return False, {"error": fx_err}, {}
        # 转录覆盖完整性：timeline.speech 不允许把后半段口播整段丢弃
        # （实测 48.5s 片只排了前 14.4s 字幕，后 34s 成了无声填充）。无语音
        # 段不算缺失——校验只要求"有语音的地方必须有字幕编排"。
        if self._speech_cache and self._speech_cache.get("segments"):
            segs = self._speech_cache["segments"]
            last_src = float(segs[-1]["end"])
            tl_speech = tl.get("speech") or []
            last_tl = max((float(s.get("end", 0)) for s in tl_speech), default=0.0)
            if last_tl < last_src * 0.95:
                missing = [s for s in segs
                           if float(s["end"]) > last_tl + 0.5]
                heads = "; ".join(
                    f"[{float(s['start']):.1f}-{float(s['end']):.1f}]"
                    f"{str(s.get('text', ''))[:14]}" for s in missing[:4])
                return False, {"error": (
                    f"timeline.speech 未覆盖全部转录段：最后编排到 "
                    f"{last_tl:.1f}s，但转录到 {last_src:.1f}s，缺失 "
                    f"{len(missing)} 段：{heads}。每一段口播都必须有字幕与"
                    "词级编排（可合并但不能丢弃）；真正无语音的片段除外")}, {}
            # 台词忠实性：timeline.speech 文本必须对得上转录原文——时间覆盖
            # 校验只管"何时说"，拦不住"编造台词"（实测 agent 前 6 段照抄、
            # 后半段自由创作填满时间轴，成片字幕与口播完全对不上）。
            mism = _check_speech_texts(tl.get("speech") or [], segs)
            if mism:
                detail = "; ".join(
                    f"[{m['t']}]编排「{m['got']}」≠原文「{m['want']}」"
                    for m in mism[:4])
                return False, {"error": (
                    f"timeline.speech 有 {len(mism)} 段与转录原文不符（台词"
                    "必须是转录原文的忠实编排，禁止改写/创作）："
                    f"{detail}。请逐字使用 speech.json segments 的原文"
                    "（只允许拆段/合并/增删标点）后重新 save_timeline")}, {}
        p = save_timeline(self._ws, tl)
        self._timeline = tl
        self._stale = True  # 时间轴已变：此前若有成片即告过期，须重渲重检
        return (True,
                {"path": str(p), "beats": len(tl.get("beats") or []),
                 "duration": tl.get("duration"), "fps": tl.get("fps")},
                {"beats": len(tl.get("beats") or [])})

    async def _t_write_scene_file(self, args):
        scene = self._ws["scene"]
        resolved, err = self._resolve_under(scene.parent, args.get("path"))
        if err:
            return False, {"error": err}, {}
        scene_r = scene.resolve()
        if resolved == scene_r or scene_r not in resolved.parents:
            return False, {"error": f"路径被拒绝：{args.get('path')}"
                                    f"（write 仅限 scene/ 下，如 scene/index.html）"}, {}
        content = args.get("content")
        if not isinstance(content, str):
            return False, {"error": f"content 应为字符串，实际为 {type(content).__name__}"}, {}
        if resolved.is_dir():
            return False, {"error": f"写入失败：{resolved} 是目录"}, {}
        resolved.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        resolved.write_bytes(data)
        self._stale = True  # 场景已变：此前若有成片即告过期，须重渲重检
        return (True, {"path": args.get("path"), "bytes": len(data)},
                {"path": args.get("path"), "bytes": len(data)})

    async def _t_mix_sfx(self, args):
        """定点音效混音：口播轨 + SFX 事件（[{file,t,gain?},...]）→ 混合音轨。

        file 必须逐字来自 prepare_assets("sfx_list") 返回的文件名（防幻觉与
        路径逃逸）；t 已加载 timeline 时以 timeline.duration 为上界；gain ∈ (0,1]。
        """
        events = args.get("events")
        if not isinstance(events, list) or not events:
            return False, {"error": "events 应为非空列表，如 "
                                    '[{"file":"impact.wav","t":2.44,"gain":0.8}]'}, {}
        if not _SFX_DIR.is_dir():
            return False, {"error": f"SFX 库目录不存在：{_SFX_DIR}"}, {}
        names = {p.name for p in _SFX_DIR.iterdir() if p.is_file()}
        t_max = (float(self._timeline["duration"])
                 if self._timeline else _MIX_T_MAX)
        norm = []
        for ev in events:
            if not isinstance(ev, dict):
                return False, {"error": f"events 项应为字典，实际 {ev!r}"}, {}
            f = ev.get("file")
            t = ev.get("t")
            g = ev.get("gain", _MIX_GAIN_DEFAULT)
            if not isinstance(f, str) or f not in names:
                return False, {"error": f"file 必须是 SFX 库中的文件名"
                                        f"（prepare_assets('sfx_list') 获取），实际 {f!r}"}, {}
            if not isinstance(t, (int, float)) or isinstance(t, bool) \
                    or not (0 <= t <= t_max):
                return False, {"error": f"t 应为 0 ≤ t ≤ {t_max}s 的数值，实际 {t!r}"}, {}
            if not isinstance(g, (int, float)) or isinstance(g, bool) \
                    or not (0 < g <= 1):
                return False, {"error": f"gain 应为 (0,1] 数值（默认 {_MIX_GAIN_DEFAULT}），"
                                        f"实际 {g!r}"}, {}
            norm.append({"file": f, "t": float(t), "gain": float(g)})
        out_path = Path(self._ws["out"]) / "audio_mixed.m4a"
        try:
            await asyncio.to_thread(_mix_sfx_sync, str(self._video_path), norm, out_path)
        except Exception as exc:
            return False, {"error": f"混音失败: {exc}"}, {}
        self._seq += 1
        return (True, {"path": str(out_path), "events": len(norm),
                       "note": 'render 时传 {"audio":"mixed"} 使用此音轨'},
                {"events": len(norm)})

    async def _t_render(self, args):
        audio = args.get("audio", "original")
        audio_path = str(self._video_path)
        if audio == "mixed":
            mixed = Path(self._ws["out"]) / "audio_mixed.m4a"
            if not mixed.is_file():
                return False, {"error": 'audio="mixed" 但混音轨不存在'
                                        "（先调 mix_sfx 生成 audio_mixed.m4a）"}, {}
            audio_path = str(mixed)
        elif audio != "original":
            return False, {"error": f'audio 仅支持 "original" / "mixed"，'
                                    f"实际为 {audio!r}"}, {}
        if self._timeline is None:
            return False, {"error": "尚未 save_timeline（render 的 duration/fps/canvas 取自 timeline）"}, {}
        scene_html = self._ws["scene"] / "index.html"
        if not scene_html.is_file():
            return False, {"error": f"场景未写入：{scene_html} 不存在（先 write_scene_file）"}, {}
        tl = self._timeline
        if self._last_review is not None and self._last_review.get("errors"):
            self._rework_rounds += 1
        self._render_attempts += 1
        out_video = self._ws["out"] / "final.mp4"
        try:
            res = await asyncio.to_thread(
                render_scene, scene_html, self._ws["frames"],
                float(tl["duration"]), int(tl["fps"]),
                int(tl["canvas"]["w"]), int(tl["canvas"]["h"]),
                audio_path, out_video,
            )
        except Exception as exc:
            return (False,
                    {"error": f"render 失败（第 {self._render_attempts}/{_MAX_RENDER_ATTEMPTS} 次尝试）: {exc}"},
                    {"attempt": self._render_attempts})
        self._render_ok = True
        self._seq += 1
        self._render_seq = self._seq
        self._stale = False  # 新成片落盘：覆盖场景/时间轴的过期标记
        return (True,
                {"video_path": str(out_video), "frames": res.count, "fps": res.fps,
                 "elapsed_s": round(res.elapsed, 2)},
                {"attempt": self._render_attempts})

    async def _t_review(self, args):
        if self._timeline is None:
            return False, {"error": "尚未 save_timeline，review 需要 timeline"}, {}
        out_video = self._ws["out"] / "final.mp4"
        if not out_video.is_file():
            return False, {"error": "尚未成功 render（out/final.mp4 不存在），无可质检成片"}, {}
        res = await asyncio.to_thread(review_workspace, self._ws, self._timeline, str(out_video))
        self._last_review = res
        self._seq += 1
        self._review_seq = self._seq  # 供 finish 校验「review 晚于最近成功 render」
        errs = res.get("errors") or []
        return (True,
                {"errors": errs, "loudness": res.get("loudness") or {},
                 "contact_sheets": res.get("contact_sheets") or []},
                {"error_count": len(errs)})

    async def _t_start_preview(self, args):
        if self._preview_url:
            return (True, {"preview_url": self._preview_url, "already_running": True},
                    {"preview_url": self._preview_url})
        try:
            server = StudioPreviewServer(self._ws)
            url = await asyncio.to_thread(server.start)
        except Exception as exc:
            return False, {"error": f"预览服务启动失败: {exc}"}, {}
        self._preview_server = server
        self._preview_url = url
        return True, {"preview_url": url}, {"preview_url": url}


_TOOL_HANDLERS = {
    "list_rules": StudioToolLoop._t_list_rules,
    "read_rules_file": StudioToolLoop._t_read_rules_file,
    "transcribe": StudioToolLoop._t_transcribe,
    "prepare_assets": StudioToolLoop._t_prepare_assets,
    "generate_image": StudioToolLoop._t_generate_image,
    "mix_sfx": StudioToolLoop._t_mix_sfx,
    "save_timeline": StudioToolLoop._t_save_timeline,
    "write_scene_file": StudioToolLoop._t_write_scene_file,
    "render": StudioToolLoop._t_render,
    "review": StudioToolLoop._t_review,
    "start_preview": StudioToolLoop._t_start_preview,
}
