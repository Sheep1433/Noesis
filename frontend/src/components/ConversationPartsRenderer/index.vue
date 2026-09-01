<script setup lang="ts">
import type { MessageContent } from '@/api/chat'
import type { RetrievalResultUi, RetrievalUiPart } from '@/views/chat/messageParts'
import { computed } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview/index.vue'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { normalizeApiContent } from '@/views/chat/messageParts'

const props = withDefaults(defineProps<{
  content: MessageContent
  appearance?: 'light' | 'default'
  collapseSignal?: number
  showActionBar?: boolean
  retrievalResults?: RetrievalResultUi[]
  msgMetadata?: Record<string, unknown> | null
  qaType?: string
}>(), {
  appearance: 'light',
  collapseSignal: 0,
  showActionBar: false,
  retrievalResults: () => [],
  msgMetadata: null,
  qaType: 'COMMON_QA',
})

const parts = computed(() => normalizeApiContent(props.content).parts)

/**
 * 检索 tool 卡的结构化结果来源：同消息 retrieval parts 按 tool_call_id 关联。
 *  主/子会话同构——tool part 只存摘要，完整结果渲染自 retrieval part。
 */
const retrievalByToolCallId = computed(() => {
  const map = new Map<string, RetrievalUiPart>()
  for (const part of parts.value) {
    if (part.type === 'retrieval' && part.tool_call_id) {
      map.set(part.tool_call_id, part)
    }
  }
  return map
})
</script>

<template>
  <template v-for="part in parts" :key="part.id">
    <ReasoningBlock
      v-if="part.type === 'reasoning'"
      :reasoning="part.content"
      :streaming="part.status === 'streaming'"
      :appearance="appearance"
      :collapse-signal="collapseSignal"
    />
    <ToolCallCollapse
      v-else-if="part.type === 'tool'"
      :appearance="appearance"
      :name="part.name"
      :arguments="part.input"
      :result="part.output"
      :status="part.status"
      :state="part.state"
      :error="part.error"
      :duration-ms="part.duration_ms"
      :collapse-signal="collapseSignal"
      :retrieval-part="part.tool_call_id ? retrievalByToolCallId.get(part.tool_call_id) : undefined"
    />
    <MarkdownPreview
      v-else-if="part.type === 'text'"
      :content="part.content"
      :retrieval-results="retrievalResults"
      :msg-metadata="msgMetadata"
      :qa-type="qaType"
      :show-action-bar="showActionBar"
      variant="segment"
    />
  </template>
</template>
