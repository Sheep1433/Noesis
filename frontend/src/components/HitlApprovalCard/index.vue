<script setup lang="ts">
import { NButton, NSpace, NText } from 'naive-ui'
import { computed } from 'vue'

const props = defineProps<{
  command?: string
  path?: string
  toolName: string
  allowSessionGrant?: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'decide', payload: { type: 'approve' | 'reject', grant_scope?: 'once' | 'session' }): void
}>()

const preview = computed(() => {
  if (props.command) {
    return props.command
  }
  if (props.path) {
    return props.path
  }
  return props.toolName
})

const title = computed(() => {
  if (props.toolName === 'execute') {
    return '确认执行命令'
  }
  return '确认写入记忆'
})
</script>

<template>
  <div class="hitl-approval-card">
    <div class="hitl-approval-card__title">
      {{ title }}
    </div>
    <NText depth="3" class="hitl-approval-card__hint">
      等待确认
    </NText>
    <pre class="hitl-approval-card__preview">{{ preview }}</pre>
    <NSpace>
      <NButton
        size="small"
        type="primary"
        :disabled="disabled"
        @click="emit('decide', { type: 'approve', grant_scope: 'once' })"
      >
        允许一次
      </NButton>
      <NButton
        v-if="allowSessionGrant"
        size="small"
        :disabled="disabled"
        @click="emit('decide', { type: 'approve', grant_scope: 'session' })"
      >
        本会话允许
      </NButton>
      <NButton
        size="small"
        type="error"
        ghost
        :disabled="disabled"
        @click="emit('decide', { type: 'reject' })"
      >
        拒绝
      </NButton>
    </NSpace>
  </div>
</template>

<style scoped lang="scss">
.hitl-approval-card {
  margin: 8px 0 12px;
  padding: 12px 14px;
  border: 1px solid var(--noesis-border-subtle, #e5e5e5);
  border-radius: 8px;
  background: var(--noesis-surface-muted, #f7f7f5);
}

.hitl-approval-card__title {
  font-weight: 600;
  margin-bottom: 4px;
}

.hitl-approval-card__hint {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
}

.hitl-approval-card__preview {
  margin: 0 0 12px;
  padding: 8px 10px;
  max-height: 160px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 6px;
  background: var(--noesis-surface, #fff);
  border: 1px solid var(--noesis-border-subtle, #ebebeb);
}
</style>
