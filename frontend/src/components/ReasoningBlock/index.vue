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
            <n-icon :size="14" :color="appearance === 'light' ? '#3d5a80' : '#8bd9f0'">
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
/* 布局与 ToolCallCollapse（tool-call / tool-call--light）一致；浅色主题复用工具块同款色板，仅图标区分语义 */
.reasoning-call {
  --reasoning-accent: #5ec8eb;
  background: linear-gradient(165deg, #252830 0%, #1a1d24 100%);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 8px;
  margin: 3px 0;
  box-shadow:
    0 1px 0 rgb(255 255 255 / 6%) inset,
    0 4px 12px rgb(0 0 0 / 20%);
  border-left: 2px solid var(--reasoning-accent);
}

.reasoning-call--light {
  --reasoning-accent: #5b8bd9;
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  margin: 5px 0;
  background: linear-gradient(180deg, #fbfcfe 0%, #f4f6fb 100%);
  border: 1px solid #e1e6ef;
  border-radius: 10px;
  border-left: 2px solid var(--reasoning-accent);
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%);
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
  border-top: 1px solid rgb(255 255 255 / 6%);
}

.reasoning-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid #e8ecf2;
}

.reasoning-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  color: #a8dff5;
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
  background: rgb(94 200 235 / 12%);
  flex-shrink: 0;
}

.reasoning-header:hover {
  background: rgb(255 255 255 / 4%);
}

.reasoning-call--light .reasoning-header {
  color: #334e68;
}

.reasoning-call--light .reasoning-header__icon {
  background: rgb(91 139 217 / 12%);
}

.reasoning-call--light .reasoning-header:hover {
  background: rgb(91 139 217 / 8%);
}

.reasoning-name {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 12px;
  color: #e8f4ff;
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reasoning-call--light .reasoning-name {
  color: #1e3a5f;
}

.reasoning-content {
  padding: 0 10px 10px;
}

.reasoning-section__body {
  border-radius: 7px;
  padding: 8px 10px;
  border: 1px solid rgb(255 255 255 / 7%);
  background: rgb(0 0 0 / 22%);
  border-color: rgb(94 200 235 / 15%);
}

.reasoning-content pre {
  margin: 0;
  color: #d0d8e0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.reasoning-call--light .reasoning-section__body {
  background: #fff;
  border: 1px solid #e5e9f0;
  border-color: #d8e2f0;
}

.reasoning-call--light .reasoning-content pre {
  color: #334155;
}
</style>
