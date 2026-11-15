<template>
  <aside class="mint-sidebar" :class="{ 'mint-content-float-open': contentFloatOpen }">
    <div class="mint-sidebar-main" :class="{ 'mint-main-noscroll': workStore.sidebarTab === 'content' && !isCollapsed }">
      <!-- 固定导航区：logo / 会话-内容 tab 不参与滚动 -->
      <div class="mint-sidebar-head">
      <div class="mint-logo-row" v-if="!isCollapsed">
        <div class="mint-logo-top" @click="goToHome" title="返回首页" style="cursor:pointer;">
          <transition name="logo-fade" mode="out-in">
            <img key="text" src="/icons/logo2.svg" alt="Pulse Studio" class="mint-logo-text" />
          </transition>
        </div>
        <button class="mint-sidebar-search-btn" @click="toggleSidebarSearch" title="搜索">
          <Search :size="16" :stroke-width="1.5" />
        </button>
      </div>
      <template v-else>
        <div class="mint-logo-top" @click="goToHome" title="返回首页" style="cursor:pointer;">
          <img src="/icons/crab.svg" alt="Pulse Studio" class="mint-logo-crab" />
        </div>
        <button
          class="mint-nav-item mint-sidebar-search-collapsed"
          data-tooltip="搜索"
          aria-label="搜索"
          @click="toggleSidebarSearch"
        >
          <Search :size="18" />
        </button>
        <!-- 收起态「内容」入口：内容 Tab 下收起侧边栏后出现，点击临时悬浮内容导航区 -->
        <button
          v-if="workStore.sidebarTab === 'content'"
          ref="contentFloatBtnRef"
          class="mint-nav-item mint-content-float-btn"
          :class="{ 'mint-nav-active': contentFloatOpen }"
          data-tooltip="内容"
          aria-label="悬浮显示内容导航"
          :aria-expanded="contentFloatOpen"
          @click="toggleContentFloat"
        >
          <LibraryBig :size="18" />
        </button>
      </template>

      <div class="mint-sidebar-tabs" v-if="!isCollapsed">
        <button
          class="mint-sidebar-tab"
          :class="{ 'mint-sidebar-tab-active': workStore.sidebarTab === 'chat' }"
          @click="switchToChatTab"
        >会话</button>
        <button
          class="mint-sidebar-tab"
          :class="{ 'mint-sidebar-tab-active': workStore.sidebarTab === 'content' }"
          @click="switchToContentTab"
        >内容</button>
      </div>
      </div>

      <!-- ====== 会话 Tab ====== -->
      <nav class="mint-nav" v-show="workStore.sidebarTab === 'chat'" style="margin-top: 8px;">
        <div class="mint-nav-item" data-tooltip="首页" @click="goToHome" :class="{ 'mint-nav-active': isHomeActive }">
          <Home :size="18" />
          <span>首页</span>
        </div>
        <div class="mint-nav-item" id="nav-chat" data-page="chat" data-tooltip="开启新创作" @click="onNewTaskClick" :class="{ 'mint-nav-active': isChatActive }">
          <MessageSquarePlus :size="18" />
          <span>开启新创作</span>
        </div>
        <div class="mint-nav-item" id="nav-topic-pool" data-page="topic-pool" data-tooltip="选题池" @click="goToTopicPool" :class="{ 'mint-nav-active': isTopicPoolActive }">
          <Lightbulb :size="18" />
          <span>选题池</span>
        </div>
        <div class="mint-nav-item" data-tooltip="数据分析" @click="goToMyWorks" :class="{ 'mint-nav-active': isMyWorksActive }">
          <ChartNoAxesCombined :size="18" />
          <span>数据分析</span>
        </div>
        <div class="mint-nav-item" data-tooltip="任务清单" @click="goToTaskPlans" :class="{ 'mint-nav-active': isTaskPlansActive }">
          <ClipboardList :size="18" />
          <span>任务清单</span>
        </div>
        <div class="mint-nav-item" data-tooltip="我的空间" @click="goToPortfolio" :class="{ 'mint-nav-active': isPortfolioActive }">
          <LayoutGrid :size="18" />
          <span>我的空间</span>
        </div>
        <div class="mint-nav-item" data-tooltip="技能库" @click="goToSkills" :class="{ 'mint-nav-active': isSkillsActive }">
          <Blocks :size="18" />
          <span>技能库</span>
        </div>

        <!-- 滚动区：只有「对话」「素材区」两个列表滚动，上方导航保持固定 -->
        <div class="mint-sidebar-scroll" @scroll.passive="closeCollectMenu">
        <div class="mint-ws-toolbar" v-if="!isCollapsed">
          <span class="mint-ws-toolbar-label">对话</span>
          <div class="mint-ws-toolbar-actions">
            <button class="mint-ws-toolbar-action" @click="onNewTaskClick" title="新建对话">
              <MessageSquarePlus :size="13" :stroke-width="1.5" />
            </button>
          </div>
        </div>
        <div class="mint-conversations-content" v-if="!isCollapsed">
          <div
            v-for="(item, idx) in visibleChatItems"
            :key="item.id"
            class="mint-ws-thread"
            :class="{ 'mint-ws-thread-blurred': idx === 6 && !chatExpanded && unfiledChatItems.length > 7 }"
            @click="onChatHistoryClick(item)"
            @contextmenu.prevent="onChatContextMenu($event, item.id)"
          >
            <span style="width:16px;flex:none;"></span>
            <span class="mint-ws-file-name">{{ item.title }}</span>
            <img
              v-if="isThreadRunning(item.id)"
              src="/icons/crab-flat.svg"
              class="mint-ws-thread-crab"
              alt="执行中"
              title="正在执行…"
            />
            <span class="mint-ws-thread-delete" @click.stop="onDeleteChat(item.id)">
              <Trash2 :size="12" :stroke-width="1.6" />
            </span>
          </div>
          <div v-if="unfiledChatItems.length > 7 && !chatExpanded" class="mint-chat-show-more" @click="chatExpanded = true">
            <span>还有 {{ unfiledChatItems.length - 7 }} 条对话</span>
          </div>
          <div v-if="unfiledChatItems.length > 7 && chatExpanded" class="mint-chat-show-more" @click="chatExpanded = false">
            <ChevronUp :size="14" :stroke-width="1.5" />
            <span>收起</span>
          </div>
          <div v-if="unfiledChatItems.length === 0" class="mint-conversations-empty">
            暂无对话
          </div>
        </div>

        <div class="mint-ws-toolbar" v-if="!isCollapsed">
          <span class="mint-ws-toolbar-label">素材区</span>
          <div class="mint-ws-toolbar-actions">
            <button class="mint-ws-toolbar-action" @click="onImportFolder" title="导入文件夹">
              <FolderOpen :size="13" :stroke-width="1.5" />
            </button>
          </div>
        </div>
        <div class="mint-ws-content" v-if="!isCollapsed">
          <div v-for="(folder, fIdx) in visibleFolders" :key="folder.id" class="mint-ws-folder" :class="{ 'mint-ws-folder-active': fileStore.activeFolderId === folder.id, 'mint-ws-thread-blurred': fIdx === 6 && !workspaceExpanded && fileStore.folders.length > 7 }" @click="onFolderClick(folder.id)">
            <div class="mint-ws-folder-header" :class="{ 'is-open': isFolderExpanded(folder.id) }">
              <span class="mint-ws-folder-icon-wrap" :title="isFolderExpanded(folder.id) ? '收起对话' : '展开对话'">
                <FolderOpen
                  v-if="isFolderExpanded(folder.id)"
                  :size="13"
                  :stroke-width="1.5"
                  class="mint-ws-folder-icon"
                />
                <Folder
                  v-else
                  :size="13"
                  :stroke-width="1.5"
                  class="mint-ws-folder-icon"
                />
              </span>
              <span class="mint-ws-folder-name">{{ folder.name }}</span>
              <span
                v-if="!isFolderExpanded(folder.id) && getFolderThreads(folder.id).length"
                class="mint-ws-folder-count"
              >{{ getFolderThreads(folder.id).length }}</span>
              <span class="mint-ws-folder-action" @click.stop="onNewChatInFolder(folder.id)" title="新建对话">
                <MessageSquarePlus :size="12" :stroke-width="1.6" />
              </span>
              <span class="mint-ws-folder-delete" @click.stop="onDeleteFolder(folder.id)" title="删除文件夹">
                <Trash2 :size="12" :stroke-width="1.6" />
              </span>
              <span class="mint-ws-folder-more" @click.stop="openFolderMenu($event, folder)" title="更多操作">
                <MoreVertical :size="12" :stroke-width="1.6" />
              </span>
            </div>
            <div v-if="getFolderThreads(folder.id).length && isFolderExpanded(folder.id)" class="mint-ws-folder-threads">
              <div
                v-for="conv in visibleFolderThreads(folder.id)"
                :key="conv.id"
                class="mint-ws-thread mint-ws-thread-nested"
                @click.stop="onFolderThreadClick(folder.id, conv.id)"
                @contextmenu.prevent.stop="onChatContextMenu($event, conv.id)"
              >
                <span class="mint-ws-file-name">{{ conv.title }}</span>
                <img
                  v-if="isThreadRunning(conv.id)"
                  src="/icons/crab-flat.svg"
                  class="mint-ws-thread-crab"
                  alt="执行中"
                  title="正在执行…"
                />
                <span class="mint-ws-thread-delete" @click.stop="onDeleteChat(conv.id)">
                  <Trash2 :size="12" :stroke-width="1.6" />
                </span>
              </div>
              <div
                v-if="getFolderThreads(folder.id).length > FOLDER_THREAD_LIMIT"
                class="mint-chat-show-more mint-chat-show-more-nested"
                @click.stop="toggleFolderThreads(folder.id)"
              >
                <template v-if="!expandedFolderThreads.has(folder.id)">
                  <span>还有 {{ getFolderThreads(folder.id).length - FOLDER_THREAD_LIMIT }} 条对话</span>
                </template>
                <template v-else>
                  <ChevronUp :size="13" :stroke-width="1.5" />
                  <span>收起</span>
                </template>
              </div>
            </div>
          </div>
          <div v-if="fileStore.folders.length > 7 && !workspaceExpanded" class="mint-chat-show-more" @click="workspaceExpanded = true">
            <span>还有 {{ fileStore.folders.length - 7 }} 个文件夹</span>
          </div>
          <div v-if="fileStore.folders.length > 7 && workspaceExpanded" class="mint-chat-show-more" @click="workspaceExpanded = false">
            <ChevronUp :size="14" :stroke-width="1.5" />
            <span>收起</span>
          </div>
        </div>
        </div>
      </nav>

      <!-- ====== 内容 Tab ====== -->
      <!-- 显隐交给 CSS：展开态正常流；收起态默认隐藏，点悬浮按钮后 fixed 浮出（mint-content-float-open） -->
      <div
        ref="contentPanelRef"
        class="mint-creation-panel mint-content-float-panel"
        v-show="workStore.sidebarTab === 'content'"
        :style="contentFloatPanelStyle"
      >
        <div class="mint-creation-toolbar">
          <div class="mint-creation-toolbar-actions">
            <button
              class="mint-creation-toolbar-btn"
              :class="{ 'is-syncing': worksSyncing }"
              @click="onRefreshWorks"
              :title="worksSyncing ? '正在同步我的空间作品…' : '同步我的空间作品'"
            >
              <RefreshCw :size="14" />
            </button>
          </div>
        </div>

        <!-- 标签切换导航 -->
        <div class="mint-content-tabs">
          <button
            class="mint-content-tab"
            :class="{ 'mint-content-tab-active': activeContentTab === 'collected' }"
            aria-haspopup="menu"
            :aria-expanded="collectMenu.visible"
            @click="onCollectTabClick"
            @mouseenter="openCollectMenu($event)"
            @mouseleave="scheduleCloseCollectMenu"
          >
            <Inbox :size="13" />
            <span>采集</span>
            <span class="mint-tab-count">{{ collectedWorks.length }}</span>
          </button>
          <button
            class="mint-content-tab"
            :class="{ 'mint-content-tab-active': activeContentTab === 'draft' }"
            @click="activeContentTab = 'draft'"
          >
            <FileText :size="13" />
            <span>草稿</span>
            <span class="mint-tab-count">{{ draftWorks.length }}</span>
          </button>
          <button
            class="mint-content-tab"
            :class="{ 'mint-content-tab-active': activeContentTab === 'published' }"
            @click="activeContentTab = 'published'"
          >
            <Send :size="13" />
            <span>发布</span>
            <span class="mint-tab-count">{{ publishedWorks.length }}</span>
          </button>
        </div>

        <!-- 采集类型分离条：选中图文/视频/文本后出现，点 × 回到全部 -->
        <div class="mint-collect-filter-bar" v-if="activeContentTab === 'collected' && collectKindFilter">
          <span class="mint-collect-filter-chip">
            <component :is="getMediaKindIcon(collectKindFilter)" :size="13" :stroke-width="1.6" />
            <span>{{ workKindLabel(collectKindFilter) }}</span>
            <span class="mint-collect-filter-count">{{ collectKindCount(collectKindFilter) }}</span>
            <button class="mint-collect-filter-clear" title="显示全部采集内容" @click.stop="clearCollectKindFilter">
              <X :size="12" :stroke-width="2" />
            </button>
          </span>
        </div>

        <div class="mint-creation-list">
          <!-- 发布列表 -->
          <div class="mint-creation-group-items mint-scrollable-list" :class="{ 'mint-scroll-fade': publishedWorks.length > 6 && listFadeState.published }" v-show="activeContentTab === 'published'" ref="publishedListRef" @scroll="onListScroll($event, 'published')">
            <template v-for="work in publishedWorks" :key="work.id">
            <div
              class="mint-work-row"
              :class="{ 'mint-work-active': workStore.activeWorkId === work.id }"
              @click="onSelectWork(work.id)"
            >
              <div class="mint-work-thumb">
                <WorkMediaThumb :info="mediaOf(work)" :title="work.title" thumb />
              </div>
              <div class="mint-work-info">
                <div class="mint-work-title">{{ work.title }}</div>
                <div class="mint-work-meta">{{ work.platform ? workStore.getPlatformLabel(work.platform) + ' · ' : '' }}{{ formatRelativeTime(work.dataCollectedAt) }}</div>
              </div>
              <span
                class="mint-work-thread-toggle"
                :class="{ 'is-open': expandedWorkThreads.has(work.id) }"
                :title="expandedWorkThreads.has(work.id) ? '收起会话' : '展开会话'"
                @click.stop="toggleWorkThreads(work.id)"
              >
                <ChevronDown v-if="!expandedWorkThreads.has(work.id)" :size="13" :stroke-width="1.8" />
                <ChevronUp v-else :size="13" :stroke-width="1.8" />
              </span>
              <span class="mint-work-add-chat" title="添加会话" @click.stop="onAddWorkConversation(work.id)">
                <MessageSquarePlus :size="13" :stroke-width="1.8" />
              </span>
              <span class="mint-work-delete" @click.stop="onDeleteWork(work.id)"><Trash2 :size="12" /></span>
            </div>
            <div v-if="expandedWorkThreads.has(work.id)" class="mint-work-threads">
              <div
                v-for="conv in workConversationsOf(work.id)"
                :key="conv.id"
                class="mint-work-thread"
                @click.stop="onSelectWorkConversation(work.id, conv.id)"
              >
                <img
                  v-if="isThreadRunning(conv.id)"
                  src="/icons/crab-flat.svg"
                  class="mint-ws-thread-crab"
                  alt="执行中"
                  title="正在执行…"
                />
                <span class="mint-work-thread-title">{{ conv.title }}</span>
              </div>
              <button class="mint-work-thread-add" @click.stop="onAddWorkConversation(work.id)">
                <Plus :size="12" :stroke-width="2" />
                <span>添加会话</span>
              </button>
            </div>
            </template>
            <div v-if="publishedWorks.length === 0" class="mint-ws-folder-empty"><span>{{ worksSyncing ? '正在同步我的空间…' : '暂无发布内容' }}</span></div>
          </div>

          <!-- 草稿列表：按产物形态分组 -->
          <div class="mint-creation-group-items mint-scrollable-list" :class="{ 'mint-scroll-fade': draftWorks.length > 6 && listFadeState.draft }" v-show="activeContentTab === 'draft'" ref="draftListRef" @scroll="onListScroll($event, 'draft')">
            <template v-for="group in draftWorksByKind" :key="group.kind">
              <div class="mint-content-subgroup">
                <div class="mint-content-subgroup-header">
                  <component :is="getMediaKindIcon(group.kind)" :size="18" :stroke-width="1.5" />
                  <span>{{ group.label }}</span>
                  <span class="mint-creation-group-count">{{ group.items.length }}</span>
                </div>
                <template v-for="work in group.items" :key="work.id">
                <div
                  class="mint-work-row"
                  :class="{ 'mint-work-active': workStore.activeWorkId === work.id }"
                  @click="onSelectWork(work.id)"
                >
                  <div class="mint-work-thumb">
                    <WorkMediaThumb :info="mediaOf(work)" :title="work.title" thumb />
                  </div>
                  <div class="mint-work-info">
                    <div class="mint-work-title">{{ work.title }}</div>
                    <div class="mint-work-meta">{{ formatRelativeTime(work.dataCollectedAt) }}</div>
                  </div>
                  <span
                    class="mint-work-thread-toggle"
                    :class="{ 'is-open': expandedWorkThreads.has(work.id) }"
                    :title="expandedWorkThreads.has(work.id) ? '收起会话' : '展开会话'"
                    @click.stop="toggleWorkThreads(work.id)"
                  >
                    <ChevronDown v-if="!expandedWorkThreads.has(work.id)" :size="13" :stroke-width="1.8" />
                    <ChevronUp v-else :size="13" :stroke-width="1.8" />
                  </span>
                  <span class="mint-work-add-chat" title="添加会话" @click.stop="onAddWorkConversation(work.id)">
                <MessageSquarePlus :size="13" :stroke-width="1.8" />
              </span>
              <span class="mint-work-delete" @click.stop="onDeleteWork(work.id)"><Trash2 :size="12" /></span>
                </div>
                <div v-if="expandedWorkThreads.has(work.id)" class="mint-work-threads">
                  <div
                    v-for="conv in workConversationsOf(work.id)"
                    :key="conv.id"
                    class="mint-work-thread"
                    @click.stop="onSelectWorkConversation(work.id, conv.id)"
                  >
                    <img
                      v-if="isThreadRunning(conv.id)"
                      src="/icons/crab-flat.svg"
                      class="mint-ws-thread-crab"
                      alt="执行中"
                      title="正在执行…"
                    />
                    <span class="mint-work-thread-title">{{ conv.title }}</span>
                  </div>
                  <button class="mint-work-thread-add" @click.stop="onAddWorkConversation(work.id)">
                    <Plus :size="12" :stroke-width="2" />
                    <span>添加会话</span>
                  </button>
                </div>
                </template>
              </div>
            </template>
            <div v-if="draftWorks.length === 0" class="mint-ws-folder-empty"><span>{{ worksSyncing ? '正在同步我的空间…' : '暂无草稿' }}</span></div>
          </div>

          <!-- 采集列表：按我的空间产出类型分组 -->
          <div class="mint-creation-group-items mint-scrollable-list" :class="{ 'mint-scroll-fade': collectedWorks.length > 6 && listFadeState.collected }" v-show="activeContentTab === 'collected'" ref="collectedListRef" @scroll="onListScroll($event, 'collected')">
            <template v-for="group in collectedWorksByKind" :key="group.kind">
              <div class="mint-content-subgroup">
                <div class="mint-content-subgroup-header">
                  <component :is="getMediaKindIcon(group.kind)" :size="18" :stroke-width="1.5" />
                  <span>{{ group.label }}</span>
                  <span class="mint-creation-group-count">{{ group.items.length }}</span>
                </div>
                <template v-for="work in group.items" :key="work.id">
                <div
                  class="mint-work-row"
                  :class="{ 'mint-work-active': workStore.activeWorkId === work.id }"
                  @click="onSelectWork(work.id)"
                >
                  <div class="mint-work-thumb">
                    <WorkMediaThumb :info="mediaOf(work)" :title="work.title" thumb />
                  </div>
                  <div class="mint-work-info">
                    <div class="mint-work-title">{{ work.title }}</div>
                    <div class="mint-work-meta">{{ work.platform ? workStore.getPlatformLabel(work.platform) + ' · ' : '' }}{{ formatRelativeTime(work.dataCollectedAt) }}</div>
                  </div>
                  <span
                    class="mint-work-thread-toggle"
                    :class="{ 'is-open': expandedWorkThreads.has(work.id) }"
                    :title="expandedWorkThreads.has(work.id) ? '收起会话' : '展开会话'"
                    @click.stop="toggleWorkThreads(work.id)"
                  >
                    <ChevronDown v-if="!expandedWorkThreads.has(work.id)" :size="13" :stroke-width="1.8" />
                    <ChevronUp v-else :size="13" :stroke-width="1.8" />
                  </span>
                  <span class="mint-work-add-chat" title="添加会话" @click.stop="onAddWorkConversation(work.id)">
                <MessageSquarePlus :size="13" :stroke-width="1.8" />
              </span>
              <span class="mint-work-delete" @click.stop="onDeleteWork(work.id)"><Trash2 :size="12" /></span>
                </div>
                <div v-if="expandedWorkThreads.has(work.id)" class="mint-work-threads">
                  <div
                    v-for="conv in workConversationsOf(work.id)"
                    :key="conv.id"
                    class="mint-work-thread"
                    @click.stop="onSelectWorkConversation(work.id, conv.id)"
                  >
                    <img
                      v-if="isThreadRunning(conv.id)"
                      src="/icons/crab-flat.svg"
                      class="mint-ws-thread-crab"
                      alt="执行中"
                      title="正在执行…"
                    />
                    <span class="mint-work-thread-title">{{ conv.title }}</span>
                    <span class="mint-ws-thread-delete" title="删除会话" @click.stop="onDeleteChat(conv.id)">
                      <Trash2 :size="12" />
                    </span>
                  </div>
                  <button class="mint-work-thread-add" @click.stop="onAddWorkConversation(work.id)">
                    <Plus :size="12" :stroke-width="2" />
                    <span>添加会话</span>
                  </button>
                </div>
                </template>
              </div>
            </template>
            <div v-if="collectedWorksByKind.length === 0" class="mint-ws-folder-empty"><span>{{ worksSyncing ? '正在同步我的空间…' : (collectKindFilter ? '暂无' + workKindLabel(collectKindFilter) + '采集内容' : '暂无采集内容') }}</span></div>
          </div>

          <!-- 采集按钮（仅在素材 tab 显示） -->
          <div class="mint-collect-action" v-if="activeContentTab === 'collected'">
            <button class="mint-creation-toolbar-btn" @click="openCollectModal" title="采集内容">
              <Link :size="14" />
              <span>添加采集内容</span>
            </button>
          </div>
        </div>
        </div>

      </div>

    <div class="mint-sidebar-bottom">
      <div class="mint-sidebar-actions">
        <div class="mint-nav-item" data-tooltip="设置" @click="onOpenSettings">
          <Settings :size="18" />
          <span>设置</span>
        </div>
        <button class="mint-sidebar-toggle" id="sidebar-toggle" data-tooltip="收起侧边栏" aria-label="收起侧边栏" @click="$emit('toggle-sidebar')">
          <PanelLeft :size="16" />
        </button>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="contextMenu.visible"
        class="mint-context-menu"
        :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
        @click.stop
      >
        <button class="mint-context-menu-item mint-context-delete" @click="onDeleteChat(contextMenu.convId)">
          <Trash2 :size="18" />
          <span>删除对话</span>
        </button>
      </div>
      <div v-if="contextMenu.visible" class="mint-context-overlay" @click="closeContextMenu" @contextmenu.prevent="closeContextMenu"></div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="collectMenu.visible"
        class="mint-collect-menu"
        :style="{ top: collectMenu.y + 'px', left: collectMenu.x + 'px' }"
        role="menu"
        @mouseenter="cancelCloseCollectMenu"
        @mouseleave="scheduleCloseCollectMenu"
      >
        <div class="mint-collect-menu-title">采集内容类型</div>
        <button
          v-for="item in COLLECT_MENU_ITEMS"
          :key="item.kind"
          class="mint-collect-menu-item"
          :class="{ 'is-active': collectKindFilter === item.kind }"
          role="menuitem"
          :aria-pressed="collectKindFilter === item.kind"
          @click="onCollectKindSelect(item.kind)"
        >
          <span class="mint-collect-menu-icon">
            <component :is="item.icon" :size="15" :stroke-width="1.6" />
          </span>
          <span class="mint-collect-menu-text">
            <span class="mint-collect-menu-label">{{ item.label }}</span>
            <span class="mint-collect-menu-desc">{{ item.desc }}</span>
          </span>
          <span class="mint-collect-menu-count">{{ collectKindCount(item.kind) }}</span>
        </button>
      </div>
    </Teleport>

    <AddWorkModal
      :visible="collectModalOpen"
      @close="collectModalOpen = false"
      @synced="onCollectSynced"
    />

    <Teleport to="body">
      <transition name="search-modal">
        <div v-if="sidebarSearchOpen" class="search-modal-overlay" @click.self="closeSidebarSearch">
          <div ref="searchModalEl" class="search-modal" @keydown="onSearchKeydown">
            <div class="search-modal-field">
              <Search :size="17" :stroke-width="1.8" class="search-modal-icon" />
              <input
                ref="sidebarSearchInput"
                v-model="sidebarSearchQuery"
                class="search-modal-input"
                placeholder="搜索对话、选题或功能..."
                autofocus
              />
              <button v-if="sidebarSearchQuery" class="search-modal-clear" @click="clearSidebarSearch">
                <X :size="14" :stroke-width="2" />
              </button>
              <kbd v-else class="search-modal-esc">Esc</kbd>
            </div>

            <div class="search-modal-body">
              <template v-if="sidebarSearchResults.length">
                <div class="search-modal-section-label">
                  <span>最近对话</span>
                  <span class="search-modal-section-count">{{ sidebarSearchResults.length }} 条</span>
                </div>
                <div class="search-modal-results">
                  <div
                    v-for="(item, index) in sidebarSearchResults"
                    :key="item.id"
                    class="search-modal-item"
                    :class="{ 'is-active': searchActiveIndex === index }"
                    @mouseenter="searchActiveIndex = index"
                    @click="onSearchResultClick(item)"
                  >
                    <span class="search-modal-item-title">{{ item.title }}</span>
                    <span class="search-modal-item-time">{{ formatSearchTime(item.createdAt) }}</span>
                    <kbd v-if="searchActiveIndex === index" class="search-modal-item-enter">↵</kbd>
                  </div>
                </div>
              </template>

              <div v-if="sidebarSearchResults.length && matchedSearchActions.length" class="search-modal-divider"></div>

              <template v-if="matchedSearchActions.length">
                <div class="search-modal-section-label">快捷操作</div>
                <div class="search-modal-actions">
                  <button
                    v-for="(action, aIndex) in matchedSearchActions"
                    :key="action.id"
                    class="search-modal-action"
                    :class="{
                      'is-active': searchActiveIndex === sidebarSearchResults.length + aIndex,
                      'is-primary': action.primary,
                    }"
                    @mouseenter="searchActiveIndex = sidebarSearchResults.length + aIndex"
                    @click="runSearchAction(action)"
                  >
                    <span class="search-modal-action-icon">
                      <component :is="action.icon" :size="14" :stroke-width="1.7" />
                    </span>
                    <span class="search-modal-action-label">{{ action.label }}</span>
                  </button>
                </div>
              </template>

              <div v-if="searchOptions.length === 0" class="search-modal-empty">
                {{ sidebarSearchQuery ? `没有找到「${sidebarSearchQuery}」相关结果` : '暂无对话' }}
              </div>
            </div>

            <div class="search-modal-footer">
              <div class="search-modal-footer-hints">
                <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
                <span><kbd>↵</kbd> 打开</span>
                <span><kbd>Esc</kbd> 关闭</span>
              </div>
              <span class="search-modal-footer-hint"><kbd>Ctrl</kbd>+<kbd>K</kbd> 唤起</span>
            </div>
          </div>
        </div>
      </transition>
    </Teleport>

    <Teleport to="body">
      <div v-if="folderMenu.visible" class="mint-context-overlay" @click="closeFolderMenu" @contextmenu.prevent="closeFolderMenu"></div>
      <div v-if="folderMenu.visible" class="mint-folder-menu" :style="{ left: folderMenu.x + 'px', top: folderMenu.y + 'px' }">
        <button class="mint-context-menu-item" @click="onMenuFolderManager">
          <FolderCog :size="15" :stroke-width="1.5" />
          <span>文件夹管理</span>
        </button>
        <button class="mint-context-menu-item" @click="onMenuRevealFolder">
          <FolderOpen :size="15" :stroke-width="1.5" />
          <span>在资源管理器打开</span>
        </button>
      </div>
    </Teleport>

    <FolderManagerDialog :visible="folderManagerOpen" @close="folderManagerOpen = false" />

    <Teleport to="body">
      <transition name="mint-toast">
        <div v-if="toastMessage" class="mint-sidebar-toast">{{ toastMessage }}</div>
      </transition>
    </Teleport>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref, nextTick, onMounted, onBeforeUnmount, watch, type Component } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  MessageSquarePlus,
  Home,
  LibraryBig,
  Lightbulb,
  ChartNoAxesCombined,
  ClipboardList,
  LayoutGrid,
  Settings,
  PanelLeft,
  Trash2,
  FolderOpen,
  Folder,
  FolderCog,
  MoreVertical,
  ChevronUp,
  ChevronDown,
  Image as ImageIcon,
  RefreshCw,
  Link,
  Send,
  Inbox,
  Video,
  CheckCircle2,
  Pencil,
  Bookmark,
  FileText,
  Music,
  Code2,
  Table2,
  Search,
  Blocks,
  X,
  Plus,
} from 'lucide-vue-next'
import { useChatHistory } from '@/composables/useChatHistory'
import { useUIState } from '@/composables/useUIState'
import { useWorkStore } from '@/stores/work'
import { useFileStore, type FolderItem } from '@/stores/files'
import { revealFolder, isAbsoluteLocalPath } from '@/api/workspace'
import WorkMediaThumb from '@/components/portfolio/WorkMediaThumb.vue'
import AddWorkModal from '@/components/chat/AddWorkModal.vue'
import {
  summarizeWorkItemMedia,
  WORK_KIND_ORDER,
  workKindLabel,
  type WorkMediaInfo,
  type WorkMediaKind,
} from '@/components/portfolio/workMedia'
import FolderManagerDialog from './FolderManagerDialog.vue'

