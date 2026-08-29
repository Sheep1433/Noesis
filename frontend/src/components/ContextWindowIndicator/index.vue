<script setup lang="ts">
import type { ContextWindowSnapshot } from '@/views/chat/messageParts'
import { computed, ref } from 'vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { formatTokenCount } from '@/views/chat/messageParts'

const props = defineProps<{
  context: ContextWindowSnapshot
}>()

// top-end 右缘对齐触发器向左展开：移动端触发器贴右缘时面板左端会溢出屏幕，
// 换 top 居中对齐，配合 min(360px, 100vw - 32px) 宽度可完整落在视口内
const { isMobile } = useBreakpoint()
const placement = computed(() => (isMobile.value ? 'top' : 'top-end'))
const panelOpen = ref(false)
const percentage = computed(() => Math.min(100, Math.max(0, Math.round(props.context.used_percentage))))

const ringColor = computed(() => {
  if (percentage.value >= 85) {
    return 'var(--noesis-color-danger)'
  }
  if (percentage.value >= 60) {
    return 'var(--noesis-color-warning)'
  }
  return 'var(--noesis-color-text-muted)'
})

const dashOffset = computed(() => {
  const circumference = 2 * Math.PI * 9
  return circumference * (1 - percentage.value / 100)
})
</script>

<template>
  <n-popover
    v-model:show="panelOpen"
    :placement="placement"
    trigger="click"
    :show-arrow="false"
    raw
  >
    <template #trigger>
      <div
        class="context-window-indicator"
        role="status"
        :aria-label="`上下文占用 ${percentage}%，点击查看用量`"
        tabindex="0"
        @keydown.enter.prevent="panelOpen = !panelOpen"
        @keydown.space.prevent="panelOpen = !panelOpen"
      >
        <svg
          class="context-window-indicator__ring"
          width="22"
          height="22"
          viewBox="0 0 22 22"
          aria-hidden="true"
        >
          <circle
            cx="11"
            cy="11"
            r="9"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            class="context-window-indicator__track"
          />
          <circle
            cx="11"
            cy="11"
            r="9"
            fill="none"
            :stroke="ringColor"
            stroke-width="2"
            stroke-linecap="round"
            class="context-window-indicator__progress"
            :style="{
              strokeDasharray: `${2 * Math.PI * 9}`,
              strokeDashoffset: `${dashOffset}`,
            }"
            transform="rotate(-90 11 11)"
          />
        </svg>
        <span class="context-window-indicator__label">{{ percentage }}%</span>
      </div>
    </template>
    <div
      data-testid="context-usage-panel"
      class="context-usage-panel"
      :class="{ 'context-usage-panel--mobile': isMobile }"
      role="dialog"
      aria-label="Context Usage"
    >
      <div class="context-usage-panel__header">
        <span>Context Usage</span>
        <button
          type="button"
          class="context-usage-panel__close"
          aria-label="关闭上下文用量"
          @click="panelOpen = false"
        >
          ×
        </button>
      </div>
      <div class="context-usage-panel__summary">
        <span>{{ percentage }}% Full</span>
        <span>{{ formatTokenCount(context.current_tokens) }} / {{ formatTokenCount(context.max_tokens) }} Tokens</span>
      </div>
      <div class="context-usage-panel__bar" role="progressbar" :aria-valuenow="percentage" aria-valuemin="0" aria-valuemax="100">
        <span class="context-usage-panel__bar-fill" :style="{ width: `${percentage}%`, backgroundColor: ringColor }"></span>
      </div>
    </div>
  </n-popover>
</template>

<style scoped lang="scss">
.context-window-indicator {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--noesis-color-text-muted);
  font-size: 12px;
  line-height: 1;
  cursor: default;
  user-select: none;
}

.context-usage-panel {
  width: min(360px, calc(100vw - 32px));
  box-sizing: border-box;
  padding: 14px;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-lg);
  background: var(--noesis-color-bg-elevated);
  box-shadow: var(--noesis-shadow-float);
  color: var(--noesis-color-text-body);
}

/* 移动端触发器距右缘仅 ~126px：follower 居中放不下会退回 start/end 对齐导致
   左缘溢出屏幕，收窄到 280px 让 top 居中对齐成立（阈值 ≈ 触发器右缘距离×2 + 52） */
.context-usage-panel--mobile {
  width: min(280px, calc(100vw - 32px));
}

.context-usage-panel__header,
.context-usage-panel__summary {
  display: flex;
  align-items: center;
}

.context-usage-panel__header {
  justify-content: space-between;
  font-size: 13px;
}

.context-usage-panel__close {
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--noesis-color-text-muted);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

.context-usage-panel__summary {
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.context-usage-panel__bar {
  position: relative;
  height: 5px;
  margin: 8px 0 12px;
  overflow: hidden;
  border-radius: var(--noesis-radius-pill);
  background: var(--noesis-color-bg-muted);
}

.context-usage-panel__bar-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
}

.context-window-indicator__track {
  opacity: 0.25;
}

.context-window-indicator__progress {
  transition: stroke-dashoffset 0.25s ease, stroke 0.25s ease;
}

.context-window-indicator__label {
  min-width: 2.2em;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
