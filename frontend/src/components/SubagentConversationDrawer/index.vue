<script setup lang="ts">
import SubagentConversationView from '@/components/SubagentConversationView/index.vue'
import SubagentSessionDrawer from '@/components/SubagentSessionDrawer/index.vue'

// 单任务对话抽屉 = 共享壳 + 内嵌对话视图。
// 任务列表入口（TaskCatalogPanel）需要列表/详情切换，直接用壳；
// 本组合只补「视图内嵌 + 标题默认」这一层，宽度/遮罩逻辑全部在壳里。
const props = withDefaults(defineProps<{
  sessionId: string
  runId?: string | null
  title?: string
}>(), {
  runId: null,
  title: '子 Agent 对话',
})

const emit = defineEmits<{ (event: 'changed'): void }>()
const show = defineModel<boolean>('show', { default: false })
</script>

<template>
  <SubagentSessionDrawer v-model:show="show" :title="props.title">
    <SubagentConversationView
      :session-id="props.sessionId"
      :run-id="props.runId"
      :active="show"
      @changed="emit('changed')"
    />
  </SubagentSessionDrawer>
</template>
