<template>
  <div class="rl-chat-layout">
  <div class="rl-chat" :class="{ 'is-dark': darkTheme, 'is-hero': heroMode, 'is-hero-intro': heroIntroPlaying, 'is-composer-anim-off': suppressComposerAnim }">

    <!--
      Hero 主光晕（A 方案）：放在 .rl-chat 根层、z-index:0，是整个页面的背景氛围。
      ⚠️ 绝不能放回 .rl-composer-seat 里 —— 座位在 hero 模式是 z-index:20，
      比 .rl-hero-zone（z-index:10）高，光晕放在座位里会盖在 logo / 标题 / 起手式之上，
      等于给文字蒙了一层淡紫，范围越大越脏。放在根层后它永远在内容之下。
      居中基准是「logo+标题+输入卡」这一整组，而不是单张输入卡。
    -->
    <svg v-if="heroMode && chatReady" class="rl-hero-glow" viewBox="0 0 900 400" fill="none" aria-hidden="true">
      <defs>
        <filter id="rl-hero-glow-blur" x="-80" y="-80" width="1060" height="560" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">
          <feFlood flood-opacity="0" result="BackgroundImageFix" />
          <feBlend mode="normal" in="SourceGraphic" in2="BackgroundImageFix" result="shape" />
          <feGaussianBlur stdDeviation="40" result="effect1_foregroundBlur" />
        </filter>
      </defs>
      <g filter="url(#rl-hero-glow-blur)">
        <ellipse cx="450" cy="200" rx="430" ry="143" />
      </g>
    </svg>

    <!-- SVG 滤镜：马克笔背景层手绘笔触效果（仅作用于伪元素背景，不伤文字） -->
    <svg style="position:absolute;width:0;height:0" aria-hidden="true">
      <defs>
        <filter id="rl-marker-filter">
          <feTurbulence type="turbulence" baseFrequency="0.04" numOctaves="4" result="noise" seed="3" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="2" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
    </svg>

    <div v-if="activeWork && !heroMode" class="rl-work-bar">
      <div class="rl-work-bar-info">
        <FileText :size="13" :stroke-width="1.8" class="rl-work-bar-icon" />
        <span class="rl-work-bar-title">{{ activeWork.title || '未命名作品' }}</span>
        <span v-if="activeWork.platform" class="rl-work-bar-platform">{{ activeWork.platform }}</span>
        <span v-if="activeWork.performanceTier && activeWork.performanceTier !== '-'" class="rl-work-bar-metrics">{{ activeWork.performanceTier }}</span>
      </div>
      <div class="rl-work-bar-actions">
        <button class="rl-work-bar-btn" @click="exitWorkContext" title="解除作品关联">
          <X :size="14" />
        </button>
      </div>
    </div>

    <div v-if="workStore.analysisContext && !heroMode" class="rl-analysis-bar">
      <div class="rl-analysis-bar-info">
        <img src="/icons/分析.svg" class="rl-analysis-bar-icon" />
        <span class="rl-analysis-bar-label">分析已联动</span>
        <span class="rl-analysis-bar-detail">{{ workStore.analysisContext.overview?.total || 0 }}篇数据 · 归因{{ ({ low: '低', medium: '中', high: '高' } as Record<string,string>)[workStore.analysisContext.confidence] || workStore.analysisContext.confidence }}置信</span>
      </div>
      <div class="rl-analysis-bar-actions">
        <button class="rl-work-bar-btn rl-analysis-create" @click="handoffOpen = true" title="基于分析开启创作">
          <Sparkles :size="13" />
          开启创作
        </button>
        <button class="rl-work-bar-btn" @click="ctxStore.unlinkAnalysis()" title="解除联动">
          <X :size="14" />
        </button>
      </div>
    </div>

    <!-- 选题参考条：从选题池「发起工作流」带入（sessionStorage.pending_topic_reference 一次性消费）。
        复用 rl-work-bar 视觉体系；hero 态用绝对定位变体，不挤占居中布局。 -->
    <div v-if="topicReference" class="rl-work-bar" :class="{ 'rl-work-bar-hero': heroMode }">
      <div class="rl-work-bar-info">
        <Lightbulb :size="13" :stroke-width="1.8" class="rl-work-bar-icon" />
        <span class="rl-work-bar-platform">选题参考</span>
        <span class="rl-work-bar-title">{{ topicReference.title }}</span>
        <span v-if="topicReference.platform" class="rl-work-bar-platform">{{ topicReference.platform }}</span>
        <span v-if="topicRefMetrics" class="rl-work-bar-metrics">{{ topicRefMetrics }}</span>
      </div>
      <div class="rl-work-bar-actions">
        <button class="rl-work-bar-btn" @click="topicReference = null" title="移除选题参考（不再随消息发送）">
          <X :size="14" />
        </button>
      </div>
    </div>

    <CreationHandoffCard
      v-if="handoffOpen && workStore.analysisContext"
      :analysis="workStore.analysisContext"
      @close="handoffOpen = false"
      @start="onHandoffStart"
    />

    <div v-if="activeFile && !heroMode" class="rl-file-bar">
      <div class="rl-file-bar-info">
        <component :is="getFileIconComponent(activeFile.type)" :size="13" :stroke-width="1.6" class="rl-file-bar-icon" />
        <span class="rl-file-bar-name">{{ activeFile.name }}</span>
        <span class="rl-file-bar-size">{{ fileStore.formatFileSize(activeFile.size) }}</span>
      </div>
      <div class="rl-file-bar-actions">
        <button class="rl-work-bar-btn" @click="fileStore.setActiveFile(null)" title="移除文件">
          <X :size="14" />
        </button>
      </div>
    </div>

    <!-- 侧边栏开关浮动按钮 -->
    <button
      v-if="!ctxStore.sidebarVisible"
      class="rl-sidebar-toggle"
      @click="toggleWorkDetail"
      title="打开工作台面板"
    >
      <PanelRight :size="16" :stroke-width="1.8" />
    </button>

    <!-- 侧会话面板开关浮动按钮（PRD FR-E） -->
    <button
      v-if="!heroMode"
      class="rl-side-toggle"
      :class="{ 'is-on': sidePanelOpen }"
      @click="toggleSidePanel"
      title="侧会话面板"
    >
      <GitBranch :size="16" :stroke-width="1.8" />
    </button>

    <SideSessionPanel
      :open="sidePanelOpen"
      @close="sidePanelOpen = false"
      @brought-back="onBroughtBack"
    />

    <!-- 滚动主体 -->
    <div
      class="rl-scroll-body"
      :class="{ 'rl-scroll-hidden': heroMode, 'is-streaming': isStreaming }"
      ref="scrollBodyRef"
      @click.capture="onContentClickCapture"
      @wheel="onUserWheel"
      @touchmove="onTouchMove"
      @mousedown="markUserInput"
      @scroll="onScroll"
    >
      <div v-if="!heroMode" class="rl-column">
        <div
          v-for="(msg, idx) in currentMessages"
          :key="idx"
          class="rl-flow-item"
          :data-user-idx="msg.role === 'user' ? minimapUserIndices.indexOf(idx) : undefined"
        >
          <!-- ═══ UserCell（侧会话摘要特殊消息走独立样式，PRD FR-B3/G3）═══ -->
          <SideDigestMessageCell
            v-if="msg.role === 'user' && isSideDigestMessage(msg.agentMeta as any)"
            :content="msg.content"
            :side-session-title="(msg.agentMeta as any)?.sideSessionTitle"
            :side-session-id="(msg.agentMeta as any)?.sideSessionId"
          />
          <UserMessageCell v-else-if="msg.role === 'user'" :msg="msg" @resend="onUserResend" />

          <!-- ═══ AssistantCell ═══ -->
          <AssistantMessageCell v-else-if="msg.role === 'assistant'" :msg="msg" :idx="idx" :sse-session-id="sse.activeSessionId.value || ''" />
        </div>

        <!-- Turn 状态指示行已退役：思考/执行状态由消息内思考时间线唯一承载（ThoughtStream） -->
        <div v-if="false && isStreaming" class="rl-turn-status" :class="{ 'rl-turn-status--thinking': streamingThinking || !streamingHasContent }">
          <span class="rl-turn-status-dot" />
          <span class="rl-turn-status-shimmer-text">
            <template v-if="isChatMode">
              {{ currentStatusIndicator.header || (streamingThinking ? '思考中…' : streamingHasContent ? '生成回复中…' : '思考中…') }}
            </template>
            <template v-else>
              <template v-if="!streamingThinking && !streamingHasContent">Thinking…</template>
              <template v-else-if="streamingThinking">{{ currentStatusIndicator.header || 'Reasoning…' }}</template>
              <template v-else>Writing…</template>
            </template>
          </span>
        </div>
      </div>
    </div>

    <!-- Minimap 粒子导航栏 -->
    <div
      v-if="minimapDots.length > 1 && !heroMode"
      class="rl-minimap"
      ref="minimapContainerRef"
    >
      <canvas
        ref="minimapCanvasRef"
        class="rl-minimap-canvas"
        @click="onMinimapCanvasClick"
        @mousemove="onMinimapCanvasMouseMove"
        @mouseleave="onMinimapCanvasMouseLeave"
      ></canvas>
      <div
        v-if="minimapHoveredIndex >= 0 && minimapHoveredPreview"
        class="rl-minimap-tooltip"
        :style="{ top: minimapHoveredY + 'px' }"
      >{{ minimapHoveredPreview }}</div>
    </div>

    <!-- HERO 区 -->
    <div v-if="heroMode && chatReady" class="rl-hero-zone" aria-hidden="false">
      <div class="rl-hero-stack">
        <div class="rl-hero-brand">
          <div
            class="rl-hero-logo-wrap"
            :class="{ 'is-pop': heroLogoPop }"
            role="button"
            tabindex="0"
            :title="heroLogoCrab ? '点我，变回文字 Logo' : '点我，变成螃蟹'"
            aria-label="切换品牌 Logo"
            @click="toggleHeroLogo"
            @keydown.enter.prevent="toggleHeroLogo"
            @keydown.space.prevent="toggleHeroLogo"
            @animationend="onHeroLogoPopEnd"
          >
            <transition name="rl-hero-logo-swap" mode="out-in">
              <img
                v-if="heroLogoCrab"
                key="crab"
                src="/icons/crab.svg"
                alt="Pulse Studio"
                class="rl-hero-logo-crab rl-hero-logo-crab-art"
              />
              <img
                v-else
                key="text"
                src="/icons/logo2.svg"
                alt="Pulse Studio"
                class="rl-hero-logo-crab"
              />
            </transition>
            <div class="rl-hero-logo-shimmer"></div>
          </div>
          <h2 class="rl-hero-title" :title="heroTitle">
            <span
              v-for="(seg, i) in heroTitleSegments"
              :key="i"
              :class="{ 'rl-hero-title-accent': seg.accent }"
            >{{ seg.text }}</span>
          </h2>
          <span class="rl-hero-badge">测试版</span>
        </div>
        <!-- 能力标签：六大核心能力常驻可见，点开输入卡前就知道平台实力（内容 Tab 下不携带） -->
        <div v-if="!contentMode" class="rl-hero-capabilities">
          <span v-for="cap in greetingCapabilities" :key="cap.label" class="rl-hero-cap">
            <component :is="heroIcon(cap.icon)" :size="13" :stroke-width="1.8" class="rl-hero-cap-icon" />
            {{ cap.label }}
          </span>
        </div>
      </div>
    </div>

    <!-- 确认/澄清弹窗停靠：输入框上方浮出（agent 阻塞等待，不遮挡消息流） -->
    <div v-if="pendingConfirm.req" class="rl-confirm-dock">
      <ChatGuardianConfirmCard
        v-if="pendingConfirm.req.kind === 'guardian'"
        :session-id="pendingConfirm.req.sessionId"
        :loop-state-path="pendingConfirm.req.loopStatePath"
        :tool-name="pendingConfirm.req.toolName"
        :confirmation-prompt="pendingConfirm.req.confirmationPrompt"
        :risk-info="pendingConfirm.req.riskInfo"
        :turn-id="pendingConfirm.req.turnId"
        :failed-error="pendingConfirm.req.failedError"
        @confirmed="onDockAction('confirmed')"
        @rejected="onDockAction('rejected')"
      />
      <ChatConfirmCard
        v-else-if="pendingConfirm.req.kind === 'confirm'"
        :session-id="pendingConfirm.req.sessionId"
        :loop-state-path="pendingConfirm.req.loopStatePath"
        :prompt="pendingConfirm.req.cardProps.prompt"
        :skill-name="pendingConfirm.req.cardProps.skillName"
        :turn-id="pendingConfirm.req.turnId"
        @confirmed="(action: 'approve' | 'reject', data?: any) => onDockAction(action, data)"
        @expired="onDockAction('expired')"
      />
      <ChatClarificationCard
        v-else-if="pendingConfirm.req.kind === 'clarify'"
        :session-id="pendingConfirm.req.sessionId"
        :loop-state-path="pendingConfirm.req.loopStatePath"
        :batches="pendingConfirm.req.cardProps.batches"
        :prompt="pendingConfirm.req.cardProps.prompt"
        :skill-name="pendingConfirm.req.cardProps.skillName"
        :turn-id="pendingConfirm.req.turnId"
        @clarified="(answers: Record<string, any>, data?: any) => onDockAction('clarified', { answers, data })"
        @skipped="onDockAction('skipped')"
        @cancelled="onDockAction('cancelled')"
      />
      <ChatStoryboardConfirmCard
        v-else-if="pendingConfirm.req.kind === 'storyboard'"
        :session-id="pendingConfirm.req.sessionId"
        :loop-state-path="pendingConfirm.req.loopStatePath"
        :shots="pendingConfirm.req.cardProps.shots"
        :title="pendingConfirm.req.cardProps.title"
        :turn-id="pendingConfirm.req.turnId"
        :failed-error="pendingConfirm.req.cardProps.failedError"
        @confirmed="(data?: any) => onDockAction('confirmed', data)"
        @cancelled="onDockAction('cancelled')"
        @revise="(comment: string) => onDockAction('revise', { comment })"
      />
      <!-- cutplan 分支已删除（设计文档 3.4 #3）：叠加方案确认卡改由
           消息内 VideoTaskCard 容器卡原位渲染，dock 不再承接该类型。 -->
    </div>

    <!-- 内置浏览器抽屉：正文链接的侧边栏渲染 -->
    <Teleport to="body">
      <Transition name="ccd-drawer">
        <div v-if="urlDrawer.open" class="rl-url-drawer-mask" @click.self="urlDrawer.open = false">
          <div class="ccd-drawer rl-url-drawer">
            <div class="ccd-drawer-head">
              <span class="ccd-drawer-title" :title="urlDrawer.url">{{ urlDrawer.title }}</span>
              <div class="ccd-drawer-nav">
                <a class="ccd-drawer-btn" :href="urlDrawer.url" target="_blank" title="在新窗口打开"><ExternalLink :size="15" /></a>
                <button class="ccd-drawer-btn" @click="urlDrawer.open = false"><X :size="15" /></button>
              </div>
            </div>
            <div class="ccd-drawer-body">
              <iframe
                :src="urlDrawer.url"
                class="ccd-drawer-iframe"
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                referrerpolicy="no-referrer"
              ></iframe>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 输入卡 -->
    <div v-if="chatReady" class="rl-composer-seat" :class="{ 'rl-composer-hero': heroMode }">
      <!--
        Hero 背光（D 方案）：一块和输入卡同轮廓的圆角矩形光源，垫在卡片正下方。
        卡片是不透明白底，会把光源中心整个吃掉，只有溢出轮廓的那半圈高斯尾巴留在外面 ——
        这就是「底光余晖绕卡一周」的物理来源：底部最亮，两侧向上衰减，顶边只剩一丝。
        与主光分工：主光（.rl-hero-glow）在根层负责整页氛围，这层只负责描卡片轮廓。
        座位在 hero 模式是独立层叠上下文（z-index:20），卡片是 z-index:10，
        所以这层 z-index:0 稳定落在卡片之下，不会盖到输入框文字。
      -->
      <div v-if="heroMode" class="rl-hero-glow-rim" aria-hidden="true"></div>
      <!--
        品牌字标（2026-11-06 修正）：必须在输入卡**正后方**，且不许漏出卡片轮廓。
        之前放在根层 .rl-hero-glow 里，位置按整页画布算，结果字落在卡片上方的
        标题/起手式一带，从卡片外面「漏」了出来 —— 这就是看着不对的原因。
        现在挪进「输入卡座位」内部，用与卡片完全相同的几何框住它：
          卡片顶 = 座位 padding box 顶 → top:0
          卡片底 = 座位底 − 8px     → bottom:8px
          卡片宽 = 座位宽 − 32px     → width:calc(100% - 32px) + max-width:680 + margin:auto
        于是字标的矩形恒等于卡片矩形，无论卡片多高、窗口多大都对得上。
        层级 z-index:0 < 卡片 z-index:10，卡片又是 #ffffff 不透明白底，
        所以字被整块吃掉，一丝都不露。它仍然待在光晕图层里（和 .rl-hero-glow
        同层），只是不再从卡片范围里溢出来。
      -->
      <svg
        v-if="heroMode"
        class="rl-hero-brand-backdrop"
        viewBox="0 0 900 200"
        preserveAspectRatio="xMidYMid slice"
        aria-hidden="true"
      >
        <text
          x="450"
          y="100"
          text-anchor="middle"
          dominant-baseline="middle"
          class="rl-hero-glow-brand"
        >PULSE STUDIO</text>
      </svg>
      <!--
        螃蟹必须渲染在「输入卡座位」内部（而不是页面根部）：
        座位 .rl-composer-seat 自带 z-index:100，是独立的层叠上下文。
        放在座位外面时，无论 z-index 调成多少，都只能待在座位的下方，
        会被座位背景（那条从透明渐变到 --rl-bg-2 的遮罩）整只盖住。
        放进座位里之后：座位背景永远绘制在自己的子元素之下，
        螃蟹（z-index:0）只在输入卡（z-index:10）下方被遮住 ——
        归位点落在卡片右上角之上，所以平时完整可见；拖到卡片范围内才会被卡片盖住。
      -->
      <CrabCompanion
        :show="!heroMode && crabVisible && pluginStore.isEnabled(CRAB_PLUGIN_ID)"
        :input-rect="crabInputRect"
        :seat-rect="crabSeatRect"
        :is-streaming="isStreaming"
        :panel-open="crabPanelOpen"
        @toggle-notes="onToggleCrabNotes"
      />
      <div class="rl-composer-card">
        <!-- 已选智能体标签行 -->
        <div v-if="selectedAgentId" class="rl-agent-tag-row">
          <span class="rl-agent-tag">
            <img src="/icons/智能体.svg" class="rl-agent-tag-icon" />
            <span class="rl-agent-tag-name">{{ selectedAgentLabel }}</span>
            <button class="rl-agent-tag-close" @click="clearSelectedAgent" title="取消选择">
              <X :size="10" />
            </button>
          </span>
        </div>
        <div class="rl-at-picker-wrap">
          <AtContentPicker
            :visible="showAtContentPicker"
            :agents="availableAgents"
            @select="onAtContentSelect"
            @close="showAtContentPicker = false"
          />
        </div>
        <!--
          待发送图片条：作为输入卡的**独立行**排在输入框上方。
          原来是 absolute 浮层（bottom:100%），会盖住输入框和刚发出的消息。
        -->
        <Transition name="rl-slide-up">
          <div v-if="pendingImageAssets.length > 0" class="rl-img-preview-bar">
            <div v-for="(img, idx) in pendingImageAssets" :key="img.asset_id" class="rl-img-thumb-item">
              <img :src="img.thumbnail_url || img.original_url" class="rl-img-thumb" :alt="img.filename || '配图'" />
              <button class="rl-img-thumb-remove" @click="removePendingImage(idx)" title="移除">&times;</button>
            </div>
            <button class="rl-img-clear-all" @click="pendingImageAssets = []" title="清空全部">清空</button>
          </div>
        </Transition>
        <!--
          待发送视频条：视频缩略图预览
        -->
        <Transition name="rl-slide-up">
          <div v-if="pendingVideoAssets.length > 0" class="rl-img-preview-bar">
            <div v-for="(vid, idx) in pendingVideoAssets" :key="vid.asset_id" class="rl-img-thumb-item">
              <video :src="vid.original_url" class="rl-img-thumb" muted></video>
              <button class="rl-img-thumb-remove" @click="removePendingVideo(idx)" title="移除">&times;</button>
            </div>
            <button class="rl-img-clear-all" @click="pendingVideoAssets = []" title="清空全部">清空</button>
          </div>
        </Transition>
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="rl-input"
          :disabled="isStreaming && !canTypeWhilePaused"
          :placeholder="canTypeWhilePaused ? '等待确认：点上方「批准执行」，或直接输入内容作为补充说明继续' : (isStreaming ? '等待回复中…' : (selectedAgentId ? `向 ${selectedAgentLabel} 发送消息…` : '给Pulse发送消息（@ 智能体 / 内容 / 技能）'))"
          rows="1"
          @keydown="onKeyDown"
          @input="onUserInput"
          @focus="onUserInput"
          @paste="onPasteImages"
        ></textarea>
        <div class="rl-composer-row">
          <div class="rl-tools">
          <div class="rl-plus-wrap">
            <!-- 图片上传按钮 -->
            <div class="rl-img-upload-wrap">
              <button class="rl-img-upload-btn" :class="{ 'rl-img-upload-active': pendingImageAssets.length > 0 }" :disabled="isStreaming || uploadingImages" :title="uploadingImages ? '上传中…' : '上传图片（图文卡片 / Skill 可直接使用，最多十张）'" @click="triggerImageUpload">
                <img src="/icons/Camera-8.svg" alt="上传图片" style="width:14px;height:14px;" />
                <span v-if="pendingImageAssets.length > 0" class="rl-img-badge">{{ pendingImageAssets.length }}</span>
              </button>
              <input ref="imageUploadRef" type="file" accept="image/*" multiple class="rl-hidden-input" @change="onImageFilesSelected" />
            </div>
            <!-- 视频上传按钮 -->
            <div class="rl-img-upload-wrap">
              <button class="rl-img-upload-btn" :class="{ 'rl-img-upload-active': pendingVideoAssets.length > 0 }" :disabled="isStreaming || uploadingVideos" :title="uploadingVideos ? '上传中…' : '上传视频（智能剪辑 / 叠加增强可直接使用，最多五个）'" @click="triggerVideoUpload">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                <span v-if="pendingVideoAssets.length > 0" class="rl-img-badge">{{ pendingVideoAssets.length }}</span>
              </button>
              <input ref="videoUploadRef" type="file" accept="video/*" multiple class="rl-hidden-input" @change="onVideoFilesSelected" />
            </div>
            </div>
            <!-- 绑定文件夹按钮 -->
            <div class="rl-ws-pick-wrap">
              <button class="rl-ws-pick-btn" :class="{ 'rl-ws-pick-active': workspaceStore.activeWorkspace }" :disabled="workspaceStore.binding" @click="workspaceStore.workspaces.length > 0 ? toggleWorkspaceList() : onPickFolder()" title="绑定本地文件夹（智能体可直接操控）">
                <FolderOpen :size="14" :stroke-width="2" />
              </button>
              <!-- 已绑定工作区下拉列表 -->
              <Transition name="rl-slide-up">
                <div v-if="showWorkspaceList" class="rl-ws-dropdown" @click.stop>
                  <div class="rl-ws-dropdown-header">
                    <span class="rl-ws-dropdown-title">已绑定的工作区</span>
                    <button class="rl-ws-dropdown-add" @click="onPickFolder" :disabled="workspaceStore.binding">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                      绑定新文件夹
                    </button>
                  </div>
                  <div class="rl-ws-dropdown-list">
                    <!--
                      ⚠️ 外层必须是 div 而不是 button：里面嵌了"解绑"按钮，
                      <button> 套 <button> 是非法 HTML（Vite 编译期就告警），
                      部分浏览器里外层点击会失效、内层解绑可能误触发切换。
                      键盘可达性由 role="button" + tabindex + Enter/Space 补齐。
                    -->
                    <div
                      v-for="ws in workspaceStore.workspaces"
                      :key="ws.id"
                      class="rl-ws-dropdown-item"
                      :class="{ 'rl-ws-dropdown-selected': ws.id === workspaceStore.activeWorkspaceId }"
                      role="button"
                      tabindex="0"
                      @click="switchToFolder('ws-' + ws.id); showWorkspaceList = false"
                      @keydown.enter="switchToFolder('ws-' + ws.id); showWorkspaceList = false"
                      @keydown.space.prevent="switchToFolder('ws-' + ws.id); showWorkspaceList = false"
                    >
                      <FolderOpen :size="14" :stroke-width="2" />
                      <div class="rl-ws-item-info">
                        <span class="rl-ws-item-name">{{ ws.name }}</span>
                        <span class="rl-ws-item-path">{{ ws.local_path }}</span>
                      </div>
                      <span v-if="ws.id === workspaceStore.activeWorkspaceId" class="rl-ws-item-check">
                        <Check :size="12" />
                      </span>
                      <button
                        class="rl-ws-item-unbind"
                        type="button"
                        aria-label="解绑工作区 {{ ws.name }}"
                        @click.stop="workspaceStore.unbind(ws.id)"
                        title="解绑"
                      >
                        <X :size="10" />
                      </button>
                    </div>
                    <div v-if="workspaceStore.workspaces.length === 0" class="rl-ws-dropdown-empty">
                      尚未绑定任何文件夹
                    </div>
                  </div>
                </div>
              </Transition>
            </div>
            <!-- 智能体召唤按钮 -->
            <div class="rl-agent-pick-wrap">
              <button class="rl-agent-pick-btn" :class="{ 'rl-agent-pick-active': showAgentPicker }" @click="toggleAgentPicker" title="选择智能体（快捷键 @）">
                <img src="/icons/智能体.svg" class="rl-agent-btn-icon" />
                <ChevronDown :size="10" />
              </button>
              <!-- 智能体选择下拉面板 -->
              <Transition name="rl-slide-up">
                <div v-if="showAgentPicker" class="rl-agent-dropdown" @click.stop>
                  <div class="rl-agent-dropdown-header">
                    <input
                      v-model="agentPickerFilter"
                      class="rl-agent-search"
                      placeholder="搜索智能体…"
                      @keydown.escape.stop="closeAgentPicker"
                      @keydown.enter.prevent="() => { const f = filteredAgents[0]; if (f) selectAgent(f) }"
                      ref="agentSearchRef"
                    />
                  </div>
                  <div class="rl-agent-dropdown-list">
                    <button
                      v-for="a in filteredAgents"
                      :key="a.agent_id"
                      class="rl-agent-dropdown-item"
                      :class="{ 'rl-agent-dropdown-selected': a.agent_id === selectedAgentId }"
                      @click="selectAgent(a)"
                    >
                      <img src="/icons/智能体.svg" class="rl-agent-item-svg" />
                      <div class="rl-agent-item-info">
                        <span class="rl-agent-item-role">{{ a.role || a.agent_id }}</span>
                        <span class="rl-agent-item-id">{{ a.agent_id }}</span>
                      </div>
                      <span v-if="a.agent_id === selectedAgentId" class="rl-agent-item-check">
                        <Check :size="12" />
                      </span>
                    </button>
                    <div v-if="filteredAgents.length === 0" class="rl-agent-dropdown-empty">
                      没有匹配的智能体
                    </div>
                  </div>
                  <div class="rl-agent-dropdown-footer">
                    <span class="rl-agent-dropdown-hint">输入 @ 快速召唤</span>
                  </div>
                </div>
              </Transition>
            </div>
            <!-- 技能召唤按钮 -->
            <div class="rl-skill-pick-wrap">
              <button class="rl-skill-pick-btn" :class="{ 'rl-skill-pick-active': showSkillPicker }" @click="toggleSkillPicker" title="选择技能（快捷键 /）">
                <span class="rl-skill-pick-slash">/</span>
                <ChevronDown :size="10" />
              </button>
              <!-- 技能选择下拉面板 -->
              <Transition name="rl-slide-up">
                <div v-if="showSkillPicker" class="rl-skill-dropdown" @click.stop>
                  <div class="rl-skill-dropdown-header">
                    <input
                      v-model="skillPickerFilter"
                      class="rl-skill-search"
                      placeholder="搜索技能…"
                      @keydown.escape.stop="closeSkillPicker"
                      @keydown.enter.prevent="() => { const f = filteredSkills[0]; if (f) insertSkillToInput(f) }"
                      ref="skillSearchRef"
                    />
                  </div>
                  <div class="rl-skill-dropdown-list">
                    <button
                      v-for="s in filteredSkills"
                      :key="s.node_type + '.' + s.name"
                      class="rl-skill-dropdown-item"
                      @click="insertSkillToInput(s)"
                    >
                      <span class="rl-skill-item-slash">/</span>
                      <div class="rl-skill-item-info">
                        <span class="rl-skill-item-name">{{ s.display_name }}</span>
                        <span class="rl-skill-item-desc">{{ s.description }}</span>
                      </div>
                      <span class="rl-skill-item-tag">{{ s.node_type }}</span>
                    </button>
                    <div v-if="filteredSkills.length === 0" class="rl-skill-dropdown-empty">
                      没有匹配的技能
                    </div>
                  </div>
                  <div class="rl-skill-dropdown-footer">
                    <span class="rl-skill-dropdown-hint">输入 / 快速召唤 · 选中后插入到输入框</span>
                  </div>
                </div>
              </Transition>
            </div>

            <!-- 自动放行切换按钮 -->
            <div class="rl-auto-approve-wrap">
              <button
                class="rl-auto-approve-btn"
                :class="{ 'rl-auto-approve-on': autoApproveEnabled }"
                @click="toggleAutoApprove"
                :title="autoApproveEnabled ? '工具调用自动放行中，点击切换为需要确认' : '工具调用需要确认，点击切换为自动放行'"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                <span class="rl-auto-approve-label">{{ autoApproveEnabled ? '自动放行' : '需确认' }}</span>
              </button>
            </div>
          </div>
          <div class="rl-trailing">
            <div class="rl-model-pick-wrap">
              <button class="rl-model-pill" :class="{ 'rl-model-pick-active': showModelPicker }" @click="toggleModelPicker" :title="(currentModelMeta?.name || currentModel || '未选择模型') + ' · ' + (currentModelMeta?.providerName || '')">
                <span class="rl-model-pill-icon" v-html="currentModelMeta?.icon || ''" />
                <span class="rl-model-pill-name">{{ currentModelMeta?.name || currentModel || '选择模型' }}</span>
                <ChevronDown :size="10" />
              </button>
              <Transition name="rl-model-pop">
                <div v-if="showModelPicker" class="rl-model-dropdown" @click.stop>
                  <div class="rl-model-dropdown-head">
                    <span class="rl-model-dropdown-title">选择模型</span>
                    <span class="rl-model-dropdown-count">{{ modelOptions.length }}</span>
                  </div>
                  <div class="rl-model-dropdown-body" @mouseleave="setHoveredModel('')">
                    <div v-if="modelOptions.length === 0" class="rl-model-empty">
                      <span class="rl-model-empty-title">暂无可用模型</span>
                      <span class="rl-model-empty-desc">请到「设置 → 模型配置」添加 API Key</span>
                    </div>
                    <button
                      v-for="m in modelOptions"
                      :key="m.id"
                      class="rl-model-dropdown-item"
                      :class="{ 'rl-model-dropdown-selected': m.id === currentModel }"
                      @mouseenter="setHoveredModel(m.id)"
                      @click="selectModel(m.id)"
                    >
                      <span class="rl-model-dropdown-icon" v-html="m.icon" />
                      <span class="rl-model-dropdown-name">{{ m.name }}</span>
                      <Check v-if="m.id === currentModel" :size="13" class="rl-model-check" />
                    </button>
                  </div>
                  <div class="rl-model-dropdown-foot">
                    <template v-if="hoveredModelMeta">
                      <span class="rl-model-foot-provider">{{ hoveredModelMeta.providerName }}</span>
                      <span class="rl-model-foot-desc">{{ hoveredModelMeta.description }}</span>
                    </template>
                    <span v-else class="rl-model-foot-hint">模型来自「设置 → 模型配置」</span>
                  </div>
                </div>
              </Transition>
            </div>
            <button v-if="!isStreaming || canTypeWhilePaused" class="rl-primary" :disabled="!inputText.trim() && pendingImageAssets.length === 0 && pendingVideoAssets.length === 0" @click="sendMessage" title="发送">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </button>
            <button v-else class="rl-stop" @click="stopStreaming" title="停止生成">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            </button>
          </div>
        </div>
      </div>

      <div class="rl-composer-footer" v-if="!heroMode && lastStats">
        <span class="rl-stats-line">
          {{ lastStats.turns }} 轮 · {{ lastStats.tokens }} tokens · {{ lastStats.latency }}ms
        </span>
      </div>

      <!--
        回到底部浮动钮：挂在输入卡座位内、跨在卡片上边缘。
        原来挂在滚动区里（sticky bottom:16px），正好压住最后一条消息的正文；
        移到这里后只用掉消息列底部留白 + 卡片顶部内边距，不遮挡任何文本，
        并且输入框随内容变高时按钮始终跟着卡片顶边走。
      -->
      <div v-if="showToBottom && !heroMode" class="rl-to-bottom-slot">
        <button class="rl-to-bottom" @click="isPinned = true; scrollToBottom(true)" title="回到底部">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
      </div>
    </div>

    <!--
      灵感模板区必须挂在「输入卡座位」之外：
      座位 .rl-composer-seat 是 flex:none 的定高块，内部再塞模板区无法吃掉剩余空间，
      只能固定高度或在小屏被裁。放到 .rl-chat 直接子级后，模板区可用 flex:1
      填满输入卡下方的剩余空间，标题与输入卡因此整体上移，竖向滚动浏览更多模板。
    -->
    <InspirationRail
      v-if="heroMode && !isStreaming && !contentMode"
      :intro-playing="heroIntroPlaying"
      @pick="applyStarter"
    />

    <ConfirmDialog
      :visible="dangerDialog.visible"
      :title="dangerDialog.title"
      :message="dangerDialog.message"
      confirm-text="确定重置"
      cancel-text="等待恢复"
      danger
      @confirm="onDangerConfirm"
      @cancel="onDangerCancel"
    />

  </div>

  <CatCompanion
    :show="!heroMode && crabVisible && pluginStore.isEnabled(CAT_PLUGIN_ID)"
    :input-rect="crabInputRect"
  />
  <SpongeBobCompanion
    :show="!heroMode && crabVisible && pluginStore.isEnabled(SPONGEBOB_PLUGIN_ID)"
    :input-rect="crabInputRect"
  />

  <!-- 螃蟹便签面板：内部 Teleport 到 body，放在这里只是为了让状态归属清晰 -->
  <CrabNotePanel
    :open="crabPanelOpen"
    :anchor="crabPanelAnchor"
    :theme="darkTheme ? 'dark' : 'light'"
    @close="crabPanelOpen = false"
    @apply="onCrabNoteApply"
  />

  <!-- 右侧上下文侧边栏 -->
  <div
    class="rl-ctx-slide"
    :class="{ 'rl-ctx-slide-open': ctxStore.sidebarVisible }"
  >
    <div
      class="rl-ctx-resize-bar"
      title="拖拽调整面板宽度 · 双击恢复默认"
      @mousedown="startCtxResize"
      @dblclick.prevent="resetCtxWidth"
    ></div>
    <ContextSidebar
      ref="contextSidebarRef"
      :class="{ 'is-dark': darkTheme }"
      @chat-action="onWorkChatAction"
    />
  </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, nextTick, onMounted, watch, onUnmounted, defineAsyncComponent, provide } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { usePluginStore } from '@/stores/plugin'
