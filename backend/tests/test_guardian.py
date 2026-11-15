"""Tests for Guardian — tool-call safety gate.

Covers:
1. Default policies: auto-allow tools (trending_search, xhs_search, etc.)
2. High-risk tools requiring confirmation (file_write, bash_exec, bash)
3. Strict mode: unknown tools are denied
4. Non-strict mode: unknown tools require confirmation (safety default)
5. Argument validation: too-long arguments, denied patterns, injection patterns
6. Permission gate: skill with required_permissions denied
7. Audit log: every decision is recorded
8. to_observation() serialization
"""

import pytest

from app.engine.governance.guardian import (
    Guardian,
    GuardianAction,
    GuardianDecision,
    GuardianPolicy,
    _DEFAULT_POLICIES,
    _is_bash_media_safe,
)


class TestGuardianDecision:
    def test_to_observation_basic(self):
        d = GuardianDecision(action=GuardianAction.DENY, reason="test reason", risk_level="high")
        obs = d.to_observation()
        assert '"error": "guardian_denied"' in obs
        assert '"reason": "test reason"' in obs
        assert '"risk_level": "high"' in obs

    def test_to_observation_with_hint(self):
        d = GuardianDecision(
            action=GuardianAction.DENY,
            reason="permission denied",
            risk_level="medium",
            hint="内部详细指引",
            user_hint="请向用户求助开启权限",
        )
        obs = d.to_observation()
        assert '"hint": "请向用户求助开启权限"' in obs

    def test_to_observation_hint_fallback_when_no_user_hint(self):
        d = GuardianDecision(
            action=GuardianAction.DENY,
            reason="permission denied",
            risk_level="medium",
            hint="回退到 hint",
        )
        obs = d.to_observation()
        assert '"hint": "回退到 hint"' in obs

    def test_to_observation_no_hint(self):
        d = GuardianDecision(action=GuardianAction.ALLOW, reason="ok", risk_level="low")
        obs = d.to_observation()
        assert "hint" not in obs


class TestDefaultPolicies:
    def test_auto_allow_tools_exist(self):
        """2026-10 纯净化：策略表白名单 = 内核协议工具 + collab 协作工具。

        skill_call 已从白名单移出（见 guardian.py：LLM 决定调什么技能，
        Guardian 决定能不能执行，创作类技能必须经用户确认）。
        """
        auto_allow_tools = [
            "ask_user_question", "file_read", "glob", "grep",
            "llm_generate", "viral_score", "content_check",
            "spawn_agent", "wait_agent", "list_agents", "send_message", "interrupt_agent",
        ]
        for tool in auto_allow_tools:
            assert tool in _DEFAULT_POLICIES, tool
            assert _DEFAULT_POLICIES[tool].auto_allow is True

    def test_high_risk_tools_need_confirmation(self):
        confirm_tools = ["file_write", "bash_exec", "bash", "skill_call"]
        for tool in confirm_tools:
            assert tool in _DEFAULT_POLICIES
            assert _DEFAULT_POLICIES[tool].needs_confirmation is True

    def test_file_write_denies_path_traversal(self):
        policy = _DEFAULT_POLICIES["file_write"]
        assert r"\.\./" in policy.denied_patterns

    def test_bash_denies_dangerous_commands(self):
        for tool in ["bash_exec", "bash"]:
            policy = _DEFAULT_POLICIES[tool]
            assert any("rm" in p for p in policy.denied_patterns)
            assert any("sudo" in p for p in policy.denied_patterns)


@pytest.fixture
def guardian():
    return Guardian()


@pytest.fixture
def strict_guardian():
    return Guardian(strict_mode=True)


