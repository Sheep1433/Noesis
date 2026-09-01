<script setup lang="ts">
import type { MessageContent, TaskCatalogEntry } from '@/api/chat'
import type { RetrievalResultUi, RetrievalUiPart, ToolUiPart } from '@/views/chat/messageParts'
import { NCollapse, NCollapseItem } from 'naive-ui'
import { computed } from 'vue'
import BackgroundSubagentCollapse from '@/components/BackgroundSubagentCollapse/index.vue'
import MarkdownPreview from '@/components/MarkdownPreview/index.vue'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import SubagentCollapse from '@/components/SubagentCollapse/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { buildDisplayParts, collapseDisplayEntries } from '@/utils/groupAssistantParts'
import { COMPACTION_BOUNDARY, normalizeApiContent } from '@/views/chat/messageParts'

/**
 * 会话 parts 的唯一渲染入口（主/子会话共用）：分组（委派卡 / 并行工具组）、
 * 工具过滤、reasoning 合并全部经 buildDisplayParts；compact 工具模式的
 * 折叠视图经 collapseDisplayEntries。宿主只传状态与数据源，不自带渲染分支。
 */
const props = withDefaults(defineProps<{
  content: MessageContent
  appearance?: 'light' | 'default'
  collapseSignal?: number
  showActionBar?: boolean
  retrievalResults?: RetrievalResultUi[]
  msgMetadata?: Record<string, unknown> | null
  qaType?: string
  /** start_task 委派卡的后台任务目录查询（主会话宿主提供） */
  taskForToolPart?: (part: ToolUiPart) => TaskCatalogEntry | undefined
  /** compact 工具模式（并行组紧凑样式） */
  compactTools?: boolean
  /** 消息仍在生成（并行组默认展开看进度；完成后收起） */
  liveStreaming?: boolean
  /** compact 工具模式折叠视图（委派卡 + 终稿；宿主持展开状态） */
  collapsed?: boolean
  isInit?: boolean
  isView?: boolean
}>(), {
  appearance: 'light',
  collapseSignal: 0,
  showActionBar: false,
  retrievalResults: () => [],
  msgMetadata: null,
  qaType: 'COMMON_QA',
  taskForToolPart: undefined,
  compactTools: false,
  liveStreaming: false,
  collapsed: false,
  isInit: false,
  isView: false,
})

const emit = defineEmits<{ (e: 'readerFailed', error: unknown): void }>()

const entries = computed(() => {
  const built = buildDisplayParts(normalizeApiContent(props.content).parts)
  return props.collapsed ? collapseDisplayEntries(built) : built
})

/** 并行组 default-expanded 需随完成态翻转：key 变化强制重建（n-collapse 首渲染语义） */
const parallelGroupKey = computed(() =>
  `ptg-${props.liveStreaming ? 'live' : 'done'}-${props.collapseSignal}`)

/**
 * 检索 tool 卡的结构化结果来源：同消息 retrieval parts 按 tool_call_id 关联。
 *  主/子会话同构——tool part 只存摘要，完整结果渲染自 retrieval part。
 */
const retrievalByToolCallId = computed(() => {
  const map = new Map<string, RetrievalUiPart>()
  for (const part of normalizeApiContent(props.content).parts) {
    if (part.type === 'retrieval' && part.tool_call_id) {
      map.set(part.tool_call_id, part)
    }
  }
  return map
})

function entryKey(entry: (typeof entries.value)[number], fallback: number): string {
  if (entry.kind === 'parallel_tools') {
    return `pg:${entry.parts[0]?.tool_call_id ?? entry.parts[0]?.id ?? fallback}`
  }
  return entry.part.tool_call_id ?? entry.part.id ?? String(fallback)
}
</script>

<template>
  <template v-for="(entry, index) in entries" :key="entryKey(entry, index)">
    <ReasoningBlock
      v-if="entry.kind === 'part' && entry.part.type === 'reasoning' && (entry.part.content || entry.part.status === 'streaming')"
      :reasoning="entry.part.content"
      :default-open="false"
      :streaming="entry.part.status === 'streaming'"
      :appearance="appearance"
      :collapse-signal="collapseSignal"
    />
    <SubagentCollapse
      v-else-if="entry.kind === 'subagent'"
      :appearance="appearance"
      :input="entry.part.input"
      :output="entry.part.output"
      :status="entry.part.status"
      :state="entry.part.state"
      :error="entry.part.error"
      :duration-ms="entry.part.duration_ms"
      :child-parts="entry.childParts"
    />
    <div
      v-else-if="entry.kind === 'parallel_tools'"
      class="parallel-tools-group"
      :class="[{ 'parallel-tools-group--compact': compactTools }, appearance === 'light' ? 'parallel-tools-group--light' : '']"
    >
      <n-collapse>
        <n-collapse-item
          :key="`${parallelGroupKey}-${index}`"
          name="parallel-tools"
          :default-expanded="liveStreaming"
        >
          <template #header>
            <div class="parallel-tools-group__header">
              并行工具 · {{ entry.parts.length }} 个
            </div>
          </template>
          <div class="parallel-tools-group__body">
            <ToolCallCollapse
              v-for="toolPart in entry.parts"
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
              :retrieval-part="toolPart.tool_call_id ? retrievalByToolCallId.get(toolPart.tool_call_id) : undefined"
            />
          </div>
        </n-collapse-item>
      </n-collapse>
    </div>
    <template v-else-if="entry.kind === 'part' && entry.part.type === 'tool'">
      <BackgroundSubagentCollapse
        v-if="entry.part.name === 'start_task'"
        :tool-part="entry.part"
        :task="taskForToolPart ? taskForToolPart(entry.part) : undefined"
      />
      <ToolCallCollapse
        v-else
        :appearance="appearance"
        :name="entry.part.name"
        :arguments="entry.part.input"
        :result="entry.part.output"
        :error="entry.part.error"
        :status="entry.part.status"
        :state="entry.part.state"
        :error-category="entry.part.errorCategory"
        :exit-code="entry.part.exitCode"
        :truncated="entry.part.truncated"
        :duration-ms="entry.part.duration_ms"
        :collapse-signal="collapseSignal"
        :retrieval-part="entry.part.tool_call_id ? retrievalByToolCallId.get(entry.part.tool_call_id) : undefined"
      />
    </template>
    <div
      v-else-if="entry.kind === 'part' && entry.part.type === 'text' && entry.part.content === COMPACTION_BOUNDARY"
      class="compact-boundary"
      role="separator"
    >
      <span class="compact-boundary__text">以上对话已压缩摘要</span>
    </div>
    <MarkdownPreview
      v-else-if="entry.kind === 'part' && entry.part.type === 'text'"
      :content="entry.part.content || ''"
      :retrieval-results="retrievalResults"
      :msg-metadata="msgMetadata"
      :is-init="isInit"
      :is-view="isView"
      :show-action-bar="showActionBar"
      variant="segment"
      :qa-type="qaType"
      @failed="(error: unknown) => emit('readerFailed', error)"
    />
  </template>
</template>
