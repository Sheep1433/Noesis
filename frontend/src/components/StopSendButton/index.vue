<script setup lang="ts">
import { NFloatButton } from 'naive-ui'

/**
 * 单按钮 stop/send（主/子会话共用）：运行中呈停止态（实心方块 + 主色光环），
 * 否则发送态。停止/发送的行为差异（乐观收尾 vs 等往返）由宿主的 action
 * 处理决定；`stopping` 为停止受理中的防重复禁用。
 */
withDefaults(defineProps<{
  stopMode: boolean
  sendDisabled?: boolean
  stopping?: boolean
  testidPrefix?: string
}>(), { sendDisabled: false, stopping: false, testidPrefix: '' })

const emit = defineEmits<{ (e: 'action', kind: 'stop' | 'send'): void }>()
</script>

<template>
  <div class="stop-send-btn-wrap">
    <n-float-button
      position="relative"
      :width="36"
      :height="36"
      :disabled="(!stopMode && sendDisabled) || stopping"
      :type="stopMode ? 'primary' : 'default'"
      :data-testid="stopMode ? `${testidPrefix}stop-button` : `${testidPrefix}send-button`"
      class="stop-send-btn"
      :class="{ 'stop-send-btn--stop': stopMode }"
      @click.stop="emit('action', stopMode ? 'stop' : 'send')"
    >
      <span v-if="stopMode" class="stop-send-btn__stop-icon" aria-label="停止生成"></span>
      <div v-else class="stop-send-btn__send-icon i-mingcute:send-fill text-20 cursor-pointer transition-colors duration-300 hover:c-primary/80"></div>
    </n-float-button>
  </div>
</template>

<style scoped>
.stop-send-btn-wrap {
  z-index: 1;
  display: flex;
  align-items: center;
}

.stop-send-btn-wrap :deep(.n-float-button) {
  position: relative !important;
  inset: auto !important;
}

.stop-send-btn--stop {
  box-shadow: 0 0 0 2px var(--noesis-color-primary-ring);
}

.stop-send-btn__stop-icon {
  display: block;
  width: 12px;
  height: 12px;
  background-color: var(--noesis-color-bg-elevated);
  border-radius: 2px;
}
</style>
