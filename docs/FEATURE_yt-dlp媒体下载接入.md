# FEATURE: yt-dlp 媒体下载能力接入

## 需求锚定

- 需求来源: 2026-11-01 用户对话确认的方案（P2 口头需求，本文档即锚定文档）
- 需求标题: 为 platform_spider 接入 yt-dlp，提供视频文件下载能力
- 背景: 现有 spider（xhs / douyin / bilibili）只爬元数据，无视频文件落盘能力
- 验收标准:
  1. `BasePlatformSpider.download_media(url, ...)` 可用，白名单平台（bilibili 等）走 yt-dlp 下载成功并返回文件路径
  2. 非白名单平台（xiaohongshu / douyin）明确拒绝，给出可读错误
  3. 登录态 cookies（dict 形式，与 bilibili_spider 一致）自动转换为 Netscape 格式传给 yt-dlp
  4. 输出目录可配置（env `MEDIA_DOWNLOAD_DIR`），默认 `backend/outputs/media/{platform}/`
  5. 单元测试（mock yt-dlp）真实通过；真实网络冒烟验证通过

## 平台支持边界（重要）

| 平台 | yt-dlp 支持 | 处理方式 |
|---|---|---|
| bilibili | 支持 | 白名单放行 |
| youtube 等通用站点 | 支持 | 白名单放行（generic 路由） |
| xiaohongshu | 不支持（无 extractor） | 拒绝，保持现有 xhs_spider 路线 |
| douyin | 部分支持且风控频繁变动 | 拒绝，保持现有 douyin_spider 路线 |

## 方案设计

- 模块: `backend/capabilities/scrapling/spiders/media_downloader.py`
  - `download_media(url, platform, out_dir=None, cookies=None, max_height=None) -> dict`
  - 返回 `{platform, url, filepath, title, duration, extractor}`
  - yt-dlp 用 Python API（`YoutubeDL`），不用 CLI 子进程
- 基类接入: `BasePlatformSpider.download_media()` 薄封装，自动带 self.platform / self.cookies
- Cookie 转换: dict（`{name: value}` 或 `{name: {value, domain...}}`）→ 临时 Netscape cookiefile → `cookiefile` 选项
- 配置: `MEDIA_DOWNLOAD_DIR` 环境变量，默认相对 `backend/outputs/media`
- 注册: 无新增 registry；`app/platform_spider/base.py` shim 已透传基类方法

### 参考验证（SOP Phase 1.2）

- 不确定点: yt-dlp Python API 如何传入登录态与输出模板
- 参考1: yt-dlp 官方 README「Embedding yt-dlp」章节 → `YoutubeDL(opts).download([url])`，选项 `cookiefile`（Netscape 格式）、`outtmpl`、`format`、`quiet`
- 参考2: yt-dlp 源码 `yt_dlp/cookies.py` → Netscape cookie 行格式 `domain \t include_subdomains \t path \t secure \t expiry \t name \t value`
- 决策: cookies dict 自行转换为 Netscape 临时文件（yt-dlp Python API 不接受 dict），格式按参考2

## 环境注意

- 本机 pypi.org 直连不通，安装需走镜像: `pip install yt-dlp -i https://mirrors.aliyun.com/pypi/simple/`
- 已安装版本: yt-dlp 2026.10.26

## 测试计划

- L1: `backend/tests/test_media_downloader.py`（mock `yt_dlp.YoutubeDL`）
  - bilibili 白名单通过、返回结构完整
  - xiaohongshu / douyin 拒绝并抛 `UnsupportedPlatformError`
  - cookies dict → Netscape 文件转换正确（含 dict 形式 value）
  - 空 url / 下载失败错误路径
- L4 冒烟: 真实下载一个 B站公开视频，验证文件落盘（网络依赖，不在 CI 必跑）

## 验收记录（2026-11-01）

1. `BasePlatformSpider.download_media()` 可用，bilibili 真实下载通过 → 产物 `backend/outputs/media/bilibili/BV1GJ411x7h7.mp4`（9.6MB，yt-dlp 2026.10.26）
2. xiaohongshu / douyin 拒绝 → `UnsupportedPlatformError`，单测覆盖
3. cookies dict → Netscape 转换 → 单测覆盖（含 `{value, domain}` 形态、用后清理）
4. 输出目录 `MEDIA_DOWNLOAD_DIR` 可配置，默认 `backend/outputs/media/{platform}/` → 单测覆盖
5. 单元测试 13 passed（`backend/tests/test_media_downloader.py`，mock yt-dlp）

实现期补充决策：
- ffmpeg 未在 PATH：自动降级使用 imageio_ffmpeg 自带二进制（`opts["ffmpeg_location"]`），与项目视频管线复用同一份 ffmpeg
- 本机 pypi.org 直连不通，安装走阿里云镜像

## 前端接入（2026-11-01 第二期）

需求来源: 用户确认按「作品卡片手动下载」范围实现（MyWorksView 详情页按钮，按需单个下载）。

### 用户使用路径

```
我的空间 → 打开某个已采集的 B站视频作品 → 详情页顶栏 [下载视频]
  → 按钮变"下载中..."（前端每 2s 轮询作品详情）
  → 完成后变绿色"本地已缓存"徽标，视频播放器自动切换为本地文件播放
```

### 实现明细

- 后端服务: `app/services/media_download_service.py`
  - `start_download(record_id, user_id)`: 校验归属/白名单/幂等 → `asyncio.create_task` 后台下载（yt-dlp 阻塞调用走 `asyncio.to_thread`）→ `local_video_path` 回写 → 通知总线推 `work_media_download_started/finished/failed`
  - `to_web_url()`: 本地路径 → `/outputs/...` 静态挂载路径，不暴露服务器路径
- 端点: `POST /api/my-works/{record_id}/download-media`（返回 started/ready/blocked 三态）；作品详情响应新增 `local_video_url`
- 数据库: `published_content_performance` 加 `local_video_path` 列（迁移 `021_local_video`，已执行）
- 前端:
  - `api/my_works.ts`: `downloadWorkMedia()` + `local_video_url` 字段
  - `components/portfolio/workMedia.ts`: 播放地址本地缓存优先（远程 CDN 链接常因 referer 限制无法播放，这正是下载的核心价值）
  - `views/PortfolioDetailView.vue`: 顶栏下载按钮 + 轮询（超时 5 分钟）+ 状态徽标
- 进度机制: 状态轮询（与平台账号同步的现有模式一致），通知总线事件并行推送备用

### 测试

- `backend/tests/test_media_download_service.py`: 11 passed（校验/Web 路径转换/三态/回写）
- 前端 `npm run type-check` 通过
- 注意: 需重启后端服务后生效
