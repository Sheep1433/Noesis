<script setup lang="ts">
import { useLocalStorage, useWindowSize } from '@vueuse/core'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { computed } from 'vue'
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
const { drawerWidth: responsiveWidth } = useResponsiveDrawerWidth({ max: 760, mobileRatio: 0.96 })

// 左边缘可拖拽调宽：用户拖过的宽度持久化；窄视口回到响应式宽度且不可拖
const MIN_WIDTH = 420
const customWidth = useLocalStorage('noesis:subagent-drawer-width', 0)
const { width: windowWidth } = useWindowSize()
const desktop = computed(() => windowWidth.value > 768)
const drawerWidth = computed(() =>
  (desktop.value && customWidth.value >= MIN_WIDTH ? customWidth.value : responsiveWidth.value),
)

function handleResize(width: number) {
  customWidth.value = Math.round(Math.min(Math.max(width, MIN_WIDTH), windowWidth.value - 48))
}
</script>

<template>
  <n-drawer
    v-model:show="show"
    placement="right"
    :width="drawerWidth"
    :resizable="desktop"
    :min-width="MIN_WIDTH"
    :max-width="windowWidth - 48"
    @update:width="handleResize"
  >
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
