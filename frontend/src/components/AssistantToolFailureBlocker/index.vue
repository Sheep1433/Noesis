<script setup lang="ts">
/**
 * 「本轮未完成」阻断提示（主/子会话共用）：
 * 工具失败且无回答时显示（判定函数 shouldShowAssistantToolFailureBlocker 在宿主侧）。
 */
</script>

<template>
  <div class="assistant-failure-blocker" role="status">
    <span class="assistant-failure-blocker__icon" aria-hidden="true">!</span>
    <span>本轮未完成</span>
  </div>
</template>

<style scoped>
.assistant-failure-blocker {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 7px 2px 3px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.assistant-failure-blocker__icon {
  display: inline-flex;
  flex: 0 0 16px;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--noesis-color-warning);
  font-size: 11px;
  font-weight: 700;
  border: 1px solid currentColor;
  border-radius: 50%;
}
</style>
