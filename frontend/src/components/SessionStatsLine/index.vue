<script setup lang="ts">
/**
 * 会话统计条（主/子会话共用）：格式化统计文本的单行展示。
 * 字体排版在此单点；宿主经 class 传布局壳（宽度/内边距/居中方式）。
 */
defineProps<{
  line: string
}>()
</script>

<template>
  <div class="session-stats-line" role="status" aria-live="polite">{{ line }}</div>
</template>

<style scoped>
.session-stats-line {
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  font-variant-numeric: tabular-nums;
  text-align: center;
}
</style>
