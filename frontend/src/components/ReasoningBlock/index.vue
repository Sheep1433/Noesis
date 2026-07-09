<script setup lang="ts">
import { BulbOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { collapseCompactStyle } from '@/utils/collapseCompact'

interface Props {
  reasoning?: string
  defaultOpen?: boolean
  /** 与 ToolCallCollapse 的 appearance 一致：嵌入助手气泡用 light */
  appearance?: 'dark' | 'light'
  /** 流式生成中：标题为「思考中…」，状态标签为「运行中」 */
  streaming?: boolean
}

withDefaults(defineProps<Props>(), {
  reasoning: '',
  defaultOpen: false,
  appearance: 'light',
  streaming: false,
})
</script>

<template>
  <n-collapse
    class="reasoning-call"
    :class="{ 'reasoning-call--light': appearance === 'light', 'reasoning-call--dark': appearance === 'dark' }"
    :style="collapseCompactStyle"
  >
    <n-collapse-item name="reasoning" :default-expanded="defaultOpen">
      <template #header>
        <div class="reasoning-header">
          <div class="reasoning-header__icon">
            <n-icon :size="14">
              <BulbOutline />
            </n-icon>
          </div>
          <div class="reasoning-header__middle">
            <span class="reasoning-name">{{ streaming ? '思考中…' : '思考过程' }}</span>
            <div class="reasoning-header__tags">
              <n-tag v-if="streaming" type="warning" size="small" round bordered>运行中</n-tag>
              <n-tag v-else type="success" size="small" round bordered>完成</n-tag>
            </div>
          </div>
        </div>
      </template>

      <div class="reasoning-content">
        <div class="reasoning-section__body">
          <pre>{{ reasoning }}</pre>
        </div>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
.reasoning-call {
  --reasoning-accent: var(--noesis-block-dark-accent);
  background: var(--noesis-block-dark-bg);
  border: 1px solid var(--noesis-block-dark-border);
  border-radius: var(--noesis-radius-sm);
  margin: 3px 0;
  box-shadow: var(--noesis-shadow-block-dark);
  border-left: 3px solid var(--reasoning-accent);
}

.reasoning-call--light {
  --reasoning-accent: var(--noesis-block-light-accent);
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  margin: 5px 0;
  background: var(--noesis-block-light-bg);
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-md);
  border-left: 3px solid var(--reasoning-accent);
  box-shadow: var(--noesis-shadow-sm);
}

.reasoning-call :deep(.n-collapse-item) {
  margin: 0 !important;
}

.reasoning-call :deep(.n-collapse-item__header) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 4px;
  min-width: 0;
  min-height: 0;
  padding: 0 6px 0 0 !important;
}

.reasoning-call :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.reasoning-call :deep(.n-collapse-item-arrow) {
  flex-shrink: 0;
  font-size: 14px !important;
  margin-right: 4px !important;
}

.reasoning-call :deep(.n-collapse-item__content-inner) {
  padding-top: 0 !important;
}

.reasoning-call :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-dark-border-inner);
}

.reasoning-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.reasoning-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  color: var(--noesis-block-dark-text);
  font-size: 12px;
  padding: 7px 10px 7px 8px;
  cursor: pointer;
  transition: background 0.15s ease;
  line-height: 1.3;
}

.reasoning-header__middle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.reasoning-header__tags {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.reasoning-header__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--noesis-block-dark-bg-icon);
  color: var(--noesis-block-dark-icon);
  flex-shrink: 0;
}

.reasoning-header:hover {
  background: var(--noesis-block-dark-bg-hover);
}

.reasoning-call--light .reasoning-header {
  color: var(--noesis-block-light-text);
}

.reasoning-call--light .reasoning-header__icon {
  background: var(--noesis-color-primary-bg-icon);
  color: var(--noesis-block-light-icon);
}

.reasoning-call--light .reasoning-header:hover {
  background: var(--noesis-color-primary-bg-hover);
}

.reasoning-name {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 12px;
  color: var(--noesis-block-dark-text-name);
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reasoning-call--light .reasoning-name {
  color: var(--noesis-block-light-text-name);
}

.reasoning-content {
  padding: 0 10px 10px;
}

.reasoning-section__body {
  border-radius: 7px;
  padding: 8px 10px;
  border: 1px solid var(--noesis-block-dark-border-section);
  background: var(--noesis-block-dark-bg-section);
  border-color: var(--noesis-block-dark-border-args);
}

.reasoning-content pre {
  margin: 0;
  color: var(--noesis-block-dark-text-code);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.reasoning-call--light .reasoning-section__body {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-code);
  border-color: var(--noesis-color-border-args);
}

.reasoning-call--light .reasoning-content pre {
  color: var(--noesis-block-light-text-code);
}
</style>
