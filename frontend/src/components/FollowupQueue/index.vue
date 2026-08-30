<script setup lang="ts">
import { ref } from 'vue'

/**
 * 待发队列条（主/子 Agent 共用）：运行中发送的消息在此排队，
 * 当前 run 终态后由宿主逐条自动提交。条目支持立即发送、编辑回填、删除与拖拽排序。
 */
defineProps<{
  messages: string[]
}>()

const emit = defineEmits<{
  (e: 'remove', index: number): void
  (e: 'edit', index: number): void
  (e: 'sendNow', index: number): void
  (e: 'reorder', from: number, to: number): void
}>()

const dragIndex = ref<number | null>(null)
</script>

<template>
  <div v-if="messages.length" class="followup-queue" data-testid="followup-queue">
    <div
      v-for="(message, index) in messages"
      :key="`${index}-${message}`"
      class="followup-queue__item"
      :class="{ 'followup-queue__item--dragging': dragIndex === index }"
      draggable="true"
      data-testid="followup-queue-item"
      @dragstart="dragIndex = index"
      @dragover.prevent
      @drop.prevent="emit('reorder', dragIndex ?? index, index); dragIndex = null"
      @dragend="dragIndex = null"
    >
      <span class="followup-queue__drag i-material-symbols:drag-indicator" aria-hidden="true"></span>
      <span class="followup-queue__text" :title="message">{{ message }}</span>
      <button
        type="button"
        class="followup-queue__send"
        title="立即发送：空闲时立即开跑，运行中衔接为当前轮后的下一轮"
        @click="emit('sendNow', index)"
      >
        <span class="i-material-symbols:bolt text-14" aria-hidden="true"></span>
        立即
      </button>
      <button
        type="button"
        class="followup-queue__icon"
        title="编辑后重新排队"
        @click="emit('edit', index)"
      >
        <span class="i-carbon:edit" aria-hidden="true"></span>
      </button>
      <button
        type="button"
        class="followup-queue__icon"
        title="删除"
        @click="emit('remove', index)"
      >
        <span class="i-carbon:trash-can" aria-hidden="true"></span>
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.followup-queue {
  display: flex;
  flex-direction: column;
  margin: -2px -4px 2px;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
}

.followup-queue__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
}

.followup-queue__item--dragging {
  opacity: 0.5;
}

.followup-queue__drag {
  flex: none;
  color: var(--noesis-color-text-hint);
  font-size: 16px;
  cursor: grab;
}

.followup-queue__text {
  flex: 1;
  overflow: hidden;
  min-width: 0;
  color: var(--noesis-color-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.followup-queue__send {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 3px;
  padding: 2px 10px;
  border: 1px solid var(--noesis-color-border);
  border-radius: 999px;
  background: var(--noesis-color-bg-elevated);
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 18px;
  cursor: pointer;
}

.followup-queue__send:hover {
  border-color: var(--noesis-color-primary-border-soft);
  color: var(--noesis-color-primary);
}

.followup-queue__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: var(--noesis-radius-sm);
  background: transparent;
  color: var(--noesis-color-text-hint);
  font-size: 15px;
  cursor: pointer;
}

.followup-queue__icon:hover {
  background: var(--noesis-color-bg-muted);
  color: var(--noesis-color-text-secondary);
}
</style>