const props = defineProps<{
  currentPage: string
  isCollapsed?: boolean
}>()

const emit = defineEmits<{
  'nav-click': [page: string]
  'toggle-sidebar': []
  'go-eco': []
  'open-settings': []
  'new-chat': []
  'delete-chat': [id: string]
  'select-work': [workId: string]
  'create-draft': [contentType: string]
  'refresh-works': []
  'select-file': [fileId: string]
  'select-conversation': [convId: string]
  'new-work-conversation': [workId: string]
  'new-chat-in-folder': [folderId: string]
  'select-folder': [folderId: string]
  'exit-creation': []
  'switch-creation-panel': [typeKey: string]
}>()

const router = useRouter()
const route = useRoute()
const workStore = useWorkStore()
const fileStore = useFileStore()
const { openSettings } = useUIState()

const { history: chatHistory, removeConversation: removeChatHistory, getConversationsForFolder: getFolderConversations, getConversationsForWork: getWorkConversations, unfileConversations, runningSessionIds } = useChatHistory()

// ====== 笔记会话归置（2026-11-04 方案 A，大哥拍板）======
// workId 有值的会话不进「对话」平铺列表，挂到「内容」tab 对应笔记行下展开。
// 孤儿会话（笔记已删）回落「对话」列表尾部，不静默消失。
// works 未加载完（isLoading）时不判孤儿，避免同步瞬间误回落。
function isOrphanWorkChat(workId: string | null | undefined): boolean {
  if (!workId) return false
  if (workStore.isLoading) return false
  return !workStore.works.some(w => w.id === workId)
}

