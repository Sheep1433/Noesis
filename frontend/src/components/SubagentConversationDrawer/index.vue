<script setup lang="ts">
import { NDrawer, NDrawerContent } from 'naive-ui'
import SubagentConversationView from '@/components/SubagentConversationView/index.vue'
import { useResponsiveDrawerWidth } from '@/hooks/useResponsiveDrawerWidth'

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
const { drawerWidth } = useResponsiveDrawerWidth({ max: 760, mobileRatio: 0.96 })
</script>

<template>
  <n-drawer v-model:show="show" placement="right" :width="drawerWidth">
    <n-drawer-content :title="props.title" closable>
      <SubagentConversationView
        :session-id="props.sessionId"
        :run-id="props.runId"
        :active="show"
        @changed="emit('changed')"
      />
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped lang="scss">
:deep(.n-drawer-header__main) {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
</style>
