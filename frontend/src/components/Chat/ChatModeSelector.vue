<script lang="ts" setup>
import type { ChatModeQaType } from '@/utils/qaType'
import { CHAT_MODE_OPTIONS, chatModeOption } from '@/utils/qaType'

const props = withDefaults(defineProps<{
  qaType?: string
  disabled?: boolean
  placement?: 'bottom' | 'bottom-start' | 'bottom-end' | 'top' | 'top-start' | 'top-end'
}>(), {
  qaType: 'COMMON_QA',
  disabled: false,
  placement: 'bottom',
})

const emit = defineEmits<{
  select: [qaType: ChatModeQaType]
}>()

const open = ref(false)
const currentMode = computed(() => chatModeOption(props.qaType))

function selectMode(qaType: ChatModeQaType) {
  open.value = false
  emit('select', qaType)
}
</script>

<template>
  <n-popover
    v-model:show="open"
    trigger="click"
    :placement="placement"
    :show-arrow="false"
    :disabled="disabled"
    raw
  >
    <template #trigger>
      <slot name="trigger" :mode="currentMode">
        <button
          type="button"
          class="chat-mode-trigger"
          :disabled="disabled"
          :aria-label="`当前模式：${currentMode.label}`"
        >
          <span class="chat-mode-trigger__icon" :class="currentMode.iconClass" aria-hidden="true"></span>
          <span>{{ currentMode.label }}</span>
          <span class="chat-mode-trigger__chevron i-carbon:chevron-down" aria-hidden="true"></span>
        </button>
      </slot>
    </template>

    <div class="chat-mode-panel" role="menu" aria-label="选择对话模式">
      <button
        v-for="option in CHAT_MODE_OPTIONS"
        :key="option.qaType"
        type="button"
        class="chat-mode-option"
        :class="{ 'chat-mode-option--active': option.qaType === currentMode.qaType }"
        role="menuitem"
        @click="selectMode(option.qaType)"
      >
        <span class="chat-mode-option__icon" :class="option.iconClass" aria-hidden="true"></span>
        <span class="chat-mode-option__content">
          <strong>{{ option.label }}</strong>
          <small>{{ option.description }}</small>
        </span>
        <span
          v-if="option.qaType === currentMode.qaType"
          class="chat-mode-option__check i-carbon:checkmark"
          aria-hidden="true"
        ></span>
      </button>
    </div>
  </n-popover>
</template>

<style scoped>
.chat-mode-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 88px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-pill);
  background: var(--noesis-color-bg-elevated);
  color: var(--noesis-color-text);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.chat-mode-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.chat-mode-trigger:hover:not(:disabled) {
  border-color: var(--noesis-color-primary-muted);
  background: var(--noesis-color-primary-bg-subtle);
}

.chat-mode-trigger__icon,
.chat-mode-trigger__chevron {
  display: inline-block;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
}

.chat-mode-trigger__chevron {
  width: 14px;
  height: 14px;
  color: var(--noesis-color-text-secondary);
}

.chat-mode-panel {
  width: min(300px, calc(100vw - 32px));
  padding: 6px;
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-lg);
  background: var(--noesis-color-bg-elevated);
  box-shadow: var(--noesis-shadow-lg);
}

.chat-mode-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 58px;
  padding: 9px 10px;
  border: 0;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text);
  text-align: left;
  cursor: pointer;
}

.chat-mode-option:hover,
.chat-mode-option--active {
  background: var(--noesis-color-primary-bg-subtle);
}

.chat-mode-option__icon,
.chat-mode-option__check {
  display: inline-block;
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  color: var(--noesis-color-primary);
}

.chat-mode-option__content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.chat-mode-option__content strong {
  font-size: 14px;
  line-height: 1.4;
}

.chat-mode-option__content small {
  margin-top: 2px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}
</style>
