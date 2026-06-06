<script setup lang="ts">
import { BulbOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'

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
  <n-collapse class="reasoning-call" :class="{ 'reasoning-call--light': appearance === 'light', 'reasoning-call--dark': appearance === 'dark' }">
    <n-collapse-item name="reasoning" :default-expanded="defaultOpen">
      <template #header>
        <div class="reasoning-header">
          <div class="reasoning-header__icon">
            <n-icon :size="17" :color="appearance === 'light' ? '#3d5a80' : '#8bd9f0'">
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
        <div class="reasoning-section reasoning-section--body">
          <div class="reasoning-section__label">内容</div>
          <div class="reasoning-section__body">
            <pre>{{ reasoning }}</pre>
          </div>
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
  border-radius: 12px;
  margin: 10px 0;
  box-shadow:
    0 1px 0 rgb(255 255 255 / 6%) inset,
    0 8px 24px rgb(0 0 0 / 28%);
  border-left: 3px solid var(--reasoning-accent);
}

.reasoning-call--light {
  --reasoning-accent: #5b8bd9;
  box-sizing: border-box;
  width: 90%;
  max-width: 100%;
  margin: 8px auto;
  background: linear-gradient(180deg, #fbfcfe 0%, #f4f6fb 100%);
  border: 1px solid #e1e6ef;
  border-radius: 12px;
  border-left: 3px solid var(--reasoning-accent);
  box-shadow: 0 1px 2px rgb(15 23 42 / 5%);
}

.reasoning-call :deep(.n-collapse-item) {
  margin: 0;
}

.reasoning-call :deep(.n-collapse-item__header) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: 0;
  padding: 0 10px 0 0;
}

.reasoning-call :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.reasoning-call :deep(.n-collapse-item__header-extra) {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.reasoning-call :deep(.n-collapse-item-arrow) {
  flex-shrink: 0;
}

.reasoning-call :deep(.n-collapse-item__content-inner) {
  padding-top: 0;
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
  gap: 10px;
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  color: #a8dff5;
  font-size: 13px;
  padding: 11px 14px 11px 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.reasoning-header__middle {
  display: flex;
  align-items: center;
  gap: 10px;
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
  width: 32px;
  height: 32px;
  border-radius: 10px;
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
  font-size: 12.5px;
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
  padding: 0 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.reasoning-section__label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: rgb(168 223 245 / 55%);
  margin-bottom: 6px;
}

.reasoning-call--light .reasoning-section__label {
  color: #64748b;
}

.reasoning-section__body {
  border-radius: 8px;
  padding: 10px 12px;
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
  line-height: 1.55;
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
