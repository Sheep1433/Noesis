<script setup lang="ts">
import type { ContextWindowSnapshot } from '@/views/chat/messageParts'
import { computed } from 'vue'
import { formatContextWindowTooltip } from '@/views/chat/messageParts'

const props = defineProps<{
  context: ContextWindowSnapshot
}>()

const percentage = computed(() => Math.min(100, Math.max(0, Math.round(props.context.used_percentage))))

const ringColor = computed(() => {
  if (percentage.value >= 85) {
    return '#e88080'
  }
  if (percentage.value >= 60) {
    return '#f2c97d'
  }
  return '#8a8f99'
})

const tooltipText = computed(() => `${formatContextWindowTooltip(props.context)}（估算）`)

const dashOffset = computed(() => {
  const circumference = 2 * Math.PI * 9
  return circumference * (1 - percentage.value / 100)
})
</script>

<template>
  <n-tooltip placement="top">
    <template #trigger>
      <div
        class="context-window-indicator"
        role="status"
        :aria-label="`上下文占用约 ${percentage}%`"
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
    {{ tooltipText }}
  </n-tooltip>
</template>

<style scoped lang="scss">
.context-window-indicator {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #8a8f99;
  font-size: 12px;
  line-height: 1;
  cursor: default;
  user-select: none;
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
