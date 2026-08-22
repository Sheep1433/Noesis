<script setup lang="ts">
import type { MessageContent } from '@/api/chat'
import type { RetrievalResultUi } from '@/views/chat/messageParts'
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