const workConversationsOf = (workId: string) => getWorkConversations(workId)

const expandedWorkThreads = ref<Set<string>>(new Set())

function toggleWorkThreads(workId: string) {
  const next = new Set(expandedWorkThreads.value)
  if (next.has(workId)) next.delete(workId)
  else next.add(workId)
  expandedWorkThreads.value = next
}

function onSelectWorkConversation(workId: string, convId: string) {
  // 恢复作品上下文（右侧栏预览 + work_context 注入链路），再进对话
  if (workStore.activeWorkId !== workId) {
    workStore.setActiveWork(workId)
  }
  emit('select-conversation', convId)
}

function onAddWorkConversation(workId: string) {
  // 先恢复作品上下文（新会话的 work bar / work_context 注入依赖 activeWork），
  // 再让 ChatView 新建空草稿会话——switchToWorkConversation 的空草稿复用逻辑
  // 会把它挂到该作品名下，不会重复建。
  if (workStore.activeWorkId !== workId) {
    workStore.setActiveWork(workId)
  }
  emit('new-work-conversation', workId)
}

// 侧边栏「正在执行」螃蟹标志（2026-11-02）：ChatView 回合状态机写入
// runningSessionIds（模块级单例），这里只读渲染。
// 只挂对话行（未归档对话 + 文件夹内嵌套对话）；素材区文件夹头不放——
// 大哥拍板：素材区不需要文件夹级标志。
function isThreadRunning(sessionId: string): boolean {
  return runningSessionIds.has(sessionId)
}

