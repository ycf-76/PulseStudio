<template>
  <Teleport to="body">
    <Transition name="rl-modal-fade">
      <div v-if="visible" class="rl-confirm-overlay" @click.self="onCancel">
        <div
          class="rl-confirm-dialog"
          :class="{ 'rl-confirm-danger': danger }"
          role="alertdialog"
          aria-modal="true"
        >
          <div class="rl-confirm-header">
            <span class="rl-confirm-icon">{{ danger ? '⚠️' : 'ℹ️' }}</span>
            <span class="rl-confirm-title">{{ title }}</span>
          </div>
          <div class="rl-confirm-body">
            <p v-for="(line, i) in messageLines" :key="i" class="rl-confirm-line">{{ line }}</p>
          </div>
          <div class="rl-confirm-actions">
            <button class="rl-confirm-btn rl-confirm-cancel" @click="onCancel">{{ cancelText }}</button>
            <button
              class="rl-confirm-btn rl-confirm-ok"
              :class="{ 'rl-confirm-ok-danger': danger }"
              @click="onConfirm"
            >
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    visible: boolean
    title?: string
    message?: string
    confirmText?: string
    cancelText?: string
    danger?: boolean
  }>(),
  {
    title: '确认操作',
    message: '',
    confirmText: '确定',
    cancelText: '取消',
    danger: false,
  },
)

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
  (e: 'update:visible', value: boolean): void
}>()

const messageLines = computed(() => (props.message || '').split('\n'))

function onConfirm() {
  emit('update:visible', false)
  emit('confirm')
}

function onCancel() {
  emit('update:visible', false)
  emit('cancel')
}
</script>

<style scoped>
.rl-confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(17, 24, 39, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10020;
  backdrop-filter: blur(2px);
}

.rl-confirm-dialog {
  width: min(440px, 90vw);
  background: #ffffff;
  border-radius:16px;
  padding: 20px 22px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  border-top: 4px solid var(--rl-accent);
  font-family: inherit;
}

.rl-confirm-dialog.rl-confirm-danger {
  border-top-color: var(--rl-red);
}

.rl-confirm-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.rl-confirm-icon {
  font-size: 18px;
  line-height: 1;
}

.rl-confirm-title {
  font-size: 15px;
  font-weight: 500;
  color: #1f2937;
}

.rl-confirm-body {
  margin-bottom: 18px;
}

.rl-confirm-line {
  margin: 0 0 4px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--rl-text-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.rl-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.rl-confirm-btn {
  padding: 8px 18px;
  /* main.css 的 button { border-radius: 9999px !important } 会把弹窗按钮拉成胶囊，
     同级 !important + 类属性选择器（0-2-0 > 0-0-1）夺回方角（2026-11-04） */
  border-radius: 12px !important;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s ease;
}

.rl-confirm-cancel {
  /* fallback 硬保险：弹窗 Teleport 到 body，部分 --rl-* 变量只在 .rl-chat 局部定义
     （--rl-accent 就不在 :root），取不到时按钮=透明底白字=隐形（大哥报的确认按钮消失） */
  background: var(--rl-border-thin, #F5F5F5);
  color: var(--rl-text-2, #4B4B4B);
  border-color: var(--rl-border, rgba(0, 0, 0, 0.1));
}

.rl-confirm-cancel:hover {
  background: var(--rl-border, #E5E5E5);
}

.rl-confirm-ok {
  background: var(--rl-accent, #534AB7);
  color: #ffffff;
}

.rl-confirm-ok:hover {
  filter: brightness(1.05);
}

.rl-confirm-ok-danger {
  background: var(--rl-red, #B42318);
  color: #ffffff;
}

.rl-confirm-ok-danger:hover {
  background: var(--rl-red, #B42318);
}

.rl-modal-fade-enter-active,
.rl-modal-fade-leave-active {
  transition: opacity 0.18s ease;
}

.rl-modal-fade-enter-from,
.rl-modal-fade-leave-to {
  opacity: 0;
}
</style>