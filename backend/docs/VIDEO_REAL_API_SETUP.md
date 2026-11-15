# 真实视频 API 配置指南

## 🎯 快速启用真实 API

### 1. 修改配置文件

在 `.env` 或环境变量中设置：

```bash
# ===== 必填：切换到真实模式 =====
VIDEO_PROVIDER_MODE=real

# ===== 选择 Provider（三选一）=====

# 选项 A：阿里云万相（推荐，与图片生成同生态）
VIDEO_PROVIDER=dashscope
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx  # 你的 DashScope API Key
DASHSCOPE_VIDEO_MODEL=wanx2.1-t2v-turbo  # 可选：默认 wanx2.1-t2v-turbo

# 选项 B：可灵 AI
VIDEO_PROVIDER=kling
KLING_ACCESS_KEY=your_access_key
KLING_SECRET_KEY=your_secret_key
KLING_MODEL=kling-v1  # 可选

# 选项 C：火山方舟
VIDEO_PROVIDER=ark
ARK_API_KEY=your_api_key
ARK_MODEL=your_endpoint_id  # 必填
```

### 2. 重启后端服务

```bash
# 停止当前服务
Ctrl+C

# 重新启动
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 验证配置成功

启动日志中应该看到：

```
[media_provider] 使用真实 Provider: dashscope
[lifespan] step 6.5: video worker started (handlers=shot_image,shot_video,compose, SSE 已接入)
```

如果 API Key 未配置，会自动回落 mock 并打印警告：

```
[media_provider] 真实 Provider (dashscope) API Key 未配置，已回落 mock
```

---

## 📋 Provider 详细配置

### 🔵 阿里云万相（DashScope）- 推荐

**优势**：
- 与图片生成（WanxAdapter）同生态，复用 API Key
- 支持 text2video + image2video
- 性价比高，模型成熟

**支持模型**：
| 模型 | 说明 | 推荐场景 |
|------|------|---------|
| `wanx2.1-t2v-turbo` | 文生视频（快速） | 默认推荐 |
| `wanx2.1-i2v-turbo` | 图生视频（快速） | 分镜图→视频 |
| `wanx2.1-i2v-plus` | 图生视频（高质量） | 追求画质 |

**配置示例**：
```bash
VIDEO_PROVIDER=dashscope
DASHSCOPE_API_KEY=sk-xxxxx
DASHSCOPE_VIDEO_MODEL=wanx2.1-t2v-turbo
WANX_MODEL=wanx2.1-t2i-turbo  # 图片生成也用万相
```

**获取 API Key**：
1. 访问 [阿里云百炼控制台](https://bailian.console.aliyun.com/)
2. 创建 API Key
3. 复制到 `.env` 文件

---

### 🟣 可灵 AI（Kling）

**优势**：
- 视频质量高，动作自然
- 支持更长时长（v2/v3 支持 10s/15s）
- 首尾帧功能（v3）

**支持模型**：
| 模型 | 时长 | 特点 |
|------|------|------|
| `kling-v1` | 5s | 基础版 |
| `kling-v1-5` | 5s | 增强版 |
| `kling-v2-1-master` | 5/10s | 高质量 |
| `kling-v2-5-turbo` | 5s | 快速版 |
| `kling-v2-6` | 5/10s | 最新 |
| `kling-v3-0` | 3-15s | 最强 |

**配置示例**：
```bash
VIDEO_PROVIDER=kling
KLING_ACCESS_KEY=your_access_key
KLING_SECRET_KEY=your_secret_key
KLING_MODEL=kling-v1-5
```

**获取 Access Key / Secret Key**：
1. 访问 [可灵开放平台](https://klingai.com/)
2. 注册账号 → API 服务 → 获取密钥对

---

### 🟠 火山方舟（Ark）

**优势**：
- 字节跳动生态，与抖音/TikTok 结合好
- 支持首尾帧图生视频（Seedance）
- 国内访问速度快

**配置示例**：
```bash
VIDEO_PROVIDER=ark
ARK_API_KEY=your_api_key
ARK_MODEL=ep-20240918154325-xxxxx  # Endpoint ID（必填）
```

**获取 API Key**：
1. 访问 [火山引擎控制台](https://console.volcengine.com/)
2. 方舟平台 → 创建应用 → 获取 API Key + Endpoint ID

---

## 🔄 Mock ↔ Real 切换

### 切换到 Real 模式

```bash
# .env
VIDEO_PROVIDER_MODE=real
VIDEO_PROVIDER=dashscope  # 或 kling / ark
DASHSCOPE_API_KEY=sk-xxxxx  # 对应的 API Key
```

### 回退到 Mock 模式（调试用）

```bash
# .env
VIDEO_PROVIDER_MODE=mock
# 其他 VIDEO_* 配置可保留，不影响
```

**Mock 模式行为**：
- 分镜图：PIL 生成彩色占位 PNG（带镜头序号水印）
- 视频片段：ffmpeg 生成纯色 MP4（3-5 秒）
- 拼接成片：真实 ffmpeg 拼接占位片段
- **零成本、零延迟、可离线**

---

## 🧪 测试真实 API

### 方法 1：通过 Chat UI 测试

1. 启动前端 + 后端
2. 打开 Chat 页面
3. 发送："帮我生成一个 15 秒的短视频，主题是夕阳下的海边"
4. 等待分镜确认 → 点击"确认生成"
5. 观察实时进度推送和最终视频

### 方法 2：通过 Smoke Test 脚本测试

```bash
cd backend
python scripts/smoke_video_studio.py
```

该脚本会：
1. 创建分镜（mock 或 real 取决于配置）
2. 提交 shot_image 任务
3. 提交 shot_video 任务
4. 执行 compose 拼接
5. 打印完整结果

### 方法 3：检查日志

```bash
# 实时查看视频相关日志
tail -f logs/app.log | grep -E "\[real_provider\]|\[video_worker\]|\[dashscope-video\]|\[kling\]"