const draftExpanded = ref(true)
const collectedExpanded = ref(true)
const publishedExpanded = ref(false)
const chatExpanded = ref(false)
const activeContentTab = ref<'published' | 'draft' | 'collected'>('collected')
const workspaceExpanded = ref(false)

const sidebarSearchOpen = ref(false)
const sidebarSearchQuery = ref('')
const sidebarSearchInput = ref<HTMLInputElement | null>(null)
const searchModalEl = ref<HTMLElement | null>(null)
const searchActiveIndex = ref(0)

type SearchAction = {
  id: string
  label: string
  keywords: string
  icon: Component
  primary?: boolean
  run: () => void
}

/* 搜索面板的快捷操作：与对话结果合并成一条扁平选项链，↑↓ 才能跨分组导航。
   关键词让「选题」「素材」这类口语词也能命中对应功能。 */
const SEARCH_ACTIONS: SearchAction[] = [
  {
    id: 'new-task',
    label: '开启新创作',
    keywords: '新建 创作 对话 开始 chat new',
    icon: MessageSquarePlus,
    primary: true,
    run: () => { onNewTaskClick(); closeSidebarSearch() },
  },
  {
    id: 'topic-pool',
    label: '选题池',
    keywords: '选题 热点 爆款 灵感 topic',
    icon: Lightbulb,
    run: () => { goToTopicPool(); closeSidebarSearch() },
  },
  {
    id: 'my-works',
    label: '数据分析',
    keywords: '数据 分析 统计 作品 报表 works',
    icon: ChartNoAxesCombined,
    run: () => { goToMyWorks(); closeSidebarSearch() },
  },
  {
    id: 'import',
    label: '导入素材',
    keywords: '导入 素材 文件 文件夹 上传 import folder',
    icon: FolderOpen,
    run: () => { void onImportFolder(); closeSidebarSearch() },
  },
]

const publishedListRef = ref<HTMLElement | null>(null)
const draftListRef = ref<HTMLElement | null>(null)
const collectedListRef = ref<HTMLElement | null>(null)
const listFadeState = ref<Record<string, number>>({ published: 1, draft: 1, collected: 1 })

// 采集按钮悬浮菜单：悬浮「采集」Tab 弹出图文 / 视频 / 文本，选中后切到采集列表并携带类型
const COLLECT_MENU_ITEMS: Array<{ kind: WorkMediaKind; label: string; desc: string; icon: Component }> = [
  { kind: 'image_text', label: '图文', desc: '图片笔记与正文', icon: ImageIcon },
  { kind: 'video', label: '视频', desc: '视频作品与封面', icon: Video },
  { kind: 'text', label: '文本', desc: '纯文字内容', icon: FileText },
]
const collectMenu = ref<{ visible: boolean; x: number; y: number }>({ visible: false, x: 0, y: 0 })
// 采集弹窗：粘贴链接 / 平台同步两个入口
const collectModalOpen = ref(false)
// 采集列表的「按类型分离」状态：null = 全部，否则只显示该类型
const collectKindFilter = ref<WorkMediaKind | null>(null)
let collectMenuCloseTimer: ReturnType<typeof setTimeout> | null = null

function cancelCloseCollectMenu() {
  if (collectMenuCloseTimer) {
    clearTimeout(collectMenuCloseTimer)
    collectMenuCloseTimer = null
  }
}

function openCollectMenu(e: MouseEvent) {
  cancelCloseCollectMenu()
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const menuWidth = 208
  collectMenu.value = {
    visible: true,
    x: Math.min(Math.max(8, rect.left), window.innerWidth - menuWidth - 8),
    y: rect.bottom + 6,
  }
}

// 留出移入弹窗的时间，避免鼠标穿过间隙时弹窗闪退
function scheduleCloseCollectMenu() {
  cancelCloseCollectMenu()
  collectMenuCloseTimer = setTimeout(() => {
    collectMenu.value.visible = false
    collectMenuCloseTimer = null
  }, 140)
}

function closeCollectMenu() {
  cancelCloseCollectMenu()
  if (collectMenu.value.visible) collectMenu.value.visible = false
}

function onCollectTabClick() {
  closeCollectMenu()
  activeContentTab.value = 'collected'
}

function onCollectKindSelect(kind: WorkMediaKind) {
  closeCollectMenu()
  activeContentTab.value = 'collected'
  // 再点同一个类型 = 取消分离，回到全部采集内容
  collectKindFilter.value = collectKindFilter.value === kind ? null : kind
}

function collectKindCount(kind: WorkMediaKind): number {
  return collectedWorks.value.filter((w: any) => mediaOf(w).kind === kind).length
}

function clearCollectKindFilter() {
  collectKindFilter.value = null
}

function openCollectModal() {
  closeCollectMenu()
  activeContentTab.value = 'collected'
  collectModalOpen.value = true
}

function onCollectSynced() {
  collectKindFilter.value = null
  activeContentTab.value = 'collected'
  void syncWorks()
}

function toggleSidebarSearch() {
  sidebarSearchOpen.value = !sidebarSearchOpen.value
  if (sidebarSearchOpen.value) {
    sidebarSearchQuery.value = ''
    searchActiveIndex.value = 0
    nextTick(() => sidebarSearchInput.value?.focus())
  }
}

function closeSidebarSearch() {
  sidebarSearchOpen.value = false
  sidebarSearchQuery.value = ''
}

function clearSidebarSearch() {
  sidebarSearchQuery.value = ''
  sidebarSearchInput.value?.focus()
}

function onSearchResultClick(item: { id: string }) {
  closeSidebarSearch()
  emit('nav-click', 'chat')
  emit('select-conversation', item.id)
}

function formatSearchTime(ts: number): string {
  if (!ts) return ''
  const now = Date.now()
  const diff = now - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
  if (diff < 604800_000) return `${Math.floor(diff / 86400_000)} 天前`
  return new Date(ts).toLocaleDateString()
}

function onListScroll(e: Event, key: string) {
  const el = e.target as HTMLElement
  if (!el) return
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8
  listFadeState.value[key] = atBottom ? 0 : 1
}

const isChatActive = computed(() => route.path === '/chat')
const isTopicPoolActive = computed(() => route.path === '/topic-pool' || route.path.startsWith('/topic-pool/'))
const isMyWorksActive = computed(() => route.path === '/my-works')
const isTaskPlansActive = computed(() => route.path === '/task-plans')
const isPortfolioActive = computed(() => route.path === '/portfolio' || route.path.startsWith('/portfolio/'))
const isSkillsActive = computed(() => route.path === '/skills')
const isHomeActive = computed(() => route.path === '/')

const sidebarSearchLower = computed(() => sidebarSearchQuery.value.toLowerCase().trim())