import { useWorkStore } from '@/stores/work'
import { useFileStore } from '@/stores/files'
import { useWorkspaceStore } from '@/stores/workspace'
import { useChatContextStore } from '@/stores/chatContext'
import AtContentPicker from './AtContentPicker.vue'

import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { renderMarkdown, renderThinkingText } from './markdown-renderer'
import type { ChatMessage, Conversation, ChatStats, AgentMeta, TurnPhase, StatusIndicator } from './cell-types'
import { provideChatRenderContext } from './chat-context'
import type { CrabApplyPayload } from './crabNoteRules'
import UserMessageCell from './UserMessageCell.vue'
import SideDigestMessageCell from './SideDigestMessageCell.vue'
import { isSideDigestMessage } from './cell-types'
import AssistantMessageCell from './AssistantMessageCell.vue'
import { useActiveThoughtStream } from './thoughtStream'
import { pendingConfirm, clearConfirmation, setConfirmViewSession } from './confirmRequest'
import ChatGuardianConfirmCard from './ChatGuardianConfirmCard.vue'
import ChatConfirmCard from './ChatConfirmCard.vue'
import ChatClarificationCard from './ChatClarificationCard.vue'
import ChatStoryboardConfirmCard from './ChatStoryboardConfirmCard.vue'
// CutPlanConfirmCard 的 dock 承接已删除（设计文档 3.4 #3）：
// 叠加方案确认卡改由 AssistantMessageCell 内的 VideoTaskCard 容器卡渲染。

