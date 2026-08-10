<script setup lang="ts">
import { NButton, useDialog } from 'naive-ui'

const props = withDefaults(defineProps<{
  title: string
  description: string
  confirmLabel?: string
  disabled?: boolean
  loading?: boolean
}>(), {
  confirmLabel: '确认',
  disabled: false,
  loading: false,
})

const emit = defineEmits<{
  (e: 'confirm'): void
}>()

const dialog = useDialog()

function requestConfirm() {
  dialog.warning({
    title: props.title,
    content: props.description,
    positiveText: props.confirmLabel,
    negativeText: '取消',
    onPositiveClick: () => emit('confirm'),
  })
}
</script>

<template>
  <n-button
    type="error"
    ghost
    :disabled="disabled"
    :loading="loading"
    @click="requestConfirm"
  >
    <slot>{{ confirmLabel }}</slot>
  </n-button>
</template>