const sidebarSearchResults = computed(() => {
  const q = sidebarSearchLower.value
  if (!q) return chatHistory.value.slice(0, 10)
  return chatHistory.value
    .filter(i => i.title.toLowerCase().includes(q))
    .slice(0, 20)
})

/* 输入关键词时快捷操作一起过滤，不再出现「没有匹配的结果」下面还杵着四个操作的语义打架 */
const matchedSearchActions = computed(() => {
  const q = sidebarSearchLower.value
  if (!q) return SEARCH_ACTIONS
  return SEARCH_ACTIONS.filter(a => a.label.toLowerCase().includes(q) || a.keywords.includes(q))
})

type SearchOption =
  | { kind: 'conversation'; item: { id: string; title: string; createdAt: number } }
  | { kind: 'action'; action: SearchAction }

/* 扁平选项链：对话在前、操作在后，↑↓ 才能跨两个分组连续导航 */
const searchOptions = computed<SearchOption[]>(() => [
  ...sidebarSearchResults.value.map(item => ({ kind: 'conversation' as const, item })),
  ...matchedSearchActions.value.map(action => ({ kind: 'action' as const, action })),
])

watch([sidebarSearchQuery, sidebarSearchOpen], () => { searchActiveIndex.value = 0 })

function runSearchAction(action: SearchAction) {
  action.run()
}

function activateSearchOption(index: number) {
  const opt = searchOptions.value[index]
  if (!opt) return
  if (opt.kind === 'conversation') onSearchResultClick(opt.item)
  else runSearchAction(opt.action)
}

function scrollActiveIntoView() {
  nextTick(() => {
    const el = searchModalEl.value?.querySelector<HTMLElement>('.search-modal-body .is-active')
    el?.scrollIntoView({ block: 'nearest' })
  })
}

/* IME 组词期间不抢按键；↑↓ 循环移动，Enter 执行当前项 */
function onSearchKeydown(e: KeyboardEvent) {
  if (e.isComposing) return
  const total = searchOptions.value.length
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      if (total) {
        searchActiveIndex.value = (searchActiveIndex.value + 1) % total
        scrollActiveIntoView()
      }
      break
    case 'ArrowUp':
      e.preventDefault()
      if (total) {
        searchActiveIndex.value = (searchActiveIndex.value - 1 + total) % total
        scrollActiveIntoView()
      }
      break
    case 'Enter':
      e.preventDefault()
      activateSearchOption(searchActiveIndex.value)
      break
  }
}

/* Ctrl+K 全局唤起 / Esc 全局关闭。
   Ctrl+N 与 Alt+1~8 属于 Chrome/Edge 浏览器级保留键，网页 keydown 收不到，
   所以界面不再承诺这类快捷键（旧版的 Alt+1~5、Ctrl+N 徽章都是纯装饰）。 */
function onSearchGlobalKeydown(e: KeyboardEvent) {
  const mod = e.ctrlKey || e.metaKey
  if (mod && e.key?.toLowerCase() === 'k') {
    e.preventDefault()
    toggleSidebarSearch()
    return
  }
  if (e.key === 'Escape' && sidebarSearchOpen.value) {
    e.preventDefault()
    closeSidebarSearch()
  }
}
onMounted(() => window.addEventListener('keydown', onSearchGlobalKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onSearchGlobalKeydown))

const CHAT_FILES_FOLDER_ID = 'chat-files'

const folderIdSet = computed(() => new Set(fileStore.folders.map(f => f.id)))

const unfiledChatItems = computed(() =>
  chatHistory.value.filter(item =>
    // 笔记会话（workId 有值且作品仍存在）归「内容」tab 笔记行下，不进对话平铺列表；
    // 孤儿会话（笔记已删）回落这里，不静默消失（2026-11-04 方案 A）。
    (!item.workId || isOrphanWorkChat(item.workId))
    && (
      !item.folderId
      || item.folderId === CHAT_FILES_FOLDER_ID
      || !folderIdSet.value.has(item.folderId)
    ),
  ),
)

const FOLDER_THREAD_LIMIT = 5
const expandedFolderThreads = ref<Set<string>>(new Set())
const expandedFolders = ref<Set<string>>(new Set())

function getFolderThreads(folderId: string) {
  const list = getFolderConversations(folderId)
  const q = sidebarSearchLower.value
  if (!q) return list
  return list.filter(c => c.title.toLowerCase().includes(q))
}

function isFolderExpanded(folderId: string): boolean {
  // 搜索时强制展开，否则命中的对话会被折叠隐藏，搜了等于没搜。
  if (sidebarSearchLower.value && getFolderThreads(folderId).length) return true
  return expandedFolders.value.has(folderId)
}

function expandFolder(folderId: string) {
  if (expandedFolders.value.has(folderId)) return
  const next = new Set(expandedFolders.value)
  next.add(folderId)
  expandedFolders.value = next
}

function toggleFolderExpanded(folderId: string) {
  const next = new Set(expandedFolders.value)
  if (next.has(folderId)) next.delete(folderId)
  else next.add(folderId)
  expandedFolders.value = next
}

function visibleFolderThreads(folderId: string) {
  const list = getFolderThreads(folderId)
  if (expandedFolderThreads.value.has(folderId) || list.length <= FOLDER_THREAD_LIMIT) return list
  return list.slice(0, FOLDER_THREAD_LIMIT)
}

function toggleFolderThreads(folderId: string) {
  const next = new Set(expandedFolderThreads.value)
  if (next.has(folderId)) next.delete(folderId)
  else next.add(folderId)
  expandedFolderThreads.value = next
}

function onFolderThreadClick(folderId: string, convId: string) {
  fileStore.setActiveFolder(folderId)
  expandFolder(folderId)
  emit('nav-click', 'chat')
  emit('select-conversation', convId)
}

const visibleChatItems = computed(() => {
  let items = unfiledChatItems.value
  if (sidebarSearchLower.value) {
    items = items.filter(i => i.title.toLowerCase().includes(sidebarSearchLower.value))
  }
  if (chatExpanded.value || items.length <= 7) return items
  return items.slice(0, 7)
})

const visibleFolders = computed(() => {
  let folders = fileStore.folders
  if (sidebarSearchLower.value) {
    folders = folders.filter(f =>
      f.name.toLowerCase().includes(sidebarSearchLower.value)
      || getFolderConversations(f.id).some(c => c.title.toLowerCase().includes(sidebarSearchLower.value)),
    )
  }
  if (workspaceExpanded.value || folders.length <= 7) return folders
  return folders.slice(0, 7)
})



const draftWorks = computed(() => {
  const combined = [...workStore.worksByStatus.draft]
  const existingIds = new Set(combined.map(w => w.id))
  for (const w of workStore.outputWorks) {
    if (!existingIds.has(w.id)) {
      combined.push(w)
    }
  }
  if (!sidebarSearchLower.value) return combined
  return combined.filter((w: any) => w.title?.toLowerCase().includes(sidebarSearchLower.value))
})
const collectedWorks = computed(() => {
  const works = workStore.worksByStatus.collected
  if (!sidebarSearchLower.value) return works
  return works.filter((w: any) => w.title?.toLowerCase().includes(sidebarSearchLower.value))
})
const publishedWorks = computed(() => {
  const works = workStore.worksByStatus.published
  if (!sidebarSearchLower.value) return works
  return works.filter((w: any) => w.title?.toLowerCase().includes(sidebarSearchLower.value))
})

/** 按产物形态分组（与缩略图识别规则一致，避免「短视频草稿」被归到图文） */
function groupWorksByMediaKind(works: any[]) {
  const buckets = new Map<WorkMediaKind, any[]>()
  for (const work of works) {
    const kind = mediaOf(work).kind
    const list = buckets.get(kind)
    if (list) list.push(work)
    else buckets.set(kind, [work])
  }
  return WORK_KIND_ORDER
    .filter((kind) => buckets.has(kind))
    .map((kind) => ({ kind, label: workKindLabel(kind), items: buckets.get(kind)! }))
}

/** 作品缩略图媒体信息：图片 / 封面 / 视频 / 音频 / 矢量图 / 网页 / 文本统一识别 */
const workMediaCache = computed(() => {
  const map = new Map<string, WorkMediaInfo>()
  for (const work of [...publishedWorks.value, ...draftWorks.value, ...collectedWorks.value]) {
    map.set(work.id, summarizeWorkItemMedia(work as any))
  }
  return map
})

function mediaOf(work: any): WorkMediaInfo {
  return workMediaCache.value.get(work.id) || summarizeWorkItemMedia(work)
}

const draftWorksByKind = computed(() => groupWorksByMediaKind(draftWorks.value))

// 采集列表按类型「分离」：选了图文/视频/文本时只留该类型，其余分组不渲染
const collectedWorksByKind = computed(() => {
  const kind = collectKindFilter.value
  const works = kind
    ? collectedWorks.value.filter((w: any) => mediaOf(w).kind === kind)
    : collectedWorks.value
  // 采集列表的顺序 = 作品列表顺序（workStore 已按采集时间倒序），分组不打乱先后：
  // 组内按原始下标排，分组也按组内最靠前那条的下标排，保证后采集的仍在最上面
  const rank = new Map<string, number>(works.map((w: any, i: number) => [w.id, i]))
  const rankOf = (w: any) => rank.get(w.id) ?? Number.MAX_SAFE_INTEGER
  return groupWorksByMediaKind(works)
    .map((group) => ({
      ...group,
      items: [...group.items].sort((a: any, b: any) => rankOf(a) - rankOf(b)),
    }))
    .sort((a, b) => rankOf(a.items[0]) - rankOf(b.items[0])
      || WORK_KIND_ORDER.indexOf(a.kind) - WORK_KIND_ORDER.indexOf(b.kind))
})

function getMediaKindIcon(kind: WorkMediaKind): Component {
  const map: Record<WorkMediaKind, Component> = {
    image_text: ImageIcon,
    video: Video,
    audio: Music,
    svg: ImageIcon,
    html: Code2,
    text: FileText,
    unknown: Blocks,
  }
  return map[kind] || FileText
}