const props = defineProps<{
  modelSettings?: Record<string, any>
  contentMode?: boolean
}>()

import { formatAgentOutput, AGENT_NODE_LABELS } from './agent-output-formatter'
import { useChatStream } from './composables/useChatStream'
import { useChatExpandable } from './composables/useChatExpandable'
import { useChatPickers } from './composables/useChatPickers'
import { useChatSSE, _ingestContentDelta } from './composables/useChatSSE'
import { getSessionRuntime, adoptSessionRuntime, abortSessionRuntime, DRAFT_RUNTIME_KEY } from './composables/chatSessionRuntime'
import { useChatActions } from './composables/useChatActions'
import { useChatInteractions } from './composables/useChatInteractions'
import { useNotificationSSE } from './composables/useNotificationSSE'
import { useChatWork } from './composables/useChatWork'
import { toTagList, isLastAssistant, getResultLineCount, renderDiff, escapeHtml } from './chat-helpers'
import { useChatSend } from './composables/useChatSend'
import { useChatInit } from './composables/useChatInit'
import { useChatAgent } from './composables/useChatAgent'
import { useChatCompanions } from './composables/useChatCompanions'
import { useChatTopicReference } from './composables/useChatTopicReference'
import { useChatImages } from './composables/useChatImages'
import { useChatVideos } from './composables/useChatVideos'
import { useChatHeroPrefs } from './composables/useChatHeroPrefs'
import { useGreeting, splitGreeting, stripGreeting, type GreetingSegment, type HeroIconKey } from './composables/useGreeting'
import { useChatMinimap } from './composables/useChatMinimap'
import { useChatHistorySync } from './composables/useChatHistorySync'
import { useCtxSidebar } from './composables/useCtxSidebar'