class TestGuardianAutoAllow:
    @pytest.mark.asyncio
    async def test_send_message_auto_allowed(self, guardian):
        decision = await guardian.check("send_message", {"agent_id": "a1", "content": "hi"})
        assert decision.action == GuardianAction.ALLOW
        assert decision.risk_level == "low"

    @pytest.mark.asyncio
    async def test_wait_agent_auto_allowed(self, guardian):
        """业务名条目已删，改测 collab 协议条目。"""
        decision = await guardian.check("wait_agent", {})
        assert decision.action == GuardianAction.ALLOW

    @pytest.mark.asyncio
    async def test_llm_generate_auto_allowed(self, guardian):
        """业务名条目已删，改测同 risk_level 的协议条目。"""
        decision = await guardian.check("llm_generate", {"prompt": "写文案"})
        assert decision.action == GuardianAction.ALLOW
        assert decision.risk_level == "low"

    @pytest.mark.asyncio
    async def test_skill_call_needs_guardian_confirmation(self, guardian):
        """2026-10 重构：skill_call 不再在 Guardian 层自动放行。

        Guardian 只做"能不能执行"的判断；"要不要打扰用户"由 ReactorSession
        的 auto_approve 决定（低/中风险与只读技能在会话层自动放行）。
        """
        decision = await guardian.check("skill_call", {"skill": "x"})
        assert decision.action == GuardianAction.NEEDS_CONFIRM
        assert decision.risk_level == "medium"

    async def test_removed_business_name_falls_back_to_confirm(self, guardian):
        decision = await guardian.check("image_gen", {"prompt": "清新自然风格"})
        assert decision.action == GuardianAction.NEEDS_CONFIRM


class TestGuardianConfirmation:
    @pytest.mark.asyncio
    async def test_file_write_needs_confirmation(self, guardian):
        decision = await guardian.check("file_write", {"path": "/tmp/test.txt", "content": "hello"})
        assert decision.action == GuardianAction.NEEDS_CONFIRM
        assert decision.risk_level == "high"

    @pytest.mark.asyncio
    async def test_bash_readonly_auto_allowed(self, guardian):
        # 2026-10-22：只读探查命令走白名单自动放行（此前 ls -la 也弹
        # 高危确认，LLM 探环境即卡死，图文任务 13 步被一条 ls 摁停）
        decision = await guardian.check("bash", {"command": "ls -la"})
        assert decision.action == GuardianAction.ALLOW

    @pytest.mark.asyncio
    async def test_bash_non_readonly_needs_confirmation(self, guardian):
        # 非白名单命令（有写副作用）维持高危确认
        decision = await guardian.check("bash", {"command": "npm install foo"})
        assert decision.action == GuardianAction.NEEDS_CONFIRM

    @pytest.mark.asyncio
    async def test_bash_exec_readonly_auto_allowed(self, guardian):
        decision = await guardian.check("bash_exec", {"command": "echo hello"})
        assert decision.action == GuardianAction.ALLOW


class TestGuardianStrictMode:
    @pytest.mark.asyncio
    async def test_unknown_tool_denied_in_strict_mode(self, strict_guardian):
        decision = await strict_guardian.check("unknown_tool", {})
        assert decision.action == GuardianAction.DENY
        assert "strict mode" in decision.reason

    @pytest.mark.asyncio
    async def test_unknown_tool_needs_confirm_in_non_strict_mode(self, guardian):
        decision = await guardian.check("unknown_tool", {})
        assert decision.action == GuardianAction.NEEDS_CONFIRM
        assert "no policy" in decision.reason


class TestGuardianArgumentValidation:
    @pytest.mark.asyncio
    async def test_too_long_arguments_denied(self, guardian):
        long_args = {"data": "x" * 20000}
        decision = await guardian.check("file_write", long_args)
        assert decision.action == GuardianAction.DENY
        assert "too long" in decision.reason

    @pytest.mark.asyncio
    async def test_path_traversal_denied(self, guardian):
        decision = await guardian.check("file_write", {"path": "../../etc/passwd", "content": "hack"})
        assert decision.action == GuardianAction.DENY
        assert "denied pattern" in decision.reason

    @pytest.mark.asyncio
    async def test_sudo_injection_denied(self, guardian):
        decision = await guardian.check("bash", {"command": "sudo rm -rf /"})
        assert decision.action == GuardianAction.DENY

    @pytest.mark.asyncio
    async def test_command_substitution_injection_denied(self, guardian):
        decision = await guardian.check("bash", {"command": "echo $(cat /etc/passwd)"})
        assert decision.action == GuardianAction.DENY
        assert "injection" in decision.reason

    @pytest.mark.asyncio
    async def test_backtick_injection_denied(self, guardian):
        decision = await guardian.check("bash", {"command": "echo `whoami`"})
        assert decision.action == GuardianAction.DENY

    @pytest.mark.asyncio
    async def test_eval_injection_denied(self, guardian):
        decision = await guardian.check("bash", {"command": "python -c 'eval(input())'"})
        assert decision.action == GuardianAction.DENY