# 成功标志
[real_provider] 视频适配器已创建: dashscope
[real_provider] 生成视频片段: shot=1 mode=t2v
[dashscope-video] submitting to https://dashscope.aliyuncs.com/api/v1, model=wanx2.1-t2v-turbo
[dashscope-video] task submitted: task_xxxx
[video_worker] SSE 推送: video_job_succeeded (job=job_xxxx)
```

---

## ⚠️ 常见问题

### Q1: 配置了 API Key 但还是走 mock？

**检查清单**：
1. ✅ `VIDEO_PROVIDER_MODE=real` （不是 mock）
2. ✅ 对应的 API Key 已填写（非空字符串）
3. ✅ 重启了后端服务（配置修改后需重启）
4. ✅ 查看启动日志是否有警告信息

**调试命令**：
```python
# 在 Python REPL 中测试
from app.adapters.media_provider import resolve_media_provider
import asyncio

provider = asyncio.run(resolve_media_provider())
print(f"Provider name: {provider.name}")
print(f"Fallback note: {getattr(provider, 'fallback_note', None)}")
check = asyncio.run(provider.check())
print(f"Check result: {check}")
```

### Q2: API Key 格式正确吗？

| Provider | Key 格式 | 示例 |
|----------|---------|------|
| DashScope | `sk-` 开头 | `sk-abc123def456...` |
| Kling | 字符串 | `xxxxxxxxxxxx` |
| Ark | 字符串 | `xxxxxxxxxxxx` |

### Q3: 额度/配额不足？

**DashScope**：
- 新用户有免费额度
- 查看余额：[百炼控制台](https://bailian.console.aliyun.com/) → 用量统计

**Kling**：
- 需要充值或购买套餐
- 查看余额：[可灵控制台](https://klingai.com/) → 账户中心

**Ark**：
- 按调用次数计费
- 查看余额：[火山引擎控制台](https://console.volcengine.com/) → 费用中心

### Q4: 如何查看详细的 API 调用日志？

在 `.env` 中设置：

```bash
LOG_LEVEL=DEBUG
```

然后过滤视频相关日志：

```bash
tail -f logs/app.log | grep -E "video|dashscope|kling|ark"
```

### Q5: 想临时禁用某个 Provider 但保留配置？

注释掉即可（不删除）：

```bash
# VIDEO_PROVIDER_MODE=real  # 临时注释，回退 mock
VIDEO_PROVIDER_MODE=mock

# DASHSCOPE_API_KEY=sk-xxxxx  # 保留配置备用
```

---

## 📊 性能对比

| 指标 | Mock 模式 | Real 模式（DashScope） | Real 模式（Kling） |
|------|-----------|------------------------|--------------------|
| **单镜图片** | < 1s（PIL 本地） | 10-30s（API 异步） | N/A |
| **单镜视频** | 2-5s（ffmpeg） | 60-180s（API 异步） | 120-300s |
| **拼接成片** | 5-10s（ffmpeg） | 5-10s（相同） | 5-10s（相同） |
| **质量** | 占位色块 | 真实 AI 视频 | 真实 AI 视频 |
| **成本** | 免费 | 按量付费 | 按量付费 |
| **依赖** | 仅 ffmpeg | 网络 + API Key | 网络 + API Key |

**建议开发阶段**：用 mock 调试流程，用 real 验证效果。

---

## 🎬 下一步优化方向

当前实现（阶段 1）已满足基本需求，后续可扩展：

- [ ] **并发控制**：按 provider 限制同时任务数（避免触发限流）
- [ ] **重试机制**：API 失败自动重试（指数退避）
- [ ] **缓存层**：相同 prompt 缓存结果（节省成本）
- [ ] **进度回调**：轮询中间状态推送到前端（如"渲染中 45%"）
- [ ] **多 Provider 负载均衡**：按成本/速度智能路由
- [ ] **视频编辑**：剪辑、字幕、配音等后处理

---

## 📞 技术支持

遇到问题？检查以下文件：

1. **配置**：`backend/.env` 或环境变量
2. **日志**：`backend/logs/app.log`
3. **Provider 实现**：
   - Mock：`backend/app/adapters/mock_media_provider.py`
   - Real：`backend/app/adapters/real_media_provider.py`
   - DashScope：`backend/app/adapters/video_gen.py`（`DashScopeVideoAdapter`）
   - Kling：`backend/app/adapters/video_gen.py`（`KlingVideoAdapter`）
   - Ark：`backend/app/adapters/video_gen.py`（`ArkVideoAdapter`）
4. **分发逻辑**：`backend/app/adapters/media_provider.py`（`resolve_media_provider()`）
5. **Worker**：`backend/app/services/video_worker.py`
6. **Handler**：`backend/app/services/video_handlers.py`
7. **SSE 推送**：`backend/app/main.py`（`_on_video_job_event`）

---

**最后更新**：2026-10-18
**适用版本**：阶段 1（RealMediaProvider 接入）