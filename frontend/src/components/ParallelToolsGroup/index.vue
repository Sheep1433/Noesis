<script setup lang="ts">
import type { ToolUiPart } from '@/views/chat/messageParts'
import { NCollapse, NCollapseItem } from 'naive-ui'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'

/**
 * 并行工具组（主/子会话共用）：n-collapse 容器 + 「并行工具 · N 个」头 +
 * 工具行列表。样式随组件走（迁自 ConversationPartsRenderer；子 Agent
 * 时间线的内联重写已删除）。
 */
const props = withDefaults(defineProps<{
  parts: ToolUiPart[]
  appearance?: 'light' | 'default'
  /** compact 工具模式（无框轨道，与普通工具行共用 disclosure 样式） */
  compact?: boolean
  /** 生成中默认展开看进度；完成后收起（宿主控制翻转，经 groupKey 重建） */
  defaultExpanded?: boolean
  /** 组内 key 变化强制重建（n-collapse 首渲染语义） */
  groupKey?: string | number
  collapseSignal?: number
  /** 按 tool_call_id 关联的检索 part（来源面板数据源） */
  retrievalByToolCallId?: Map<string, unknown>
}>(), {
  appearance: 'light',
  compact: false,
  defaultExpanded: true,
  groupKey: '',
  collapseSignal: 0,
  retrievalByToolCallId: undefined,
})
</script>

<template>
  <div
    class="parallel-tools-group"
    :class="[{ 'parallel-tools-group--compact': compact }, appearance === 'light' ? 'parallel-tools-group--light' : '']"
  >
    <n-collapse>
      <n-collapse-item
        :key="`${groupKey}-parallel`"
        name="parallel-tools"
        :default-expanded="defaultExpanded"
      >
        <template #header>
          <div class="parallel-tools-group__header">
            并行工具 · {{ parts.length }} 个
          </div>
        </template>
        <div class="parallel-tools-group__body">
          <ToolCallCollapse
            v-for="toolPart in parts"
            :key="toolPart.tool_call_id ?? toolPart.id"
            :appearance="appearance"
            :name="toolPart.name"
            :arguments="toolPart.input"
            :result="toolPart.output"
            :error="toolPart.error"
            :status="toolPart.status"
            :state="toolPart.state"
            :error-category="toolPart.errorCategory"
            :exit-code="toolPart.exitCode"
            :truncated="toolPart.truncated"
            :duration-ms="toolPart.duration_ms"
            :collapse-signal="collapseSignal"
            :retrieval-part="toolPart.tool_call_id && retrievalByToolCallId ? retrievalByToolCallId.get(toolPart.tool_call_id) as never : undefined"
          />
        </div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<style lang="scss" scoped>
.parallel-tools-group--light {
  margin: 5px 0;
  padding: 6px 10px;
  border: 1px solid var(--noesis-block-light-border);
  border-left: 3px solid var(--noesis-block-light-accent);
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-block-light-bg);
}

/* 简洁模式与普通工具行共用同一条无框 disclosure 轨道。 */
.parallel-tools-group--compact {
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.parallel-tools-group--compact :deep(.n-collapse-item__header) {
  min-height: 0;
  padding: 1px 0 !important;
}

.parallel-tools-group--compact :deep(.n-collapse-item__header-main) {
  min-width: 0;
}

.parallel-tools-group--compact :deep(.n-collapse-item__content-wrapper) {
  border-top: none;
}

.parallel-tools-group--compact .parallel-tools-group__header {
  min-height: 24px;
  line-height: 24px;
}

.parallel-tools-group__header {
  display: flex;
  align-items: center;
  min-height: 22px;
  width: 100%;
  font-size: 12px;
  color: var(--noesis-color-text-secondary);
}

.parallel-tools-group :deep(.n-collapse-item__header) {
  padding: 0 !important;
}

.parallel-tools-group :deep(.n-collapse-item__content-inner) {
  padding: 0 !important;
}

.parallel-tools-group :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.parallel-tools-group__body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.parallel-tools-group__body :deep(.tool-call--light) {
  margin: 0;
  box-shadow: none;
}
</style>