const emit = defineEmits<{
  'open-image-workspace': [draft?: any]
  'start-cover-workflow': [topic: string]
  'draft-panel-update': [data: any]
}>()
import { useChatHistory, markSessionRunning } from '@/composables/useChatHistory'
import { useSideSessionStore } from '@/stores/sideSession'
import { X, BarChart3, Lightbulb, Image as ImageIcon, File as FileIcon, FolderOpen, Music, Video, FileText, Code, FileSpreadsheet, ChevronDown, Check, Rocket, Sparkles, PanelRight, Scissors, Mic, Radio, ExternalLink, GitBranch } from 'lucide-vue-next'
import { agentsApi, type AgentSummary } from '@/api/agents'
import { authFetch, tryRefreshToken } from '@/api/client'
import * as chatSessionsApi from '@/api/chatSessions'

import { normalizeCardDraft, pickCoverUrl, pickImages } from '@/composables/cardDraft'
import CreationHandoffCard from './CreationHandoffCard.vue'
import InspirationRail from './InspirationRail.vue'

const authStore = useAuthStore()
const pluginStore = usePluginStore()
const workStore = useWorkStore()
const fileStore = useFileStore()
const workspaceStore = useWorkspaceStore()
const ctxStore = useChatContextStore()

// ── 侧会话面板（Task 7/8，PRD FR-E）────────────────────────
// 2026-11-03 改版：面板本身即迷你对话窗（面板内直接聊 + 内联历史），
// 不再切换整个聊天区到侧会话，openSideSession/backToMain/徽标条已废弃。
const SideSessionPanel = defineAsyncComponent(() => import('./SideSessionPanel.vue'))
const sideSessionStore = useSideSessionStore()
const sidePanelOpen = ref(false)

async function toggleSidePanel() {
  sidePanelOpen.value = !sidePanelOpen.value
  if (!sidePanelOpen.value) return
  sideSessionStore.setMainSession(activeConvId.value || sse.activeSessionId.value || null)
  await sideSessionStore.load()
}

/** 带回成功：切回主会话并强制重拉历史（摘要消息由后端落库） */
async function onBroughtBack(mainSessionId: string) {
  sideSessionStore.select(null)
  await switchToConversation(mainSessionId, mergeLoadedHistory)
}
const darkTheme = ref(false)
/* 「内容」Tab 模式：只切换 hero 区的呈现与问候语场景，
   作品列表、会话切换等既有逻辑一律不动。 */
const contentMode = computed(() => Boolean(props.contentMode))
const {
  title: greetingTitle,
  capabilities: greetingCapabilities,
} = useGreeting(contentMode)

/* 标题优先显示当前作品名；没有作品时才显示问候语。
   问候语里用方括号标了强调词，这里拆成片段让关键词单独上色。 */
const heroTitle = computed(() => activeWork.value?.title || stripGreeting(greetingTitle.value))
const heroTitleSegments = computed<GreetingSegment[]>(() =>
  activeWork.value?.title
    ? [{ text: activeWork.value.title, accent: false }]
    : splitGreeting(greetingTitle.value),
)

/* 能力/起手式图标映射：数据源只存语义 key，渲染层负责挑具体图标 */
const HERO_ICONS: Record<HeroIconKey, any> = {
  radar: Radio,
  topic: Lightbulb,
  note: ImageIcon,
  video: Scissors,
  publish: Rocket,
  review: BarChart3,
  diagnose: Sparkles,
  competitor: FileText,
}
function heroIcon(key: HeroIconKey) {
  return HERO_ICONS[key]
}

/* 起手式：把示例指令灌进输入框并聚焦，复用输入卡现有的自动撑高逻辑 */
function applyStarter(prompt: string) {
  inputText.value = prompt
  nextTick(() => {
    inputRef.value?.focus({ preventScroll: true })
    autoResize()
  })
}

const WorkDetailPanel = defineAsyncComponent(() => import('./WorkDetailPanel.vue'))
const ContextSidebar = defineAsyncComponent(() => import('./ContextSidebar.vue'))
const AgentProgressCard = defineAsyncComponent(() => import('./AgentProgressCard.vue'))

const contextSidebarRef = ref<InstanceType<typeof ContextSidebar> | null>(null)

