<script setup lang="ts">
import { NButton, NInput, NRadio, NRadioGroup, NSpace, NText } from 'naive-ui'
import { ref } from 'vue'

const props = defineProps<{
  question: string
  options?: string[] | null
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'respond', message: string): void
}>()

const text = ref('')
const selected = ref<string | null>(null)

function submit() {
  if (props.options?.length) {
    if (!selected.value) {
      return
    }
    emit('respond', selected.value)
    return
  }
  const msg = text.value.trim()
  if (!msg) {
    return
  }
  emit('respond', msg)
}
</script>

<template>
  <div class="hitl-clarification-card">
    <div class="hitl-clarification-card__title">
      需要你补充信息
    </div>
    <NText class="hitl-clarification-card__question">
      {{ question }}
    </NText>
    <div v-if="options?.length" class="hitl-clarification-card__options">
      <NRadioGroup v-model:value="selected" :disabled="disabled">
        <NSpace vertical>
          <NRadio
            v-for="opt in options"
            :key="opt"
            :value="opt"
            :label="opt"
          />
        </NSpace>
      </NRadioGroup>
    </div>
    <NInput
      v-else
      v-model:value="text"
      type="textarea"
      :rows="3"
      placeholder="输入你的回答"
      :disabled="disabled"
    />
    <div class="hitl-clarification-card__actions">
      <NButton
        size="small"
        type="primary"
        :disabled="disabled || (options?.length ? !selected : !text.trim())"
        @click="submit"
      >
        提交回答
      </NButton>
    </div>
  </div>
</template>

<style scoped lang="scss">
.hitl-clarification-card {
  margin: 8px 0 12px;
  padding: 12px 14px;
  border: 1px solid var(--noesis-border-subtle, #e5e5e5);
  border-radius: 8px;
  background: var(--noesis-surface-muted, #f7f7f5);
}

.hitl-clarification-card__title {
  font-weight: 600;
  margin-bottom: 6px;
}

.hitl-clarification-card__question {
  display: block;
  margin-bottom: 10px;
  white-space: pre-wrap;
}

.hitl-clarification-card__options {
  margin-bottom: 10px;
}

.hitl-clarification-card__actions {
  margin-top: 10px;
}
</style>