/** 「采集」与「草稿」都按我的空间产出类型分组：图文 / 视频 / 音频 / 矢量图 / 网页 / 文本 */
// 「我的空间」作品同步：进入内容 Tab / 点刷新时自动拉取，避免侧边栏列表长期为空
const worksSyncing = ref(false)
const worksSyncedAt = ref(0)
let worksSyncTask: Promise<unknown> | null = null

async function syncWorks() {
  if (worksSyncTask) return worksSyncTask
  worksSyncing.value = true
  worksSyncTask = Promise.allSettled([workStore.fetchWorks(), workStore.fetchOutputWorks()])
    .then(() => {
      worksSyncedAt.value = Date.now()
    })
    .finally(() => {
      worksSyncing.value = false
      worksSyncTask = null
    })
  return worksSyncTask
}

function ensureWorksSynced(maxAgeMs = 60_000) {
  if (Date.now() - worksSyncedAt.value < maxAgeMs) return
  void syncWorks()
}

function onRefreshWorks() {
  emit('refresh-works')
  void syncWorks()
}

onMounted(() => ensureWorksSynced(0))

watch(
  () => workStore.sidebarTab,
  (tab) => {
    closeCollectMenu()
    if (tab === 'content') ensureWorksSynced()
    else closeContentFloat()
  },
)

watch(
  () => props.isCollapsed,
  (collapsed) => {
    if (collapsed) closeCollectMenu()
    // 展开侧边栏时内容面板回归常规位置，悬浮面板随之收起，避免同一面板双份显示
    else closeContentFloat()
  },
)

// ====== 收起态「内容」悬浮面板（2026-11-04）======
// 内容 Tab 下收起侧边栏后，点侧边栏上的「内容」按钮把内容导航区临时浮出来；
// 再点按钮 / 点面板外 / 切 Tab / 展开侧边栏都会收起。
const contentFloatOpen = ref(false)
const contentFloatBtnRef = ref<HTMLElement | null>(null)
const contentPanelRef = ref<HTMLElement | null>(null)
const contentFloatPos = ref<{ x: number; y: number }>({ x: 60, y: 120 })
// 展开态内容卡片的实测高度：收起后弹窗按它定高，保证「底层高度和卡片一致」
const contentCardHeight = ref<number | null>(null)
const contentFloatHeight = ref<number | null>(null)

const FLOAT_PANEL_GAP = 8
const FLOAT_PANEL_MARGIN = 12
const FLOAT_PANEL_MIN_HEIGHT = 180

const contentFloatPanelStyle = computed(() => {
  if (!props.isCollapsed || !contentFloatOpen.value) return undefined
  const style: Record<string, string> = {
    left: `${contentFloatPos.value.x}px`,
    top: `${contentFloatPos.value.y}px`,
  }
  if (contentFloatHeight.value !== null) style.height = `${contentFloatHeight.value}px`
  return style
})

// 只在展开态量高；收起态（display:none / 已浮出）量到的是 0 或弹窗自身高度，跳过
function measureContentCardHeight() {
  if (props.isCollapsed) return
  const h = contentPanelRef.value?.getBoundingClientRect().height
  if (h && h > 0) contentCardHeight.value = Math.round(h)
}

function placeContentFloatPanel() {
  const rect = contentFloatBtnRef.value?.getBoundingClientRect()
  if (!rect) return
  const viewportH = window.innerHeight
  const maxTop = Math.max(
    FLOAT_PANEL_MARGIN,
    viewportH - FLOAT_PANEL_MIN_HEIGHT - FLOAT_PANEL_MARGIN,
  )
  const top = Math.min(rect.bottom + FLOAT_PANEL_GAP, maxTop)
  contentFloatPos.value = { x: rect.right + FLOAT_PANEL_GAP, y: top }
  const available = viewportH - top - FLOAT_PANEL_MARGIN
  const target = contentCardHeight.value ?? available
  contentFloatHeight.value = Math.max(
    FLOAT_PANEL_MIN_HEIGHT,
    Math.floor(Math.min(available, target)),
  )
}

function toggleContentFloat() {
  if (contentFloatOpen.value) {
    closeContentFloat()
    return
  }
  placeContentFloatPanel()
  contentFloatOpen.value = true
  ensureWorksSynced()
}

// 窗口尺寸变化时重算锚点与高度上限，避免弹窗贴边或被裁
function onViewportResizeForContentFloat() {
  if (contentFloatOpen.value) placeContentFloatPanel()
}

function closeContentFloat() {
  if (contentFloatOpen.value) contentFloatOpen.value = false
}

// 点面板外关闭：面板与按钮内部点击不关，侧边栏空白区交由上面的 watch 兜底
function onDocPointerDownForContentFloat(e: MouseEvent) {
  if (!contentFloatOpen.value) return
  const target = e.target as HTMLElement | null
  if (!target) return
  if (target.closest('.mint-content-float-panel') || target.closest('.mint-content-float-btn')) return
  if (!target.closest('.mint-sidebar')) closeContentFloat()
}

let contentPanelObserver: ResizeObserver | null = null

onMounted(() => {
  document.addEventListener('mousedown', onDocPointerDownForContentFloat)
  window.addEventListener('resize', onViewportResizeForContentFloat)
  // 持续记录展开态内容卡片高度（内容增减、窗口缩放都会触发），供收起态弹窗定高
  if (contentPanelRef.value) {
    contentPanelObserver = new ResizeObserver(() => measureContentCardHeight())
    contentPanelObserver.observe(contentPanelRef.value)
  }
  measureContentCardHeight()
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocPointerDownForContentFloat)
  window.removeEventListener('resize', onViewportResizeForContentFloat)
  contentPanelObserver?.disconnect()
  contentPanelObserver = null
})

function goToEco() { emit('go-eco') }

function goToHome() {
  if (route.path === '/') return
  router.push('/').catch(() => {})
}

/* 两个 Tab 统一走 store 的 switchSidebarTab：它会顺手解绑内容 Tab 里选中的作品。
   chat 页收到 exit-creation 时把对话框重置回默认问候态。 */
function switchToChatTab() {
  const shouldResetChat = workStore.switchSidebarTab('chat')
  if (shouldResetChat) emit('exit-creation')
}

/* 「内容」Tab：先切到内容面板，再跳到 chat 页面。
   非 chat 页面才 push，已在 chat 页面时不重复导航。 */
function switchToContentTab() {
  workStore.switchSidebarTab('content')
  if (route.path !== '/chat') router.push('/chat').catch(() => {})
}

function onNewTaskClick() {
  emit('nav-click', 'chat')
  emit('new-chat')
}



function goToTopicPool() {
  if (route.path === '/topic-pool' || route.path.startsWith('/topic-pool/')) return
  router.push('/topic-pool').catch(() => {})
}
function goToMyWorks() {
  if (route.path === '/my-works') return
  router.push('/my-works').catch(() => {})
}
function goToTaskPlans() {
  if (route.path === '/task-plans') return
  router.push('/task-plans').catch(() => {})
}
function goToPortfolio() {
  if (route.path === '/portfolio') return
  router.push('/portfolio').catch(() => {})
}
function goToSkills() {
  if (route.path === '/skills') return
  router.push('/skills').catch(() => {})
}

function onOpenSettings() {
  openSettings()
}

function onChatHistoryClick(item: { id: string }) {
  emit('nav-click', 'chat')
  emit('select-conversation', item.id)
}

const _FILE_ICON_MAP: Record<string, Component> = {
  image: ImageIcon,
  video: Video,
  audio: Music,
  pdf: FileText,
  code: Code2,
  table: Table2,
  file: FileText,
}

function fileIcon(type?: string): Component {
  const key = fileStore.getFileIcon(type)
  return _FILE_ICON_MAP[key] || FileText
}

function onFolderClick(folderId: string) {
  fileStore.setActiveFolder(folderId)
  emit('select-folder', folderId)
  // 点文件夹这一行 = 打开/关闭：首次点开自动展开对话列表，再点收回。
  toggleFolderExpanded(folderId)
}

function onNewChatInFolder(folderId: string) {
  expandFolder(folderId)
  emit('new-chat-in-folder', folderId)
}

function onFileClick(fileId: string) {
  fileStore.setActiveFile(fileId)
  emit('nav-click', 'chat')
  emit('select-file', fileId)
}

function onConversationClick(convId: string) {
  emit('nav-click', 'chat')
  emit('select-conversation', convId)
}

function onDeleteConversation(convId: string) {
  removeChatHistory(convId)
  emit('delete-chat', convId)
}

function onDeleteFolder(folderId: string) {
  unfileConversations(folderId)
  fileStore.removeFolder(folderId)
}

const folderMenu = ref<{ visible: boolean; x: number; y: number; folder: FolderItem | null }>({
  visible: false, x: 0, y: 0, folder: null,
})
const folderManagerOpen = ref(false)
const toastMessage = ref('')
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(message: string) {
  toastMessage.value = message
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMessage.value = '' }, 2600)
}

function openFolderMenu(event: MouseEvent, folder: FolderItem) {
  const x = Math.min(event.clientX, window.innerWidth - 200)
  const y = Math.min(event.clientY, window.innerHeight - 110)
  folderMenu.value = { visible: true, x, y, folder }
}

function closeFolderMenu() {
  folderMenu.value.visible = false
}

function onMenuFolderManager() {
  closeFolderMenu()
  folderManagerOpen.value = true
}

async function onMenuRevealFolder() {
  const folder = folderMenu.value.folder
  closeFolderMenu()
  if (!folder) return
  if (!isAbsoluteLocalPath(folder.path)) {
    showToast('该文件夹没有可打开的本地路径')
    return
  }
  try {
    await revealFolder(folder.path)
  } catch (error: any) {
    showToast(error?.response?.data?.message || '打开文件夹失败')
  }
}

function onCreationTypeClick(typeKey: string) {
  workStore.activeCreationType = typeKey as any
  emit('switch-creation-panel', typeKey)
}

function onSelectWork(workId: string) {
  workStore.setActiveWork(workId)
  emit('select-work', workId)
  // 点作品即展开它的会话列表：ChatView 监听 activeWork 变化，
  // 会自动切到该作品最近的会话（没有会话时新建一个绑定该作品的空会话）。
  if (!expandedWorkThreads.value.has(workId)) {
    const next = new Set(expandedWorkThreads.value)
    next.add(workId)
    expandedWorkThreads.value = next
  }
}

