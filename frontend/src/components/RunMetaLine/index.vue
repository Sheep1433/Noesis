<script setup lang="ts">
/**
 * 回复级元信息行（主/子会话共用）：耗时文本 + compact 折叠轮的展开开关。
 * 耗时文本与折叠判据由宿主计算（主侧四态 / 子侧两态）；折叠状态机
 * （expanded Set + toggle）也在宿主——本组件只承载展示与交互壳。
 */
withDefaults(defineProps<{
  elapsed?: string
  collapsible?: boolean
  expanded?: boolean
  /** 附注（如「· N 个子 Agent」）；主侧专有，子侧不传 */
  suffix?: string
}>(), { elapsed: '', collapsible: false, expanded: false, suffix: '' })

const emit = defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <div class="run-meta-line">
    <button
      v-if="collapsible"
      type="button"
      class="run-meta-line__toggle"
      :aria-expanded="expanded"
      @click="emit('toggle')"
    >
      <span>{{ elapsed }}</span>
      <span
        class="run-meta-line__chevron"
        :class="{ 'run-meta-line__chevron--expanded': expanded }"
        aria-hidden="true"
      >›</span>
    </button>
    <span v-else class="run-meta-line__elapsed">{{ elapsed }}</span>
    <span v-if="suffix" class="run-meta-line__suffix">{{ suffix }}</span>
  </div>
</template>

<style scoped>
.run-meta-line {
  display: flex;
  align-items: center;
  min-height: 24px;
  position: relative;
  z-index: 1;
  margin-bottom: 6px;
  padding: 0 2px;
  font-size: 13px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  letter-spacing: 0.01em;
}

.run-meta-line__toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--noesis-color-text-muted);
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  transition: color 0.15s ease;
}

.run-meta-line__toggle:hover {
  color: var(--noesis-color-text);
}

.run-meta-line__chevron {
  display: inline-block;
  font-size: 16px;
  line-height: 12px;
  transform: translateY(-1px);
  transition: transform 0.15s ease;
}

.run-meta-line__chevron--expanded {
  transform: translateY(-1px) rotate(90deg);
}

.run-meta-line__suffix {
  margin-left: 8px;
  color: var(--noesis-color-text-secondary);
  font-variant-numeric: tabular-nums;
}
</style>