function openExternalUrl(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

const { addConversation, touchConversation, updateTitle, getSessionId, findBySessionId, removeConversation, getConversationsForFolder, getConversationsForWork, loaded: chatHistoryLoaded } = useChatHistory()

const scrollBodyRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLTextAreaElement | null>(null)
const agentSearchRef = ref<HTMLInputElement | null>(null)
const skillSearchRef = ref<HTMLInputElement | null>(null)
const inputText = ref('')
const activeConvId = ref('')
const showAtContentPicker = ref(false)

// ── 域 composable（2026-10-18 ChatView 拆分）──
const {
  handoffOpen, onHandoffStart,
  CrabCompanion, CatCompanion, SpongeBobCompanion,
  CRAB_PLUGIN_ID, CAT_PLUGIN_ID, SPONGEBOB_PLUGIN_ID,
  crabVisible, crabInputRect, crabSeatRect,
} = useChatCompanions({ inputText, sendMessage })

/* ── 螃蟹便签面板 ──
   面板本体是 Teleport 到 body 的独立组件，这里只持有开合状态与锚点：
   锚点取螃蟹盒子的屏幕矩形，面板据此决定向上/向下展开。
   打开期间把 panelOpen 传给螃蟹，让它静默所有自动冒泡。 */
const CrabNotePanel = defineAsyncComponent(() => import('./CrabNotePanel.vue'))
const crabPanelOpen = ref(false)
const crabPanelAnchor = ref<{ left: number; top: number; right: number; bottom: number } | null>(null)

function onToggleCrabNotes(rect: { left: number; top: number; right: number; bottom: number }) {
  if (heroMode.value) return
  if (crabPanelOpen.value) {
    crabPanelOpen.value = false
    return
  }
  crabPanelAnchor.value = rect
  crabPanelOpen.value = true
}

async function onCrabNoteApply(payload: CrabApplyPayload) {
  crabPanelOpen.value = false
  // remind 的落地（写 last_done_at）由面板自己完成，这里不重复写库
  if (payload.action === 'remind') return
  inputText.value = payload.text
  await nextTick()
  onUserInput()
  inputRef.value?.focus()
  if (payload.action === 'send') await sendMessage()
}

watch(crabVisible, (v) => {
  if (!v) crabPanelOpen.value = false
})

const { topicReference, topicRefMetrics, topicRefPayload, readPendingTopicReference } = useChatTopicReference()

const {
  pendingImageAssets, imageUploadRef, uploadingImages,
  triggerImageUpload, onImageFilesSelected, onPasteImages, removePendingImage,
} = useChatImages()

const {
  pendingVideoAssets, videoUploadRef, uploadingVideos,
  triggerVideoUpload, onVideoFilesSelected, removePendingVideo,
} = useChatVideos()


// 「当前视图会话」的唯一标识：ChatView / useChatStream / useChatSSE 共用同一个 ref。
// 多任务并行改造后，流式状态按这个 sessionId 取各自的运行时，切会话只换投影目标。
const activeSessionId = ref<string | null>(null)

// 视图会话一变：① 确认卡 dock 换成该会话自己的那一张；
// ② 若刚才是草稿态在跑，把这回合的运行时认领到真实 sessionId 名下。
watch(activeSessionId, (sid) => {
  if (sid) adoptSessionRuntime(sid)
  setConfirmViewSession(sid)
}, { immediate: true })

const stream = useChatStream(() => activeSessionId.value)
const expand = useChatExpandable()
const pickers = useChatPickers()

const {
  isStreaming, streamingHasContent, streamingThinking, streamingClock,
  streamingMsg,
} = stream
const {
  isExecExpanded, toggleExec,
  isDiffExpanded, toggleDiff, expandedAgentId, toggleAgentExpand,
} = expand

const {
  selectedAgentId, selectedAgentLabel, showAgentPicker, showSkillPicker,
  showModelPicker, currentModel, filteredAgents, filteredSkills,
  agentPickerFilter, skillPickerFilter, showWorkspaceList, thinkingDepth,
  thinkingLabel, allSkills, availableAgents, modelOptions, currentModelMeta,
  hoveredModelMeta, setHoveredModel,
  selectAgent, clearSelectedAgent, toggleAgentPicker, closeAgentPicker,
  toggleSkillPicker, closeSkillPicker, toggleModelPicker, selectModel,
  cycleThinking, toggleWorkspaceList,
} = pickers

let _skillInsertGuard = false

function insertSkillToInput(skill: { node_type: string; name: string; display_name: string; description: string }) {
  _skillInsertGuard = true
  pickers.insertSkillToInput(skill, inputText, inputRef)
  setTimeout(() => { _skillInsertGuard = false }, 50)
}

function activateSkill(skill: { node_type: string; name: string; display_name?: string; description?: string }) {
  insertSkillToInput({
    node_type: skill.node_type,
    name: skill.name,
    display_name: skill.display_name || skill.name,
    description: skill.description || '',
  })
  nextTick(() => inputRef.value?.focus({ preventScroll: true }))
}

function activateAgent(agentId: string, retries = 8) {
  const agent = availableAgents.value.find(a => a.agent_id === agentId)
  if (!agent) {
    if (retries > 0) setTimeout(() => activateAgent(agentId, retries - 1), 200)
    return
  }
  selectAgent(agent)
  nextTick(() => inputRef.value?.focus({ preventScroll: true }))
}

const currentTurnPhase = ref<TurnPhase>('idle')
const currentStatusIndicator = ref<StatusIndicator>({ header: '' })

/**
 * 回合状态机的唯一写入口，按会话隔离。
 * 后台会话的转场只落它自己的运行时，绝不改写当前视图的回合状态；
 * 只有「正在看的那个会话」才同步刷新视图级 ref 与侧边栏运行标识。
 */
function _transitionTurnScoped(
  phase: TurnPhase,
  statusHeader: string | undefined,
  sessionId: string | null,
  msg?: ChatMessage | null,
) {
  const rt = getSessionRuntime(sessionId)
  rt.turnPhase.value = phase
  if (statusHeader !== undefined) {
    rt.statusIndicator.value = { header: statusHeader }
  }
  const target = msg || rt.streamingMsg.value
  if (target?.agentMeta) {
    target.agentMeta.turnPhase = phase
    if (statusHeader !== undefined) {
      target.agentMeta.statusIndicator = { header: statusHeader }
    }
  }
  if (sessionId && sessionId === activeSessionId.value) {
    currentTurnPhase.value = phase
    if (statusHeader !== undefined) {
      currentStatusIndicator.value = { header: statusHeader }
    }
  }
  // 侧边栏「正在执行」螃蟹标志（2026-11-02）：回合状态机是唯一闸口，
  // 流式/POST/续跑路径都经 transitionTurn 汇报状态。
  // running 且非 awaiting_*（等待澄清/确认不算在执行）才点亮；done/error/idle 熄灭。
  const _metaStatus = String(target?.agentMeta?.workflowStatus || '')
  const _busy = phase === 'running' && !_metaStatus.startsWith('awaiting_')
  markSessionRunning(sessionId, _busy)
}

function _transitionTurn(phase: TurnPhase, statusHeader?: string) {
  _transitionTurnScoped(phase, statusHeader, activeSessionId.value, null)
}

const isChatMode = computed(() => {
  return !heroMode.value
})

const isCodexMode = computed(() => {
  return false
})

// 产物隔离：会话 → 绑定作品。草稿只允许落到本会话自己的作品上。
function resolveWorkIdForSession(sid: string): string | null {
  const conv = conversations.value.find(c => c.id === sid)
  if (conv?._workId) return conv._workId
  const item = findBySessionId(sid)
  if (item?.workId) return item.workId
  const active = conversations.value.find(c => c.id === activeConvId.value)
  return active?._isDraft ? (active._workId || null) : null
}

const sse = useChatSSE({
  transitionTurn: _transitionTurn,
  transitionTurnScoped: _transitionTurnScoped,
  resolveWorkIdForSession,
  emit: (event: string, draft?: any, sessionId?: string | null) => {
    // 后台会话产出的草稿/图片只属于它自己：不能抢当前视图的面板
    if (sessionId && sessionId !== activeSessionId.value) return
    if (event === 'draft-panel-update') {
      emit('draft-panel-update', draft)
      return
    }
    emit(event as 'open-image-workspace', draft)
  },
  isCodexMode: () => isCodexMode.value,
  isChatMode: () => isChatMode.value,
  streamingThinking: stream.streamingThinking,
  streamingHasContent: stream.streamingHasContent,
  persistAssistantMessage: (content: string, agentMeta: any, sessionId?: string | null) => {
    const sid = sessionId || sse.activeSessionId.value
    if (sid) {
      chatSessionsApi.addMessage(sid, 'assistant', content, agentMeta).catch(() => {})
    }
  },
  onSseComplete: (sessionId?: string | null) => {
    getSessionRuntime(sessionId).notifySseComplete()
  },
  onContentReady: (sessionId?: string | null) => {
    getSessionRuntime(sessionId).notifySseComplete()
  },
  onStreamDelta: (sessionId?: string | null) => {
    getSessionRuntime(sessionId).touchStream()
    // 后台会话的 token 只刷新自己的空闲计时，不滚动当前视图
    if (sessionId && sessionId !== activeSessionId.value) return
    // Claude ca2ef848d：token 追加时图钉判定（同步 scrollTo，behavior:auto，
    // 不经 rAF 合并——帧率滞后本身就是抖动源）
    const el = scrollBodyRef.value
    if (!el) return
    if (performance.now() - lastUserInputAt <= COOL_DOWN_MS) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    if (dist <= 40) isPinned = true
    if (isPinned) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'auto' })
    }
  },
}, { activeSessionId })

let _scrollRafPending = false

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
  stopNotificationSSE()
})

function onDocumentClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (pickers.showAgentPicker.value && !target.closest('.rl-agent-pick-wrap')) {
    pickers.closeAgentPicker()
  }
  if (pickers.showSkillPicker.value && !target.closest('.rl-skill-pick-wrap')) {
    pickers.closeSkillPicker()
  }
  if (pickers.showModelPicker.value && !target.closest('.rl-model-pick-wrap')) {
    pickers.showModelPicker.value = false
  }
  if (pickers.showWorkspaceList.value && !target.closest('.rl-ws-pick-wrap')) {
    pickers.showWorkspaceList.value = false
  }
  // 螃蟹本身要排除：点击螃蟹的这次事件会先触发 toggle，若这里再关一次就等于没打开
  if (
    crabPanelOpen.value &&
    !target.closest('.cnp-root') &&
    !target.closest('.crab-companion')
  ) {
    crabPanelOpen.value = false
  }
}

const lastStats = ref<ChatStats | null>(null)

const conversations = ref<Conversation[]>([])

const heroMode = ref(true)

/* 品牌 Logo 彩蛋：hero 区的文字 logo 点一下原地变成螃蟹，再点变回文字。
   纯视觉交互，不参与会话状态；一旦离开 hero（开始对话）就复位，避免下次进来还顶着螃蟹。 */
const heroLogoCrab = ref(false)
const heroLogoPop = ref(false)

function toggleHeroLogo() {
  heroLogoCrab.value = !heroLogoCrab.value
  heroLogoPop.value = true
}

/* 弹跳只跑一次：动画结束就摘掉类名，下次点击才能重新触发 */
function onHeroLogoPopEnd(e: AnimationEvent) {
  if (e.animationName === 'rl-hero-logo-pop') heroLogoPop.value = false
}

watch(heroMode, (isHero) => {
  if (!isHero) heroLogoCrab.value = false
})

/* 新创作入场编排：输入卡先从底部升上来，随后 logo → 标题 → 徽标依次从 logo 位置弹出。
   只有「进入新创作」时才挂 is-hero-intro，动画播完即摘掉，避免普通重渲染重播。 */
const HERO_INTRO_TOTAL_MS = 3400
/* chatReady：历史会话加载完之前不渲染 hero 区和输入卡。
   刷新时 heroMode 初值是 true，若立刻渲染就会先按 hero 布局画一帧，
   等 init 把 heroMode 改成 false 再跳到 docked 位置 —— 这就是用户看到的「跳一下」。
   等 init 结束、heroMode 定稿后再一次性渲染，位置从第一帧就是对的。 */
const chatReady = ref(false)
/* 首次渲染时禁用 docked 入位动画：刷新后输入卡本来就该原地出现，
   从 translateY(40vh) 升上来属于多余的「跳」。 */
const suppressComposerAnim = ref(true)
const heroIntroPlaying = ref(false)
let _heroIntroTimer: number | null = null
function scheduleHeroIntroEnd() {
  if (_heroIntroTimer !== null) window.clearTimeout(_heroIntroTimer)
  _heroIntroTimer = window.setTimeout(() => {
    heroIntroPlaying.value = false
    _heroIntroTimer = null
  }, HERO_INTRO_TOTAL_MS)
}
function replayHeroIntro() {
  heroIntroPlaying.value = false
  nextTick(() => {
    requestAnimationFrame(() => {
      heroIntroPlaying.value = true
      scheduleHeroIntroEnd()
    })
  })
}

const actions = useChatActions({
  conversations,
  activeConvId,
  inputText,
  lastStats,
  heroMode,
  activeSessionId,
  scrollBodyRef,
  inputRef,
  resetExpandAll: () => expand.resetAll(),
})
const {
  newConversation, deleteConversation, switchToConversation,
  isSwitchingConv,
  switchToFolder, onSwitchConv, enterHeroMode, resetToInitial,
  ensureSession,
  scrollToBottom: actionsScrollToBottom, normalizeAgentMeta: actionsNormalizeAgentMeta,
} = actions

/* 对外的新创作入口：落库/切会话后补播入场动画。
   wasHero=true 说明 hero 元素已存在（空草稿复用），需要重启动画；
   wasHero=false 说明 hero 是新建的，预先挂标记让它创建即带入场动画。 */
async function newConversationWithIntro(folderId?: string, workId?: string) {
  const wasHero = heroMode.value
  if (!wasHero && chatReady.value) heroIntroPlaying.value = true
  await newConversation(folderId, workId)
  if (!heroMode.value || !chatReady.value) return
  if (wasHero) replayHeroIntro()
  else scheduleHeroIntroEnd()
}

const showToBottom = ref(false)
// ── 流式滚动图钉（照抄 Claude ca2ef848d 算法）──
// 150ms 冷却 + 实时贴底判定 + behavior:auto 即时对齐：
// 用户任何物理输入（wheel/touch/keydown/mousedown）→ 记时间戳并释放图钉，
// 150ms 冷却期内禁止程序滚动（绝不与手势争夺视口）；
// 冷却后每次 token 到达时实时判定：视口距底 ≤40px → 自动重新钉住。
const COOL_DOWN_MS = 150
let lastUserInputAt = 0
let isPinned = true
function markUserInput() {
  lastUserInputAt = performance.now()
  isPinned = false
}
function onUserWheel() {
  markUserInput()
}
function onTouchMove() {
  markUserInput()
}

const {
  autoApproveEnabled,
  toggleAutoApprove,
} = useChatHeroPrefs({
  setActiveWorkspace: (id: string) => workspaceStore.setActive(id),
  switchToFolder: (id: string) => switchToFolder(id),
})


async function onPickFolder() {
  pickers.showWorkspaceList.value = false
  const result = await workspaceStore.pickAndBind()
  if (result) {
    pickers.showWorkspaceList.value = false
  }
}

const { isLive: thoughtLive } = useActiveThoughtStream()