async function onDeleteWork(workId: string) {
  const ok = await workStore.deleteWork(workId)
  if (!ok) {
    showToast('删除失败，请稍后重试')
  }
}

async function onImportFolder() {
  const folder = await fileStore.importFolder()
  if (folder) {
    fileStore.setActiveFolder(folder.id)
    emit('select-folder', folder.id)
  }
}

function onNewFolderChat() {
  const folderId = fileStore.activeFolderId
  if (folderId) {
    emit('new-chat-in-folder', folderId)
  }
}

function getConversationsForFolder(folderId: string) {
  return getFolderConversations(folderId)
}

function toggleDraftExpanded() { draftExpanded.value = !draftExpanded.value }
function toggleCollectedExpanded() { collectedExpanded.value = !collectedExpanded.value }
function togglePublishedExpanded() { publishedExpanded.value = !publishedExpanded.value }

function formatRelativeTime(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diff = now - then
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前'
  if (diff < 604800000) return Math.floor(diff / 86400000) + '天前'
  return new Date(dateStr).toLocaleDateString()
}

const contextMenu = ref<{ visible: boolean; x: number; y: number; convId: string }>({
  visible: false,
  x: 0,
  y: 0,
  convId: ''
})

function onChatContextMenu(e: MouseEvent, convId: string) {
  const x = Math.min(e.clientX, window.innerWidth - 140)
  const y = Math.min(e.clientY, window.innerHeight - 40)
  contextMenu.value = { visible: true, x, y, convId }
}

function closeContextMenu() { contextMenu.value.visible = false }

function onDeleteChat(convId: string) {
  removeChatHistory(convId)
  emit('delete-chat', convId)
  closeContextMenu()
}
</script>

<style>
.mint-logo-text {
  height: 40px !important;
  margin-top: -28px !important;
}
.mint-logo-crab {
  width: 40px !important;
  height: 40px !important;
  padding: 3px !important;
  border-radius:14px !important;
  transform: scale(1.3) !important;
}
.mint-logo-icon {
  width: 40px !important;
  height: 40px !important;
}
.mint-shell.mint-collapsed .mint-logo-top {
  width: 40px !important;
  height: 40px !important;
}
.mint-sidebar svg {
  color: #1A1A1A !important;
  stroke: #1A1A1A !important;
}
.mint-sidebar-toggle svg {
  color: #666 !important;
  stroke: #666 !important;
}
.mint-logo-top:hover {
  outline: none !important;
  border: none !important;
  box-shadow: none !important;
}
.mint-logo-top:focus,
.mint-logo-top:active {
  outline: none !important;
  border: none !important;
  box-shadow: none !important;
}
.mint-nav {
  gap: 2px !important;
}
.mint-nav > .mint-nav-item {
  padding-top: 7px !important;
  padding-bottom: 7px !important;
}
.mint-nav-item:hover {
  background: #E8E8E8 !important;
  color: #1A1A1A !important;
}
.mint-nav-item:hover svg {
  color: var(--rl-text-2) !important;
}
.mint-nav-active {
  background: rgba(249, 115, 22, 0.08) !important;
  color: #1A1A1A !important;
  font-weight: 500 !important;
}
.mint-nav-active svg {
  color: #1A1A1A !important;
}
.mint-content-subgroup {
  margin-bottom: 4px;
}
.mint-content-subgroup-header {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px 2px 20px;
  font-size: 11px;
  color: var(--mint-text-secondary, #888);
  font-weight: 500;
}
.mint-ws-thread-blurred {
  filter: blur(1.5px);
  opacity: 0.5;
  pointer-events: none;
  user-select: none;
}
.mint-chat-show-more {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--mint-text-secondary, #888);
  cursor: pointer;
  border-radius:10px;
  transition: background 0.15s ease, color 0.15s ease;
}
.mint-chat-show-more:hover {
  background: rgba(0, 0, 0, 0.04);
  color: var(--rl-text-2);
}
.mint-ws-content > .mint-chat-show-more {
  padding-left: calc(var(--ws-indent, 12px) + 19px);
}
.mint-scrollable-list {
  max-height: 360px;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 0, 0, 0.15) transparent;
  position: relative;
}
.mint-scrollable-list::-webkit-scrollbar {
  width: 5px;
}
.mint-scrollable-list::-webkit-scrollbar-track {
  background: transparent;
}
.mint-scrollable-list::-webkit-scrollbar-thumb {
  background-color: rgba(0, 0, 0, 0.15);
  border-radius:6px;
}
.mint-scrollable-list::-webkit-scrollbar-thumb:hover {
  background-color: rgba(0, 0, 0, 0.25);
}
.mint-scroll-fade::after {
  content: '';
  position: sticky;
  bottom: 0;
  left: 0;
  right: 0;
  display: block;
  height: 48px;
  background: linear-gradient(to bottom, rgba(255,255,255,0), rgba(255,255,255,1));
  pointer-events: none;
  margin-top: -48px;
  transition: opacity 0.2s ease;
}
.mint-creation-chevron {
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
  color: var(--mint-text-secondary, #888);
}
.mint-creation-chevron-open {
  transform: rotate(90deg);
}
.mint-creation-group-header {
  cursor: pointer;
  user-select: none;
  position: relative;
}
.mint-creation-group-header:hover .mint-creation-chevron {
  color: var(--rl-text-2, #1A1A1A);
}
.mint-creation-count {
  margin-left: auto;
  font-size: 11px;
  color: var(--mint-text-secondary, #888);
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 6px;
  border-radius:14px;
  font-weight: 500;
}
.mint-creation-add-btn {
  margin-left: 4px !important;
}
/* ====== 内容面板二级导航（下划线式） ====== */
.mint-content-tabs {
  position: relative;
  display: flex;
  gap: 2px;
  margin: 2px calc(var(--sb-row-pad-x) + 4px) 6px;
  padding: 0;
  background: none;
  border-radius: 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.07);
}
.mint-content-tab {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: clamp(3px, calc(0.66cqi + 1.6px), 6px);
  padding: var(--sb-tab-pad-y) 2px calc(var(--sb-tab-pad-y) + 1px);
  border: none;
  background: transparent;
  border-radius: 8px 8px 0 0;
  font-size: var(--sb-tab-font);
  font-weight: 500;
  color: #8E8E93;
  cursor: pointer;
  transition: color 0.18s ease, background-color 0.18s ease;
  font-family: inherit;
  /* 文字永不逐字换行，窄宽度走下方 @container 降级 */
  white-space: nowrap;
  min-width: 0;
  overflow: hidden;
}
.mint-content-tab > span {
  white-space: nowrap;
}
.mint-content-tab svg {
  flex: none;
  width: var(--sb-tab-icon);
  height: var(--sb-tab-icon);
  opacity: 0.7;
  transition: opacity 0.18s ease;
}
/* 选中指示条：从中心展开 */
.mint-content-tab::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -1px;
  width: 0;
  height: 2px;
  border-radius: 2px;
  background: #1A1A1A;
  transform: translateX(-50%);
  transition: width 0.24s cubic-bezier(0.4, 0, 0.2, 1), background-color 0.18s ease;
}
.mint-content-tab:hover {
  color: #3A3A3C;
  background: rgba(0, 0, 0, 0.022);
}
.mint-content-tab:hover svg {
  opacity: 1;
}
.mint-content-tab:hover::after {
  width: 26%;
  background: rgba(0, 0, 0, 0.16);
}
.mint-content-tab-active {
  background: rgba(249, 115, 22, 0.08);
  color: #1A1A1A;
  font-weight: 500;
}
.mint-content-tab-active svg {
  opacity: 1;
}
.mint-content-tab-active::after {
  width: 58%;
  background: #1A1A1A;
}
/* 窄侧边栏降级：先去图标、再隐藏计数 */
@container (max-width: 200px) {
  .mint-content-tab {
    gap: 0;
    padding: 8px 1px 9px;
    font-size: 11px;
  }
  .mint-content-tab svg {
    display: none;
  }
}
@container (max-width: 176px) {
  .mint-tab-count {
    display: none;
  }
}
/* 计数：去掉胶囊底色，改为轻量数字 */
.mint-tab-count {
  font-size: clamp(10px, calc(0.4cqi + 9.17px), 12px);
  font-weight: 500;
  color: #B2B2B7;
  background: none;
  padding: 0;
  border-radius: 0;
  min-width: 0;
  text-align: center;
  line-height: 1.4;
  flex: none;
  font-variant-numeric: tabular-nums;
  transition: color 0.18s ease;
}
.mint-content-tab-active .mint-tab-count {
  color: #6E6E73;
}
.mint-collect-action {
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  /* 钉在面板底部：上方列表 flex:1 吃满剩余高度（2026-11-04 大哥反馈） */
  flex: none;
  margin-top: auto;
  padding: clamp(8px, calc(2.63cqi + 2.42px), 16px) clamp(12px, calc(1.97cqi + 7.82px), 18px);
}
/* 底部「添加采集内容」按钮：宽侧栏时同步长高，避免整条按钮被压成细线 */
.mint-collect-action .mint-creation-toolbar-btn {
  width: 100%;
  height: auto;
  justify-content: center;
  gap: clamp(4px, calc(0.99cqi + 1.91px), 8px);
  padding: clamp(8px, calc(1.32cqi + 5.21px), 12px) 0;
  border-radius: clamp(12px, calc(1.97cqi + 7.82px), 18px);
  font-size: var(--sb-title);
  color: #4B4B4B;
}

/* ====== 采集类型悬浮菜单（图文 / 视频 / 文本） ====== */
.mint-collect-menu {
  position: fixed;
  z-index: var(--z-top);
  width: 208px;
  padding: 6px;
  background: #fff;
  border: 1px solid #EDEDED;
  border-radius: 14px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.12), 0 2px 6px rgba(0, 0, 0, 0.06);
  animation: mint-context-in 0.15s ease-out;
}
.mint-collect-menu-title {
  padding: 4px 8px 6px;
  font-size: 11px;
  font-weight: 500;
  color: #8E8E93;
  letter-spacing: 0.02em;
}
.mint-collect-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  background: none;
  border-radius: 10px;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s ease;
}
.mint-collect-menu-item:hover {
  background: #F5F5F7;
}
.mint-collect-menu-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: #F2F2F5;
  color: #1A1A1A;
}
.mint-collect-menu-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.mint-collect-menu-label {
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.mint-collect-menu-desc {
  font-size: 11px;
  color: #8E8E93;
  line-height: 1.3;
}
.mint-collect-menu-count {
  margin-left: auto;
  padding-left: 6px;
  font-size: 11px;
  color: #B2B2B7;
  font-variant-numeric: tabular-nums;
}
.mint-collect-menu-item.is-active {
  background: #F2F2F5;
}
.mint-collect-menu-item.is-active .mint-collect-menu-count {
  color: #1A1A1A;
}

/* ====== 采集类型分离条（列表顶部，显示当前只看的类型） ====== */
.mint-collect-filter-bar {
  display: flex;
  padding: 0 clamp(12px, calc(1.97cqi + 7.82px), 18px) 2px;
}
.mint-collect-filter-chip {
  display: inline-flex;
  align-items: center;
  gap: clamp(5px, calc(0.66cqi + 3.6px), 8px);
  padding: 3px 4px 3px clamp(8px, calc(1.32cqi + 5.21px), 12px);
  border-radius: 999px;
  background: #F2F2F5;
  font-size: var(--sb-meta, 11px);
  font-weight: 500;
  color: #1A1A1A;
}
.mint-collect-filter-count {
  color: #8E8E93;
  font-variant-numeric: tabular-nums;
}
.mint-collect-filter-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: clamp(16px, calc(1.32cqi + 13.21px), 20px);
  height: clamp(16px, calc(1.32cqi + 13.21px), 20px);
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.06);
  color: #6E6E73;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.mint-collect-filter-clear:hover {
  background: rgba(0, 0, 0, 0.12);
  color: #1A1A1A;
}