class TestGuardianAuditLog:
    @pytest.mark.asyncio
    async def test_decisions_are_recorded(self, guardian):
        # allow 路径走协议条目（skill_call）+ bash 只读白名单；
        # confirm 路径走非白名单 bash（写副作用仍需确认）
        await guardian.check("skill_call", {"skill": "x"})
        await guardian.check("bash", {"command": "ls"})
        await guardian.check("bash", {"command": "docker rm -f x"})
        assert guardian._allowed_count >= 1
        assert guardian._confirmed_count >= 1
        assert len(guardian._audit_log) >= 2

    @pytest.mark.asyncio
    async def test_denied_count_increments(self, guardian):
        await guardian.check("bash", {"command": "sudo rm -rf /"})
        assert guardian._denied_count >= 1


class TestGuardianPolicyCustom:
    @pytest.mark.asyncio
    async def test_custom_policy_overrides_default(self):
        custom_policies = {
            "trending_search": GuardianPolicy(
                tool_name="trending_search",
                auto_allow=False,
                needs_confirmation=True,
                risk_level="high",
            ),
        }
        g = Guardian(policies=custom_policies)
        decision = await g.check("trending_search", {"keyword": "test"})
        assert decision.action == GuardianAction.NEEDS_CONFIRM


class TestGuardianMediaWhitelist:
    """2026-11-02：媒体处理白名单——ffprobe 任意放行，ffmpeg 输出限 workspace。

    背景：视频任务每条 ffmpeg 都弹高危确认，自动执行场景无人确认即
    GUARDIAN_PAUSED（干一半就停的主凶）。执行层白名单含 ffmpeg 但
    确认层先杀，本组用例锁定确认层新行为。
    """

    @pytest.mark.asyncio
    async def test_ffprobe_always_allowed(self, guardian):
        decision = await guardian.check(
            "bash",
            {"command": "ffprobe -hide_banner -v error -show_entries format=duration -of default=nw=1 D:/anywhere/video.mp4"},
        )
        assert decision.action == GuardianAction.ALLOW

    @pytest.mark.asyncio
    async def test_ffmpeg_relative_output_allowed(self, guardian):
        decision = await guardian.check(
            "bash",
            {"command": "ffmpeg -hide_banner -y -i input.mp4 -vf select='not(between(t,1,2))' output.mp4"},
        )
        assert decision.action == GuardianAction.ALLOW

    @pytest.mark.asyncio
    async def test_ffmpeg_absolute_output_inside_workspace_allowed(self, guardian, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        out = tmp_path / "out.mp4"
        decision = await guardian.check(
            "bash",
            {"command": f"ffmpeg -y -i input.mp4 {out}"},
        )
        assert decision.action == GuardianAction.ALLOW

    @pytest.mark.asyncio
    async def test_ffmpeg_absolute_output_outside_workspace_needs_confirm(self, guardian):
        decision = await guardian.check(
            "bash",
            {"command": "ffmpeg -y -i input.mp4 D:/somewhere_else/out.mp4"},
        )
        assert decision.action == GuardianAction.NEEDS_CONFIRM
        assert decision.risk_level == "high"

    @pytest.mark.asyncio
    async def test_ffmpeg_windows_dir_output_needs_confirm(self, guardian):
        decision = await guardian.check(
            "bash",
            {"command": "ffmpeg -y -i input.mp4 C:/Windows/out.mp4"},
        )
        assert decision.action == GuardianAction.NEEDS_CONFIRM

    @pytest.mark.asyncio
    async def test_ffmpeg_without_output_token_needs_confirm(self, guardian):
        # 无明确输出 token 时，启发式回退到最后一个非选项 token（这里是
        # workspace 外的输入路径）——按保守处理，维持确认
        decision = await guardian.check(
            "bash", {"command": "ffmpeg -y -i D:/elsewhere/in.mp4 -movflags +faststart"}
        )
        assert decision.action == GuardianAction.NEEDS_CONFIRM

    @pytest.mark.asyncio
    async def test_injection_in_ffmpeg_still_denied(self, guardian):
        # 白名单放行不豁免注入校验（Stage 2 先于放行判断）
        decision = await guardian.check(
            "bash",
            {"command": "ffmpeg -y -i input.mp4 out.mp4 && eval(os.system('dir'))"},
        )
        assert decision.action == GuardianAction.DENY

    def test_media_safe_unit_paths(self):
        assert _is_bash_media_safe("ffprobe -v error -show_streams a.mp4") is True
        assert _is_bash_media_safe("ffmpeg -y -i a.mp4 out.mp4") is True
        assert _is_bash_media_safe("ffmpeg") is False  # 无参数
        assert _is_bash_media_safe("cp a.mp4 b.mp4") is False  # 非媒体命令不归本白名单管


class TestConfirmationPromptUX:
    """2026-11-04：确认弹窗是给用户看的，不是给开发者看的。

    旧版 "Tool 'skill_call' is about to execute with arguments: {...}" 把
    原始 dict str 截 200 字符甩给用户，内部字段 _user_id 漏进 UI 还被拦腰
    截断，用户看不出这步是干嘛的。新文案：中文 + 技能元数据 + 过滤内部字段。
    """

    @pytest.mark.asyncio
    async def test_skill_call_prompt_chinese_no_internal_fields(self, guardian):
        # 2026-11-04 大哥实测案例：video_transcribe 弹确认，_user_id 被截断
        args = {
            "skill": "video_transcribe",
            "arguments": {"video_path": "D:/proj/uploads/videos/abc.mp4"},
            "_user_id": "OwdN5Jwjo_9XA_iAvuht-Q",
        }
        decision = await guardian.check("skill_call", args)
        assert decision.action == GuardianAction.NEEDS_CONFIRM
        prompt = decision.confirmation_prompt
        assert "is about to execute" not in prompt  # 旧英文文案绝迹
        assert "即将执行技能" in prompt
        assert "video_transcribe" in prompt
        assert "D:/proj/uploads/videos/abc.mp4" in prompt  # 业务参数保留
        assert "_user_id" not in prompt  # 内部字段不外漏
        assert "OwdN5Jwjo" not in prompt
        assert "中等风险" in prompt

    @pytest.mark.asyncio
    async def test_skill_call_prompt_uses_skill_metadata(self, guardian, monkeypatch):
        import app.engine.governance.guardian as gmod

        monkeypatch.setattr(
            gmod,
            "_lookup_skill_metadata",
            lambda name: ("视频转文字", "把本地视频/音频转成文字（本地推理，不调云端）"),
        )
        decision = await guardian.check(
            "skill_call",
            {"skill": "video_transcribe", "arguments": {"video_path": "a.mp4"}},
        )
        prompt = decision.confirmation_prompt
        assert "【视频转文字】" in prompt
        assert "说明：把本地视频/音频转成文字" in prompt
        assert "不调云端" in prompt

    @pytest.mark.asyncio
    async def test_skill_call_prompt_degrades_without_registry(self, guardian):
        # 注册表查不到（技能名乱写）→ 静默降级，不炸不糊弄
        decision = await guardian.check(
            "skill_call",
            {"skill": "no_such_skill_xyz", "arguments": {"k": "v"}},
        )
        prompt = decision.confirmation_prompt
        assert "no_such_skill_xyz" in prompt
        assert "即将执行技能" in prompt

    @pytest.mark.asyncio
    async def test_generic_tool_prompt_chinese(self, guardian):
        decision = await guardian.check(
            "file_write",
            {"path": "/tmp/a.txt", "content": "hello", "_user_id": "u123"},
        )
        prompt = decision.confirmation_prompt
        assert "is about to execute" not in prompt
        assert "file_write" in prompt
        assert "_user_id" not in prompt
        assert "高风险" in prompt

    @pytest.mark.asyncio
    async def test_long_args_truncated_with_ellipsis(self, guardian):
        decision = await guardian.check(
            "file_write",
            {"path": "/tmp/a.txt", "content": "x" * 2000, "_user_id": "u1"},
        )
        assert len(decision.confirmation_prompt) < 500
        assert "…" in decision.confirmation_prompt