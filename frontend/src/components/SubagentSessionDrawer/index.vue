<script setup lang="ts">
import { useLocalStorage, useWindowSize } from '@vueuse/core'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { computed } from 'vue'
import { useResponsiveDrawerWidth } from '@/hooks/useResponsiveDrawerWidth'

// 子会话抽屉共享壳：resizable 宽度（持久化、双入口共享偏好）、遮罩、标题、内容槽。
// 列表↔详情切换、返回导航等导航语义归调用方——壳只管抽屉本身。
const props = withDefaults(defineProps<{
  title?: string
  /** 面板常开场景用透明遮罩，避免主界面被压暗 */
  transparentMask?: boolean
  /** 详情类内容自管内边距时置 true（body padding 0） */
  flushBody?: boolean
}>(), {
  title: '子 Agent 对话',
  transparentMask: false,
  flushBody: false,
})

const show = defineModel<boolean>('show', { default: false })

const MIN_WIDTH = 420
const customWidth = useLocalStorage('noesis:subagent-drawer-width', 0)
const { width: windowWidth } = useWindowSize()
const desktop = computed(() => windowWidth.value > 768)
const { drawerWidth: responsiveWidth } = useResponsiveDrawerWidth({ max: 760, mobileRatio: 0.96 })
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
    :show-mask="transparentMask ? 'transparent' : true"
    @update:width="handleResize"
  >
    <n-drawer-content :title="props.title" closable :body-content-style="flushBody ? 'padding: 0;' : undefined">
      <slot></slot>
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