/* ====== 搜索命令面板（2026-11-06 重做：极淡紫选中态 + 键盘导航）====== */
.search-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 12vh;
  z-index: 9999;
}
.search-modal {
  width: 556px;
  max-width: calc(100vw - 32px);
  max-height: 72vh;
  background: #FFFFFF;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 14px;
  box-shadow:
    0 24px 72px rgba(0, 0, 0, 0.18),
    0 8px 24px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* 搜索行通栏 + 底部分隔线，替代原来悬空的灰底胶囊 */
.search-modal-field {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 15px 18px;
  border-bottom: 0.5px solid rgba(0, 0, 0, 0.08);
  flex-shrink: 0;
}
.search-modal-icon {
  color: #9A9A9A;
  flex-shrink: 0;
  transition: color 0.16s ease;
}
.search-modal-field:focus-within .search-modal-icon { color: #534AB7; }
.search-modal-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  font-weight: 400;
  color: #1D1D1F;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
  line-height: 1.4;
  letter-spacing: -0.01em;
  padding: 0;
}
.search-modal-input::placeholder {
  color: #A8A8AD;
  font-weight: 400;
}
.search-modal-esc {
  font-size: 11px;
  color: #9A9A9A;
  background: #F5F5F7;
  border: 0.5px solid rgba(0, 0, 0, 0.06);
  border-radius: 5px;
  padding: 2px 6px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  flex-shrink: 0;
}
.search-modal-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 50%;
  color: #8E8E93;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}
.search-modal-clear:hover {
  background: #E8E8E8;
  color: #666;
}
/* 两个分组共用一个滚动容器，footer 始终固定在底部 */
.search-modal-body {
  overflow-y: auto;
  padding-bottom: 10px;
  flex: 1;
  min-height: 0;
}
.search-modal-body::-webkit-scrollbar { width: 6px; }
.search-modal-body::-webkit-scrollbar-track { background: transparent; }
.search-modal-body::-webkit-scrollbar-thumb { background: #DDD; border-radius: 6px; }
.search-modal-section-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 20px 5px;
  font-size: 11px;
  font-weight: 500;
  color: #9A9A9A;
  letter-spacing: 0.04em;
}
.search-modal-section-count {
  font-weight: 400;
  color: #B4B2A9;
  letter-spacing: 0;
}
.search-modal-results,
.search-modal-actions { padding: 2px 8px 6px; }
/* 选项行：选中态极淡紫 + 左侧紫条。旧版 hover 用白底（白底上白底）等于没有反馈 */
.search-modal-item,
.search-modal-action {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
  font-family: inherit;
  font-size: 13px;
  color: #3A3A3A;
  text-align: left;
}
.search-modal-item:hover,
.search-modal-action:hover { background: #F7F7F9; }
.search-modal-item.is-active,
.search-modal-action.is-active,
.search-modal-item.is-active:hover,
.search-modal-action.is-active:hover {
  background: #F7F6FE;
  color: #3C3489;
}
.search-modal-item.is-active::before,
.search-modal-action.is-active::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: #534AB7;
}
.search-modal-item-title {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: inherit;
}
.search-modal-item-time {
  font-size: 11px;
  color: #AAA;
  flex-shrink: 0;
}
.search-modal-item.is-active .search-modal-item-time { color: #7F77DD; }
.search-modal-item-enter {
  font-size: 11px;
  color: #FFFFFF;
  background: #534AB7;
  padding: 2px 7px;
  border-radius: 5px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  flex-shrink: 0;
}
.search-modal-action-icon {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: #F1EFE8;
  color: #5F5E5A;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.search-modal-action.is-primary .search-modal-action-icon {
  background: #EEEDFE;
  color: #534AB7;
}
.search-modal-action-label {
  flex: 1;
  text-align: left;
}
.search-modal-empty {
  padding: 36px 22px;
  text-align: center;
  color: #A8A8AD;
  font-size: 13px;
}
.search-modal-divider {
  height: 1px;
  background: rgba(0, 0, 0, 0.07);
  margin: 8px 18px 2px;
}
.search-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 18px;
  border-top: 0.5px solid rgba(0, 0, 0, 0.08);
  background: #FAFAFB;
  flex-shrink: 0;
}
.search-modal-footer-hints {
  display: flex;
  align-items: center;
  gap: 12px;
}
.search-modal-footer-hints span,
.search-modal-footer-hint {
  font-size: 11px;
  color: #9A9A9A;
  white-space: nowrap;
}
.search-modal-footer kbd {
  font-size: 11px;
  color: #888;
  background: #FFFFFF;
  border: 0.5px solid rgba(0, 0, 0, 0.10);
  border-radius: 4px;
  padding: 1px 4px;
  margin-right: 2px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* 弹窗过渡动画 */
.search-modal-enter-active { transition: opacity 0.18s ease-out; }
.search-modal-leave-active { transition: opacity 0.12s ease-in; }
.search-modal-enter-active .search-modal { transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.18s ease-out; }
.search-modal-leave-active .search-modal { transition: transform 0.14s ease-in, opacity 0.12s ease-in; }
.search-modal-enter-from,
.search-modal-leave-to { opacity: 0; }
.search-modal-enter-from .search-modal { transform: scale(0.95) translateY(-12px); opacity: 0; }
.search-modal-leave-to .search-modal { transform: scale(0.97); opacity: 0; }

/* ====== 收起态「内容」悬浮面板（2026-11-04）====== */
/* 收起侧边栏后内容面板默认隐藏；点「内容」按钮（mint-content-float-open）临时浮出 */
.mint-shell.mint-collapsed .mint-creation-panel { display: none; }
.mint-shell.mint-collapsed .mint-content-float-open .mint-creation-panel {
  display: flex;
  position: fixed;
  z-index: var(--z-top, 1000);
  left: 60px;
  top: 120px; /* 由内联 style 按「内容」按钮位置动态覆盖 */
  width: 248px;
  /* 高度与展开态内容卡片一致：height 由内联 style 用展开态实测高度给出（不足则按视口兜底），
     内容再多也只在面板内部滚动，不会把底部「添加采集内容」按钮顶出视口 */
  height: auto;
  max-height: calc(100vh - 24px);
  margin-top: 0;
  flex: none;
  padding: 10px 8px 8px;
  overflow-y: auto;
  /* 透明卡片：半透明白 + 毛玻璃，透出底下的聊天/工作区，仍保证文字可读 */
  background: rgba(255, 255, 255, 0.62);
  -webkit-backdrop-filter: blur(22px) saturate(180%);
  backdrop-filter: blur(22px) saturate(180%);
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 14px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.14), 0 2px 8px rgba(0, 0, 0, 0.06);
  animation: mint-context-in 0.15s ease-out;
  /* 收起态浮层脱离侧边栏宽度（固定 248px），而容器仍只有 56px，
     流体尺寸会全部落到 clamp 下限；这里按浮层实际内容宽（约 232px）锁回定值 */
  --sb-thumb: 46px;
  --sb-thumb-radius: 6px;
  --sb-row-pad-y: 12px;
  --sb-row-pad-x: 8px;
  --sb-row-gap: 10px;
  --sb-row-radius: 8px;
  --sb-title: 13px;
  --sb-meta: 11px;
  --sb-action: 21px;
  --sb-action-radius: 4px;
  --sb-list-gap: 4px;
  --sb-indent: 8px;
  --sb-tab-font: 12px;
  --sb-tab-pad-y: 8px;
  --sb-tab-icon: 13px;
  --sb-subgroup-font: 11px;
  --sb-subgroup-icon: 18px;
  --sb-thread-font: 12px;
  --sb-thread-pad-y: 6px;
  --sb-thread-pad-x: 8px;
}
/* 透明卡片下的列表淡出层跟随面板底色，避免出现一块突兀的白 */
.mint-shell.mint-collapsed .mint-content-float-open .mint-content-float-panel .mint-scroll-fade::after {
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0), rgba(255, 255, 255, 0.55));
}
/* 悬浮面板宽度足够，恢复被 56px 容器降级隐藏的 tab 图标与计数 */
.mint-shell.mint-collapsed .mint-content-float-open .mint-content-tab svg { display: inline-flex; }
.mint-shell.mint-collapsed .mint-content-float-open .mint-tab-count { display: inline; }
/* 收起态「内容」入口按钮：与其它收起态图标钮同列同风格 */
.mint-content-float-btn {
  justify-content: center;
  flex-shrink: 0;
  cursor: pointer;
}
.mint-content-float-btn svg {
  color: #1A1A1A;
}

</style>