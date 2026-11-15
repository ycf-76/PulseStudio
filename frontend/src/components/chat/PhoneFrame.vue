<script setup lang="ts">
/**
 * PhoneFrame —— 可复用手机外框（P2）
 *
 * 把 FinalReviewCard「发布预览」节点的手机外框语言抽成独立组件，
 * 供多处复用：承接卡分镜预览、早期封面预览、终审预览等，
 * 保证「手机预览」在全链路视觉一致，且能提前到创作起点。
 *
 * variant:
 *  - full：完整外框（状态栏 + 刘海 + home 指示条），用于真实预览
 *  - mini：紧凑外框（仅圆角 + 小刘海），用于分镜条里的页面缩略预览
 */
defineProps<{
  variant?: 'full' | 'mini'
}>()
</script>

<template>
  <div class="pf" :class="variant === 'mini' ? 'pf-mini' : 'pf-full'">
    <template v-if="variant === 'mini'">
      <div class="pf-mini-island"></div>
      <div class="pf-screen pf-mini-screen">
        <slot />
      </div>
    </template>
    <template v-else>
      <span class="pf-key pf-key-action"></span>
      <span class="pf-key pf-key-vol-up"></span>
      <span class="pf-key pf-key-vol-down"></span>
      <span class="pf-key pf-key-power"></span>
      <div class="pf-screen">
        <div class="pf-statusbar">
          <span class="pf-time">9:41</span>
          <div class="pf-island"></div>
          <div class="pf-status-icons">
            <svg class="pf-ico" width="17" height="11" viewBox="0 0 17 11" aria-hidden="true">
              <rect x="0" y="7.6" width="3" height="3.4" rx="1" fill="#000" />
              <rect x="4.65" y="5.2" width="3" height="5.8" rx="1" fill="#000" />
              <rect x="9.3" y="2.6" width="3" height="8.4" rx="1" fill="#000" />
              <rect x="13.95" y="0" width="3" height="11" rx="1" fill="#000" />
            </svg>
            <svg class="pf-ico" width="16" height="12" viewBox="0 0 16 12" fill="none" aria-hidden="true">
              <path d="M1.2 4.2a10.2 10.2 0 0 1 13.6 0" stroke="#000" stroke-width="1.9" stroke-linecap="round" />
              <path d="M3.9 7.2a6.1 6.1 0 0 1 8.2 0" stroke="#000" stroke-width="1.9" stroke-linecap="round" />
              <circle cx="8" cy="10.2" r="1.45" fill="#000" />
            </svg>
            <svg class="pf-ico" width="25" height="12" viewBox="0 0 25 12" aria-hidden="true">
              <rect x="0.6" y="0.6" width="20.8" height="10.8" rx="3.2" fill="none" stroke="#000" stroke-opacity="0.3" stroke-width="1.2" />
              <rect x="2.2" y="2.2" width="17.6" height="7.6" rx="1.9" fill="#000" />
              <path d="M23 4.3c.9.3 1.4 1 1.4 1.7s-.5 1.4-1.4 1.7V4.3Z" fill="#000" fill-opacity="0.4" />
            </svg>
          </div>
        </div>
        <slot />
        <div class="pf-home-bar"></div>
      </div>
      <div class="pf-seam"></div>
    </template>
  </div>
</template>

<style scoped>
/* ===== full：iPhone 15 Pro 语言 =====
 * 比例对齐真机：机身 146.6×70.6mm ≈ 1:2.08，所以 325 宽对应 676 高。
 * 下游 CardPreviewPane 用 PHONE_H 常量算等比缩放，两处必须同步。
 * 边框 6px，屏幕内宽 = 325 - 12 = 313，需同步 SCREEN_W。
 *
 * 投影分四层叠，模拟物体压在纸面上的真实光照：
 *   ① 接触影  偏移 1px、几乎不模糊 —— 交代机身落在哪，去掉「悬空漂浮」感
 *   ② 近影    6px  —— 边缘的硬转折，避免影子一上来就糊成一片
 *   ③ 中影    16px —— 主要离地感
 *   ④ 远影    36px + 大负扩散 —— 只留最淡的一层收尾，不再是底部一条深带
 * 关键：前几层扩散为正、偏移小，左右两侧才透得出来；
 * 之前那层 `0 36px 62px -16px` 负扩散把矩形四边各缩 16px，
 * 导致只有正下方漏出影子、左右几乎贴边就断，看着像垫了块抹布。
 */