// ── 内置浏览器：正文链接点击 → 右侧抽屉渲染（不新开页面）──
const urlDrawer = reactive({ open: false, url: '', title: '' })
function onContentClickCapture(e: MouseEvent) {
  const a = (e.target as HTMLElement)?.closest?.('a[href]') as HTMLAnchorElement | null
  if (!a) return
  const href = a.getAttribute('href') || ''
  if (!/^https?:\/\//.test(href)) return
  e.preventDefault()
  e.stopPropagation()
  urlDrawer.url = href
  urlDrawer.title = (a.textContent || '').trim() || href
  urlDrawer.open = true
}

function stopActiveStreamImpl() {
  // 只停「当前视图会话」：其它会话在后台跑的任务不受影响（多任务并行）
  const sid = activeSessionId.value
  sse.chatSseController.value?.abort()
  sse.workflowSseController.value?.abort()
  // 显式传 sid（草稿态为 null）：只作废本会话的订阅，不能把后台会话的消息代也推上去
  if (sse.advanceMsgEpoch) sse.advanceMsgEpoch(sid)
  // 2026-11-05：notifySseComplete 在暂停态会被 pause-guard 拦下（isStreaming 恒
  // true），后续 sendMessageWithText 的补发链路会被 isStreaming 卡死——走逃生通道。
  stream.forceInterrupt()
}

async function onUserResend(text: string) {
  inputText.value = text
  await sendMessage()
}

function onDockAction(action: string, data?: any) {
  const req = pendingConfirm.req
  if (!req) return
  // 回写消息状态（各确认形态的 resolved 字段）
  const meta = req.msg.agentMeta
  if (meta) {
    if (req.kind === 'guardian' && (action === 'confirmed' || action === 'rejected')) meta.guardianResolved = action
    if (req.kind === 'storyboard' && (action === 'confirmed' || action === 'cancelled')) meta.storyboardResolved = action
    // cutplan 分支已删除（设计文档 3.4 #3）：该类型不再进 dock，
    // 决策回写由 AssistantMessageCell 的 onCutPlan* handler 负责。
  }
  if (req.onAction) req.onAction(action, data)
  // 只清本次决策的那一条：onAction 内若同步注册了新的确认卡（二次暂停），
  // 无条件清空会把它抹掉，用户就再也点不到任何东西。
  clearConfirmation(req)
}

/**
 * 暂停态：后端 ReAct 循环已 break 落盘，进程里没有任何协程在跑，
 * 只等用户决策。此时 isStreaming 仍为 true（为了保留"思考中"指示器），
 * 但输入框若跟着禁用，用户就连"继续"都发不出去 —— 界面看起来就是死机。
 */
const isPaused = computed(() => {
  const ws = stream.streamingMsg.value?.agentMeta?.workflowStatus
  if (ws === 'awaiting_confirmation'
    || ws === 'awaiting_clarification'
    || ws === 'awaiting_guardian_confirmation'
    || ws === 'awaiting_storyboard_confirmation'
    || ws === 'awaiting_cut_plan_confirmation') return true
  // 兜底：卡片已注册但消息已不在 streamingMsg 上（刷新/切会话后重建）时，
  // 仍按"暂停中"处理，避免输入框被 isStreaming 永久禁用。
  return !!pendingConfirm.req
})

/** 仅 Guardian 暂停支持"用一句话继续"（把文字作为补充说明随批准一起送后端） */
const canTypeWhilePaused = computed(() => isPaused.value && pendingConfirm.req?.kind === 'guardian')

/**
 * 暂停态下按发送：把输入的文字作为 feedback 连同批准一起提交，
 * 让 LLM 带着"用户补充说明 + 真实执行结果"续跑。
 * 不走新回合——新回合会开一条全新 turn，把暂停中的循环变成无人认领的孤儿。
 */
async function resumeGuardianWithFeedback(feedback: string) {
  const req = pendingConfirm.req
  if (!req || req.kind !== 'guardian') return false
  const meta = req.msg.agentMeta as any
  // 先乐观把流状态接回运行，避免续跑事件到达时 UI 还停在"暂停"态
  if (meta) meta.workflowStatus = 'running'
  stream.resumeFromPause()
  try {
    const res = await authFetch('/api/v1/chat/confirm-guardian', {
      method: 'POST',
      body: JSON.stringify({
        session_id: req.sessionId,
        loop_state_path: req.loopStatePath,
        action: 'approve',
        feedback,
        turn_id: req.turnId || null,
      }),
    })
    if (!res.ok) {
      const errBody = await res.text()
      console.error('[ChatView] resume with feedback failed:', res.status, errBody)
      // 失败不吞文字：原样退回输入框，用户可以改点「批准执行」或重新组织语言
      if (meta) meta.workflowStatus = 'awaiting_guardian_confirmation'
      inputText.value = feedback
      return true
    }
    inputText.value = ''
    onDockAction('confirmed')
    return true
  } catch (err) {
    console.error('[ChatView] resume with feedback error:', err)
    if (meta) meta.workflowStatus = 'awaiting_guardian_confirmation'
    inputText.value = feedback
    return true
  }
}
const currentMessages = computed(() => {
  const conv = conversations.value.find(c => c.id === activeConvId.value)
  return conv ? conv.messages.filter(m => m.role !== 'system') : []
})

const sendMessageRef = ref<() => Promise<void>>(async () => {})

const interactions = useChatInteractions({
  conversations,
  activeConvId,
  inputText,
  activeSessionId: sse.activeSessionId,
  // 交互按「消息所属会话」解析运行时，切会话后点批准也不会改到别人的状态
  getSessionId,
  subscribeChatSSE: (sessionId: string, assistantMsg: ChatMessage, retryCount?: number, turnId?: string, skipStartGate?: boolean) => sse.subscribeChatSSE(sessionId, assistantMsg, retryCount ?? 0, turnId, skipStartGate),
  sendMessageRef,
  // 重试上一条消息时，把它带的图片放回待选区，重发才不会丢 image_asset_ids
  restorePendingImages: (atts) => {
    pendingImageAssets.value = atts.map(a => ({
      asset_id: a.asset_id,
      filename: a.filename || '',
      content_type: a.content_type || '',
      file_size: 0,
      width: a.width ?? null,
      height: a.height ?? null,
      original_url: a.original_url || '',
      thumbnail_url: a.thumbnail_url || '',
      source: 'user_upload',
    }))
  },
  // 重试上一条消息时，把它带的选题参考还原回参考条，重发才会重新注入
  restoreTopicReference: (ref) => {
    topicReference.value = ref
  },
})
const {
  onChatConfirmed, onChatClarified, onConfirmationExpired, confirmAction,
  stopStreaming, retryLastMessage,
  dangerDialog, showDangerConfirm, onDangerConfirm, onDangerCancel,
  copyMessage,
} = interactions

const chatWork = useChatWork({
  inputText,
  isStreaming: stream.isStreaming,
  stopStreaming,
  sendMessageRef,
  emitStartCoverWorkflow: (topic: string) => emit('start-cover-workflow', topic),
})
const {
  activeWork, showWorkDetail, detailWidth,
  startDetailResize, resetDetailWidth, exitWorkContext, onWorkChatAction, getFileIconComponent,
} = chatWork

const { startCtxResize, resetCtxWidth } = useCtxSidebar(ctxStore)


function toggleWorkDetail() {
  if (ctxStore.sidebarVisible) {
    ctxStore.setSidebarVisible(false)
  } else {
    ctxStore.setSidebarVisible(true)
  }
  showWorkDetail.value = ctxStore.sidebarVisible
}

const chatSend = useChatSend({
  activeWork,
  workStore,
  currentModel: pickers.currentModel,
  thinkingDepth: pickers.thinkingDepth,
  scrollToBottom: async () => scrollToBottom(),
  transitionTurn: (phase: string, label?: string) => _transitionTurn(phase as any, label || ''),
  transitionTurnScoped: _transitionTurnScoped,

  lastStats,
  // 渲染层 L3：chat 直连路径的 delta 也灌进同一个播放引擎（问题 1：两条流式路径速率一致）
  pushStreamDelta: (msg: ChatMessage, delta: string) => sse.pushStreamDelta?.(msg, delta),
  pushReasoningDelta: (msg: ChatMessage, delta: string) => sse.pushReasoningDelta?.(msg, delta),
  finishStream: (msg: ChatMessage, onDone?: () => void) => sse.finishStream?.(msg, onDone),
})
const { sendMessageReal } = chatSend


function autoResize() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 336) + 'px'
}

provideChatRenderContext({
  isChatMode,
  isCodexMode,
  isStreaming,
  streamingMsg,
  streamingThinking,
  streamingHasContent,
  streamingClock,
  currentStatusIndicator,
  isExecExpanded,
  toggleExec,
  isDiffExpanded,
  toggleDiff,
  isThinkingExpanded: (idx: number) => expand.isThinkingExpanded(idx),
  toggleThinking: expand.toggleThinking,
  expandedAgentId,
  toggleAgentExpand,
  copyMessage,
  retryLastMessage,
  onChatConfirmed,
  onChatClarified,
  onConfirmationExpired,
  sendMessage,
  // 卡片需要"带着一段指令回灌输入框再发送"（如"让 AI 重编分镜"）。
  // sendMessage 本身不收参数、且正在流式时会直接 return，所以在这里补一层：
  // 先停掉残留流，再写 inputText，等 DOM 更新后走正常发送链路。
  stopActiveStream: stopActiveStreamImpl,
  sendMessageWithText: async (text: string) => {
    const body = String(text || '').trim()
    if (!body) return
    if (stream.isStreaming.value) {
      stopStreaming()
      await new Promise(resolve => setTimeout(resolve, 400))
    }
    inputText.value = body
    await nextTick()
    await sendMessage()
  },
  openInBrowser: (url: string) => { ctxStore.setSidebarVisible(true); contextSidebarRef.value?.navigateToUrl(url) },
  // 卡片组「侧边栏预览」：在右侧 ContextSidebar 以作品详情形态打开（非临时抽屉）
  openCardSidebar: (workId: string) => { ctxStore.setSidebarVisible(true); ctxStore.linkWork(workId) },
})

function onUserInput() {
  autoResize()
  const text = inputText.value
  const cursorPos = inputRef.value?.selectionStart ?? text.length
  const before = text.slice(0, cursorPos)
  const atMatch = before.match(/@([^@\s]*)$/)
  if (atMatch) {
    showAtContentPicker.value = true
  } else {
    showAtContentPicker.value = false
  }

  const slashMatch = before.match(/\/([^\s]*)$/)
  if (slashMatch && !_skillInsertGuard) {
    const beforeSlash = before.slice(0, before.lastIndexOf('/'))
    if (!beforeSlash.match(/https?:$/)) {
      if (!showSkillPicker.value) {
        showSkillPicker.value = true
      }
      skillPickerFilter.value = slashMatch[1] || ''
    } else {
      if (showSkillPicker.value) {
        showSkillPicker.value = false
        skillPickerFilter.value = ''
      }
    }
  } else {
    if (showSkillPicker.value) {
      showSkillPicker.value = false
      skillPickerFilter.value = ''
    }
  }
}

function onAtContentSelect(type: string, data: any) {
  showAtContentPicker.value = false
  if (type === 'agent') {
    selectAgent(data)
    const atMatch = inputText.value.match(/@([^@\s]*)$/)
    if (atMatch) {
      inputText.value = inputText.value.replace(/@([^@\s]*)$/, '')
    }
    return
  }
  if (type === 'work') {
    ctxStore.linkWork(data.id)
  } else if (type === 'memory') {
    ctxStore.addStyleMemory({ content: data.content, source: data.source || 'user-set' })
  } else if (type === 'hot') {
    ctxStore.addContextItem({
      type: 'analysis',
      label: data.type === 'trending' ? '当前热点趋势' : '热门话题',
      summary: '实时数据',
      pinned: true,
      meta: { refId: `hot-${data.type}`, hotType: data.type },
    })
  } else if (type === 'rule') {
    ctxStore.addContextItem({
      type: 'rule',
      label: data.type === 'xhs_rule' ? '小红书创作规范' : '抖音创作规范',
      summary: '平台规范',
      pinned: true,
      meta: { refId: `rule-${data.type}`, ruleName: data.type === 'xhs_rule' ? '小红书创作规范' : '抖音创作规范' },
    })
  }
  const atMatch = inputText.value.match(/@([^@\s]*)$/)
  if (atMatch) {
    inputText.value = inputText.value.replace(/@([^@\s]*)$/, '')
  }
}

function onScroll() {
  const el = scrollBodyRef.value
  if (!el) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  showToBottom.value = !isPinned && dist > 120
  updateMinimapActive()
}

const {
  minimapUserIndices, minimapDots, minimapActiveIndex, minimapHoveredIndex,
  updateMinimapActive, minimapScrollTo,
  minimapCanvasRef, initMinimapCanvas, destroyMinimapCanvas, triggerMinimapDisperse,
  getParticleIndexAt, getParticleY,
} = useChatMinimap({ currentMessages, scrollBodyRef })

