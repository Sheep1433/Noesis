<script setup lang="ts">
import { NDrawer, NDrawerContent, NPopover } from 'naive-ui'
import { useBreakpoint } from '@/hooks/useBreakpoint'

withDefaults(defineProps<{
  title: string
  disabled?: boolean
  placement?: 'top-start' | 'top' | 'top-end' | 'bottom-start' | 'bottom' | 'bottom-end'
  raw?: boolean
  popupClass?: string
  height?: string | number
}>(), {
  disabled: false,
  placement: 'top-start',
  raw: false,
  popupClass: undefined,
  height: 'min(78vh, 620px)',
})

const show = defineModel<boolean>('show', { default: false })
const { isMobile } = useBreakpoint()
</script>

<template>
  <n-popover
    v-if="!isMobile"
    v-model:show="show"
    trigger="click"
    :placement="placement"
    :show-arrow="false"
    :disabled="disabled"
    :raw="raw"
    :class="popupClass"
  >
    <template #trigger>
      <slot name="trigger"></slot>
    </template>
    <slot></slot>
  </n-popover>

  <template v-else>
    <slot name="trigger"></slot>
    <n-drawer
      v-model:show="show"
      placement="bottom"
      width="100%"
      :height="height"
      :block-scroll="true"
      :class="popupClass"
    >
      <n-drawer-content
        :title="title"
        closable
        body-content-style="padding: 0 16px max(16px, env(safe-area-inset-bottom)); max-height: 100%; overflow-y: auto;"
      >
        <slot></slot>
      </n-drawer-content>
    </n-drawer>
  </template>
</template>