.pf-full {
  width: 325px;
  height: 676px;
  padding: 6px;
  border-radius: 52px;
  position: relative;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(130% 62% at 16% -4%, rgba(255, 255, 255, 0.3) 0%, rgba(255, 255, 255, 0) 58%),
    radial-gradient(110% 55% at 102% 104%, rgba(255, 255, 255, 0.14) 0%, rgba(255, 255, 255, 0) 62%),
    linear-gradient(152deg, #5e6066 0%, #3b3d42 18%, #27282c 48%, #232427 74%, #43454a 100%);
  box-shadow:
    0 1px 2px rgba(17, 20, 28, 0.04),
    0 6px 14px -2px rgba(17, 20, 28, 0.055),
    0 16px 32px -8px rgba(17, 20, 28, 0.072),
    0 36px 56px -22px rgba(17, 20, 28, 0.095),
    inset 0 0 0 1px rgba(255, 255, 255, 0.26),
    inset 0 1px 0 rgba(255, 255, 255, 0.42),
    inset 0 -1px 0 rgba(0, 0, 0, 0.42);
}

/* 侧键：宽度比旧版 2px 略厚，两端收圆，颜色跟随机身金属，
 * 只露 1.5px 在机身之外，避免读成一根细线。 */
.pf-key {
  position: absolute;
  width: 2.5px;
  z-index: 1;
  background: linear-gradient(180deg, #6c6e74 0%, #43454a 52%, #2a2b2f 100%);
  box-shadow: inset 0 0 0 0.5px rgba(255, 255, 255, 0.2);
}
.pf-key-action {
  left: -1px;
  top: 112px;
  height: 26px;
  border-radius: 2px 0 0 2px;
}
.pf-key-vol-up {
  left: -1px;
  top: 152px;
  height: 54px;
  border-radius: 2px 0 0 2px;
}
.pf-key-vol-down {
  left: -1px;
  top: 218px;
  height: 54px;
  border-radius: 2px 0 0 2px;
}
.pf-key-power {
  right: -1px;
  top: 194px;
  height: 76px;
  border-radius: 0 2px 2px 0;
}

/* 屏幕与机身之间的黑色边框：外缘 47.5，向内 1.5px 后与屏幕 46 同心。
 * 不参与布局，所以屏幕内宽仍是 325 - 12 = 313。 */
.pf-seam {
  position: absolute;
  inset: 4.5px;
  border-radius: 47.5px;
  border: 1.5px solid #08080a;
  pointer-events: none;
  z-index: 2;
}

/* 同心圆角：外壳 52 - 边框 6 = 屏幕 46，用 flex 撑满避免手算高度 */
.pf-full .pf-screen {
  flex: 1;
  min-height: 0;
  border-radius: 46px;
  overflow: hidden;
  background: #ffffff;
  position: relative;
  display: flex;
  flex-direction: column;
}

/* 状态栏按真机规格重排：高度 44（≈54pt），
 * 时间 / 灵动岛 / 图标组三者垂直居中对齐在同一条中轴线上。
 * 之前图标是手绘的扁方块（信号 13×10、电量 22×10），比例失真，
 * 现在信号 17×11、WiFi 16×12、电量 25×12，接近 iOS 真实图标比例。 */
.pf-statusbar {
  position: relative;
  flex-shrink: 0;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 22px;
  background: #ffffff;
  z-index: 1;
  font-family: -apple-system, 'SF Pro Display', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.pf-time {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.1px;
  color: #000;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

/* 灵动岛：100×30（≈125×37pt），垂直居中在状态栏里 */
.pf-island {
  position: absolute;
  left: 50%;
  top: 7px;
  transform: translateX(-50%);
  width: 100px;
  height: 30px;
  border-radius: 15px;
  background: #000;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.06);
}

/* 图标组：从右往左依次是电量 / WiFi / 信号，间距 6px */
.pf-status-icons {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
  flex-shrink: 0;
}

.pf-ico { display: block; flex-shrink: 0; }

/* home 指示条：像真机一样浮在屏幕内容之上，不占布局高度。
 * bottom 7px 让它落在底部安全区，不会压住内容区的图标。 */
.pf-home-bar {
  position: absolute;
  left: 50%;
  bottom: 7px;
  transform: translateX(-50%);
  width: 134px;
  height: 5px;
  border-radius: 3px;
  background: rgba(26, 26, 26, 0.32);
  z-index: 3;
  pointer-events: none;
}

/* ===== mini：分镜条缩略预览，同一套机身语言 ===== */
.pf-mini {
  width: 58px;
  height: 82px;
  padding: 2.5px;
  border-radius: 15px;
  position: relative;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(130% 62% at 16% -4%, rgba(255, 255, 255, 0.28) 0%, rgba(255, 255, 255, 0) 58%),
    linear-gradient(150deg, #5e6066 0%, #34363b 34%, #26272b 100%);
  box-shadow:
    0 1px 2px rgba(17, 20, 28, 0.05),
    0 4px 10px -2px rgba(17, 20, 28, 0.07),
    inset 0 0 0 1px rgba(255, 255, 255, 0.24);
}
.pf-mini-island {
  position: absolute;
  left: 50%;
  top: 5px;
  transform: translateX(-50%);
  width: 16px;
  height: 4px;
  border-radius: 2px;
  background: #050506;
  z-index: 2;
}
.pf-mini-screen {
  border-radius: 12px;
  background: #f8fafc;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 6px 4px;
  overflow: hidden;
}
</style>