const minimapContainerRef = ref<HTMLElement | null>(null)

function onMinimapCanvasClick(e: MouseEvent) {
  const canvas = minimapCanvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const clickX = e.clientX - rect.left
  const clickY = e.clientY - rect.top
  const idx = getParticleIndexAt(clickX, clickY)
  if (idx >= 0) {
    minimapScrollTo(idx)
  }
}

const minimapHoveredPreview = computed(() => {
  const idx = minimapHoveredIndex.value
  if (idx < 0 || idx >= minimapDots.value.length) return ''
  return minimapDots.value[idx].preview
})

const minimapHoveredY = computed(() => {
  return getParticleY(minimapHoveredIndex.value)
})

function onMinimapCanvasMouseMove(e: MouseEvent) {
  const canvas = minimapCanvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  minimapHoveredIndex.value = getParticleIndexAt(x, y)
}

function onMinimapCanvasMouseLeave() {
  minimapHoveredIndex.value = -1
}


const scrollToBottom = actionsScrollToBottom

// 2026-11-04：确认/澄清 dock 弹出时把消息区滚到底。dock 在普通流里占高度，
// 高卡（多问澄清/分镜）会把滚动区压矮，回复尾巴被压到折叠线下——
// 看起来就像被弹窗遮挡。弹卡瞬间滚一次，让回复尾 + 卡片同屏。
watch(() => pendingConfirm.req, (req) => {
  if (!req) return
  // 双 nextTick：等 dock 卡挂载撑完高度，再滚才有正确的 scrollHeight
  nextTick(() => nextTick(() => { void scrollToBottom() }))
})

watch([() => minimapDots.value.length, heroMode], ([dotCount, isHero]) => {
  if (dotCount > 1 && !isHero) {
    nextTick(() => {
      const canvas = minimapCanvasRef.value
      if (canvas) initMinimapCanvas(canvas)
    })
  }
})

function onKeyDown(e: KeyboardEvent) {
  markUserInput()
  if (e.key === 'Escape') {
    if (pickers.showAgentPicker.value) {
      pickers.closeAgentPicker()
      return
    }
    if (pickers.showSkillPicker.value) {
      pickers.closeSkillPicker()
      return
    }
    if (pickers.showModelPicker.value) {
      pickers.showModelPicker.value = false
      return
    }
    if (sse.workflowSseController.value) {
      sse.workflowSseController.value.abort()
      sse.workflowSseController.value = null
      return
    }
    if (stream.isStreaming.value) {
      stream.abort()
      return
    }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    if (pickers.showAgentPicker.value) {
      e.preventDefault()
      const first = pickers.filteredAgents.value[0]
      if (first) pickers.selectAgent(first)
      return
    }
    if (pickers.showSkillPicker.value) {
      e.preventDefault()
      const first = pickers.filteredSkills.value[0]
      if (first) pickers.insertSkillToInput(first, inputText, inputRef)
      return
    }
    e.preventDefault()
    sendMessage()
  }
  if (e.key === '@' && !pickers.showAgentPicker.value && !pickers.showSkillPicker.value && !showAtContentPicker.value && inputText.value === '') {
    e.preventDefault()
    pickers.toggleAgentPicker()
  }
  if (e.key === '/' && !pickers.showSkillPicker.value && !pickers.showAgentPicker.value && !showAtContentPicker.value && inputText.value === '') {
    e.preventDefault()
    pickers.toggleSkillPicker()
  }
}

async function sendMessage() {
  const rawText = inputText.value.trim()
  // 暂停态优先走"续跑"：此时后端循环已停、只在等决策，开新回合会让
  // 暂停的循环变成无人认领的孤儿（客户端不再订阅它的 loop_state）。
  if (isPaused.value) {
    if (!rawText) return
    const handled = await resumeGuardianWithFeedback(rawText)
    if (handled) return
    if (pendingConfirm.req) {
      // 澄清/技能确认/分镜：只认卡片上的按钮。文字保留在输入框，不被吞掉。
      console.warn('[ChatView] paused; please use the card above to confirm', stream.streamingMsg.value?.agentMeta?.workflowStatus)
      return
    }
    // 暂停态但没有任何确认卡（卡片注册失败/已过期）：先把卡死的流状态收尾，
    // 退化为新回合。宁可丢掉那轮暂停的循环，也不能让界面永久锁死。
    console.warn('[ChatView] paused without confirmation card; forcing reset and sending as new turn')
    const _stuckMeta = stream.streamingMsg.value?.agentMeta as any
    if (_stuckMeta && String(_stuckMeta.workflowStatus || '').startsWith('awaiting_')) {
      _stuckMeta.workflowStatus = 'error'
    }
    stream.resetStreamState()
  }
  const attachedIds = pendingImageAssets.value.map(asset => asset.asset_id)
  const attachedVideoIds = pendingVideoAssets.value.map(asset => asset.asset_id)
  // 纯图片/视频也能发送（不发文字时正文只剩素材声明）
  if ((!rawText && attachedIds.length === 0 && attachedVideoIds.length === 0) || uploadingImages.value || uploadingVideos.value) return
  // 纯图片/视频消息没有文字可当标题，给个可辨认的占位；选题参考可当末位兜底
  const titleSeed = rawText || (attachedIds.length ? `图片素材 ×${attachedIds.length}` : (attachedVideoIds.length ? `视频素材 ×${attachedVideoIds.length}` : (topicRefPayload.value?.title || '')))
  // 发给后端的正文仍需带 asset_ids 提示（LLM 要靠它优先使用用户图片/视频），
  // 但这行提示不该出现在气泡里——渲染时由 stripAssetHint 剥掉，
  // 气泡改由 msg.attachments 展示真实缩略图。
  const assetHint = attachedIds.length
    ? `[本次配图素材] 使用结构化图文卡片能力，asset_ids=${JSON.stringify(attachedIds)}；这些是我已上传的图片，请优先使用，不要重新生成。`
    : ''
  const videoAssetHint = attachedVideoIds.length
    ? `[本次视频素材] video_asset_ids=${JSON.stringify(attachedVideoIds)}；这些是我已上传的视频，可直接用于智能剪辑或叠加增强。`
    : ''
  // 选题参考同理：从选题池带入的热点数据是给模型的指令行，不进气泡（stripTopicHint 剥掉）
  const topicHint = topicRefPayload.value
    ? `[选题参考] ${JSON.stringify(topicRefPayload.value)}；请围绕该热点选题进行创作，优先使用参考中的数据与链接。`
    : ''
  const hints = [assetHint, videoAssetHint, topicHint].filter(Boolean).join('\n\n')
  const text = rawText ? (hints ? `${rawText}\n\n${hints}` : rawText) : hints
  // 图片随消息一起"发出去"：快照一份挂在消息上，发送后立即清空输入框待选区。
  const attachedAssets = pendingImageAssets.value.map(a => ({
    asset_id: a.asset_id,
    filename: a.filename,
    content_type: a.content_type,
    original_url: a.original_url,
    thumbnail_url: a.thumbnail_url,
    width: a.width,
    height: a.height,
  }))
  if (stream.isStreaming.value) {
    return
  }
  stream.isStreaming.value = true
  inputText.value = ''
  if (!chatHistoryLoaded.value) {
    const start = Date.now()
    while (!chatHistoryLoaded.value && Date.now() - start < 15000) {
      await new Promise(resolve => setTimeout(resolve, 120))
    }
    if (!chatHistoryLoaded.value) {
      // 旧实现在这里静默 return——历史加载一旦超时（后端卡顿/网络慢时很容易发生），
      // 用户的发送就凭空消失：没报错、没弹窗、没反应。这是「点了发送没动静」的根因。
      // 历史加载只影响展示连续性，不阻塞发送。
      console.warn('[ChatAgent] 历史会话加载超时（15s），跳过等待直接发送')
    }
  }

  try {
    const circuitStatus = await chatSessionsApi.getLLMCircuitStatus()
    if (circuitStatus.state === 'open' || circuitStatus.state === 'half_open') {
      const probeResult = await chatSessionsApi.probeLLMCircuit()
      if (probeResult.recovered) {
        console.info('[ChatAgent] LLM circuit auto-recovered via probe')
      } else {
        const shouldReset = await showDangerConfirm(
          'LLM 服务不可用',
          `LLM API 当前不可用（${circuitStatus.open_reason || circuitStatus.state}）\n\n` +
          `探针结果：${probeResult.detail}\n\n` +
          `如果您已充值或更换了 API Key，点击"确定"强制重置后重试。\n` +
          `点击"取消"等待自动恢复（每 ${Math.round(circuitStatus.recovery_timeout)}s 自动探针一次）。`
        )
        if (shouldReset) {
          try {
            await chatSessionsApi.resetLLMCircuit()
          } catch {}
        } else {
          stream.isStreaming.value = false
          return
        }
      }
    }
  } catch {}

  let conv = conversations.value.find(c => c.id === activeConvId.value)
  if (!conv) {
    await newConversation()
    conv = conversations.value.find(c => c.id === activeConvId.value)
    if (!conv) {
      conv = conversations.value[0]
    }
  }
  if (!conv) {
    stream.isStreaming.value = false
    return
  }

  // 会话惰性落库：只有用户真的发出请求，草稿才转成后端 session 并进入历史列表。
  // 顺序必须在 push user 消息之前——落库后 conv.id 会换成真实 sessionId，
  // 后面 updateTitle / 后端 updateSession 才能对得上。
  let ensuredSid: string | null = null
  try {
    ensuredSid = await ensureSession(
      conv.messages.filter(m => m.role === 'user').length === 0
        ? (titleSeed.length > 200 ? titleSeed.slice(0, 200) + '…' : titleSeed)
        : undefined,
    )
  } catch {
    ensuredSid = null
  }

  conv.messages.push({
    role: 'user',
    content: text,
    timestamp: Date.now(),
    attachments: attachedAssets.length > 0 ? attachedAssets : undefined,
  })
  touchConversation(conv.id)
  // 输入框待选区立刻清空：图片已经"随消息发出"，不再挂在输入框里
  pendingImageAssets.value = []
  pendingVideoAssets.value = []
  // 选题参考同理：已随该条消息注入给模型，参考条即时摘除
  topicReference.value = null
  if (conv.messages.filter(m => m.role === 'user').length === 1) {
    conv.title = titleSeed.length > 200 ? titleSeed.slice(0, 200) + '…' : titleSeed
    updateTitle(conv.id, conv.title)
    const sid = ensuredSid || sse.activeSessionId.value || getSessionId(conv.id)
    if (sid) {
      chatSessionsApi.updateSession(sid, conv.title).catch(() => {})
    }
  }

  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }

  heroMode.value = false
  triggerMinimapDisperse()
  crabVisible.value = true
  // 本回合的运行时快照（草稿态运行时会整体搬到真实 sessionId 名下，对象不变）
  const _turnRt = getSessionRuntime(sse.activeSessionId.value)
  stream.streamingHasContent.value = false
  stream.streamingThinking.value = false
  stream.startClock()
  stream.createAbortController()
  stream.setBeforeFinish(() => {
    // 同样只看本回合自己的运行时：切会话后不能去 flush 别人的消息
    const msg = _turnRt.streamingMsg.value
    if (msg) sse.flushStreamDelta(msg)
  })
  stream.beginStream((r) => {
    // 收尾只作用于「这一回合自己的运行时」（r 由运行时回调传入）：
    // 用户中途切到别的会话时，不能顺手把别人的流式状态和回合状态清掉。
    r.resetStreamState()
    const sid = r.key === DRAFT_RUNTIME_KEY ? null : r.key
    _transitionTurnScoped('idle', '', sid, null)
    r.stopClock()
    // 只有还在看这个会话时才滚动，后台会话收尾不打断用户当前阅读
    if (sid === activeSessionId.value || (!sid && !activeSessionId.value)) scrollToBottom()
  })
  await scrollToBottom()

  const startTime = Date.now()

  try {
    // ⚠️ 必须用发送前快照的 attachedIds：pendingImageAssets 为了让图片"随消息
    // 发出"已经在前面清空了，这里再读一次会拿到空数组，后端就收不到
    // image_asset_ids（正是用户反馈"后端仍能拿到图片 id"依赖的那条链路）。
    await sendAgentMessage(conv, text, startTime, attachedIds, attachedVideoIds)
  } catch (err: any) {
    // 兜底防线：任何来源的错误文案都不允许把裸 JSON / 对象序列化带进回复文本。
    // HTTP 失败分支已由 parseHttpErrorMessage 解析（见 useChatAgent/useChatSend），
    // 这里再挡一层，防网络异常、abort 等其它 throw 路径。
    const friendlyErrText = (() => {
      const m = typeof err?.message === 'string' ? err.message.trim() : ''
      if (!m || m.startsWith('{') || m.startsWith('[') || m.includes('[object Object]')) {
        return '未知错误'
      }
      return m.length > 200 ? `${m.slice(0, 200)}…` : m
    })()
    const errText = `请求失败: ${friendlyErrText}`
    const lastMsg = conv.messages[conv.messages.length - 1]
    if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
      lastMsg.content = errText
      lastMsg.isError = true
    } else {
      conv.messages.push({
        role: 'assistant',
        content: errText,
        isError: true,
      })
    }
    const errSid = sse.activeSessionId.value || getSessionId(conv.id)
    if (errSid) {
      chatSessionsApi.addMessage(errSid, 'assistant', errText).catch(() => {})
    }
    _turnRt.notifyImmediateComplete()
  } finally {
    if (_turnRt.isStreaming.value) {
      // 暂停等待用户确认（clarification/guardian/storyboard）时不调
      // notifyAgentLoopDone：它会设 10s 定时器，到期后强制 _finishStream，
      // 但暂停可能持续远超 10s（等用户操作），且确认后续跑也可能超过 10s。
      // 暂停时 SSE 流保持心跳，终态事件（workflow_completed）才是真正的结束信号。
      const _msg = _turnRt.streamingMsg.value
      const _ws = _msg?.agentMeta?.workflowStatus
      const _isPaused = _ws === 'awaiting_clarification'
        || _ws === 'awaiting_confirmation'
        || _ws === 'awaiting_guardian_confirmation'
        || _ws === 'awaiting_storyboard_confirmation'
        || _ws === 'awaiting_cut_plan_confirmation'
      if (!_isPaused) {
        _turnRt.notifyAgentLoopDone(10_000)
      }
    }
  }
}

sendMessageRef.value = sendMessage

const historySync = useChatHistorySync({ normalizeAgentMeta: actionsNormalizeAgentMeta })
const { mergeLoadedHistory } = historySync


const notificationSSE = useNotificationSSE()
const startNotificationSSE = notificationSSE.start
const stopNotificationSSE = notificationSSE.stop

const _watcherSuppressed = ref(false)
function suppressWatcher() { _watcherSuppressed.value = true }
function releaseWatcher() { _watcherSuppressed.value = false }

const chatInit = useChatInit({
  conversations,
  activeConvId,
  heroMode,
  chatHistoryLoaded,
  crabVisible,
  sse,
  fileStore,
  pluginStore,
  workspaceStore,
  pickers,
  addConversation,
  mergeLoadedHistory,
  startNotificationSSE,
  onDocumentClick,
  CRAB_PLUGIN_ID,
  suppressWatcher,
  releaseWatcher,
})

/* chatReady 定稿：只执行一次。
   init 正常结束时调用；后端响应慢时由 800ms 兜底计时器调用，避免一直白屏。 */
let _chatReadyDone = false
let _chatReadyFallback: number | null = null
function markChatReady() {
  if (_chatReadyDone) return
  _chatReadyDone = true
  if (_chatReadyFallback !== null) {
    window.clearTimeout(_chatReadyFallback)
    _chatReadyFallback = null
  }
  heroIntroPlaying.value = heroMode.value
  chatReady.value = true
  if (heroMode.value) {
    scheduleHeroIntroEnd()
  }
  // 首帧渲染完成后再放开 docked 入位动画，之后从 hero 收起到 docked 才有动画
  nextTick(() => {
    requestAnimationFrame(() => { suppressComposerAnim.value = false })
  })
}

onMounted(async () => {
  readPendingTopicReference()
  window.addEventListener('chat:fill-input', onFillInput as EventListener)

  // 兜底：后端迟迟不返回时最多等 800ms 就先按当前状态渲染
  _chatReadyFallback = window.setTimeout(markChatReady, 800)

  await chatInit.init()

  // init 结束后 heroMode 才定稿，此刻一次性渲染，避免先画错布局再跳回
  markChatReady()
})

onUnmounted(() => {
  window.removeEventListener('chat:fill-input', onFillInput as EventListener)
  destroyMinimapCanvas()
  if (_heroIntroTimer !== null) {
    window.clearTimeout(_heroIntroTimer)
    _heroIntroTimer = null
  }
  if (_chatReadyFallback !== null) {
    window.clearTimeout(_chatReadyFallback)
    _chatReadyFallback = null
  }
})

function onFillInput(event: Event) {
  const detail = (event as CustomEvent<{ text?: string }>).detail
  const text = String(detail?.text || '').trim()
  if (!text) return
  inputText.value = text
  nextTick(() => { inputRef.value?.focus({ preventScroll: true }); autoResize() })
}

watch(activeConvId, async (newId) => {
  if (!newId) return
  // 确认卡按会话存放：切会话只是把 dock 的投影换成目标会话自己的卡，
  // 后台会话若正在等确认，它的卡会留在注册表里，切回去就能接着点。
  setConfirmViewSession(null)
  if (isSwitchingConv() || _watcherSuppressed.value) return
  const conv = conversations.value.find(c => c.id === newId)
  // 草稿会话尚未落库：后端没有对应记录，别拿 draft_xxx 去查消息/文件
  if (conv?._isDraft) {
    sse.activeSessionId.value = null
    heroMode.value = conv.messages.length === 0
    void scrollToBottom()
    return
  }
  if (conv && conv.messages.length === 0) {
    heroMode.value = true
    const sid = getSessionId(newId) || newId
    sse.activeSessionId.value = sid
    try {
      const msgs = await chatSessionsApi.listMessages(sid)
      mergeLoadedHistory(conv, msgs)
      if (conv.messages.length > 0) heroMode.value = false
    } catch {
      // failed to load messages
    }
  } else {
    heroMode.value = false
  }
  const sid = getSessionId(newId) || newId
  sse.activeSessionId.value = sid
  await fileStore.loadFilesForSession(sid)
  void scrollToBottom()
})

watch(pickers.showAgentPicker, (val) => {
  if (val) {
    nextTick(() => {
      agentSearchRef.value?.focus()
    })
  }
})

watch(pickers.showSkillPicker, (val) => {
  if (val) {
    nextTick(() => {
      skillSearchRef.value?.focus()
    })
  }
})

const activeFile = computed(() => fileStore.activeFile)
const creativePhase = ref<string>('idle')

const chatAgent = useChatAgent({
  activeWork,
  activeFile,
  workStore,
  fileStore,
  workspaceStore,
  pickers,
  sse,
  stream,
  showWorkDetail,
  creativePhase,
  modelSettings: props.modelSettings,
  autoApproveEnabled,
  sendMessageReal,
  ensureSession,
  resolveWorkIdForSession,
  // 兜底：后端返回 session_id 时把仍处于草稿态的会话对上号（进历史、不再丢消息）
  linkSession: (sid: string) => {
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    if (conv && conv._isDraft) {
      conv.id = sid
      conv._isDraft = false
      activeConvId.value = sid
      addConversation(sid, conv.title || '新会话', conv._folderId || 'chat-files', sid, conv._workId ?? null)
    }
    sse.activeSessionId.value = sid
  },
  transitionTurn: (phase: string, label?: string) => _transitionTurn(phase as any, label || ''),
  transitionTurnScoped: _transitionTurnScoped,
})
const { sendAgentMessage } = chatAgent

// 产物隔离：切到别的会话时，若当前打开的作品属于原会话，则解绑，
// 避免把 A 会话的草稿/产物带到 B 会话的工作区里继续编辑。
watch(activeConvId, (newId) => {
  const work = activeWork.value
  if (!work || !newId) return
  // 新会话本身就绑定这个作品（点作品自动跳转到它的会话）时保留
  if (resolveWorkIdForSession(newId) === work.id) return
  if (work.sessionId && work.sessionId !== newId) {
    workStore.setActiveWork(null)
  }
})

watch(activeWork, async (newWork, oldWork) => {
  if (newWork) {
    showWorkDetail.value = true
    ctxStore.setSidebarVisible(true)
    ctxStore.linkWork(newWork.id)
    if (newWork.id !== oldWork?.id) {
      await switchToWorkConversation(newWork.id)
    }
  } else {
    showWorkDetail.value = false
  }
})

async function switchToWorkConversation(workId: string) {
  // 作品已有会话时必须先打开它——空草稿复用只留给「该作品还没有任何会话」的场景。
  // 之前把草稿复用放在最前面，点作品会停在一个空草稿上，看起来像没打开会话。
  const workConvs = getConversationsForWork(workId)
  if (workConvs.length > 0) {
    const latest = workConvs.reduce((a, b) => ((b.updatedAt || b.createdAt) > (a.updatedAt || a.createdAt) ? b : a))
    await switchToConversation(latest.id, mergeLoadedHistory)
    return
  }
  // 内存缓存未命中（如页面刷新后），向后端查询该作品已有的会话
  try {
    const sessions = await chatSessionsApi.listSessions(workId)
    if (sessions.length > 0) {
      for (const sess of sessions) {
        // 侧会话不进主会话侧边栏（只在侧会话面板出现，PRD FR-E1）
        if (sess.kind === 'side') continue
        const exists = conversations.value.find(c => c.id === sess.id)
        if (!exists) {
          conversations.value.push({
            id: sess.id,
            title: sess.title || '新会话',
            messages: [],
            createdAt: sess.created_at ? new Date(sess.created_at).getTime() : Date.now(),
            // workId 必须写回 conversations（不只 history）：
            // resolveWorkIdForSession 靠 conv._workId 判定"这个会话属于哪个作品"，
            // 缺了它，切到同作品旧会话会误判成无关会话 → 解绑 activeWork →
            // work_context 注入断链（2026-11-04 方案 A 配套修复）。
            _workId: sess.work_id || null,
          })
        }
        addConversation(sess.id, sess.title || '新会话', sess.folder_id || 'chat-files', sess.id, sess.work_id)
      }
      conversations.value.sort((a, b) => b.createdAt - a.createdAt)
      // 后端按 updated_at 倒序返回，sessions[0] 即最新会话
      const latest = conversations.value.find(c => c.id === sessions[0].id)
      if (latest) {
        await switchToConversation(latest.id, mergeLoadedHistory)
        return
      }
    }
  } catch {
    // 后端查询失败时回退：新建会话
  }
  // 该作品确实还没有会话：复用当前空草稿（草稿未落库，上面两处都查不到），
  // 否则每次点同一个作品都会丢弃用户已输入的内容。
  const current = conversations.value.find(c => c.id === activeConvId.value)
  if (current && current._isDraft && current.messages.length === 0) {
    current._workId = workId
    heroMode.value = true
    return
  }
  await newConversation(undefined, workId)
}

function _resetToInitial() {
  resetToInitial()
  showWorkDetail.value = false
  ctxStore.setSidebarVisible(false)
  ctxStore.clearAll()
}

function exposedSwitchToConversation(convId: string) {
  return switchToConversation(convId, mergeLoadedHistory)
}

defineExpose({ newConversation: newConversationWithIntro, deleteConversation, onWorkChatAction, resetToInitial: _resetToInitial, enterHeroMode, switchToConversation: exposedSwitchToConversation, switchToFolder, showWorkDetail, toggleWorkDetail, activateSkill, activateAgent })
</script>

<style src="./chat-view-global.css"></style>

<style scoped src="./chat-view.css"></style>