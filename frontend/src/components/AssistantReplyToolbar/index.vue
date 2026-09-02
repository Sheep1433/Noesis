<script lang="ts" setup>
import { computed } from 'vue'
import { copyToClipboard } from '@/utils/copy'

const props = withDefaults(defineProps<{
  qaType?: string
  copyText?: string
  /** 回复完成时间，显示在复制按钮左侧 */
  timeText?: string
  /** 与 SSE message-start.langfuse_session_id 一致 */
  langfuseSessionId?: string
  /** VITE_LANGFUSE_UI_ORIGIN，非空时显示「观测」 */
  langfuseUiOrigin?: string
  /** 顶部边框：主视图卡片内 true（内容/页脚分隔），平铺宿主 false */
  bordered?: boolean
}>(), {
  qaType: 'COMMON_QA',
  copyText: '',
  timeText: '',
  langfuseSessionId: '',
  langfuseUiOrigin: '',
  bordered: true,
})

const showLangfuse = computed(
  () => Boolean(props.langfuseSessionId?.trim() && props.langfuseUiOrigin?.trim()),
)

function openLangfuseUi() {
  const origin = String(props.langfuseUiOrigin || '').replace(/\/$/, '')
  if (!origin) {
    return
  }
  window.open(origin, '_blank', 'noopener,noreferrer')
}

const handlePassClip = async () => {
  const text = props.copyText || ''
  if (!text.trim()) {
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.warning('暂无可复制内容')
    return
  }
  try {
    await copyToClipboard(text)
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.success('已复制')
  } catch {
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.error('复制失败')
  }
}
</script>

<template>
  <div class="assistant-reply-toolbar" :class="{ 'assistant-reply-toolbar--borderless': !bordered }">
    <div class="assistant-reply-toolbar__left">
      <slot name="meta"></slot>
      <n-tooltip v-if="showLangfuse" placement="top">
        <template #trigger>
          <n-button
            type="default"
            ghost
            size="tiny"
            :bordered="false"
            @click="openLangfuseUi"
          >
            观测
          </n-button>
        </template>
        <div style="max-width: 280px; font-size: 12px; line-height: 1.5">
          Langfuse 会话 ID：<span style="word-break: break-all">{{ langfuseSessionId }}</span>
          。点击打开控制台后在 Session / Traces 中检索。
        </div>
      </n-tooltip>
    </div>
    <div class="assistant-reply-toolbar__actions">
      <span v-if="timeText" class="assistant-reply-toolbar__time">{{ timeText }}</span>
      <button
        type="button"
        class="assistant-reply-toolbar__btn"
        aria-label="复制回复"
        @click="handlePassClip()"
      >
        <span class="i-hugeicons:copy-01" aria-hidden="true"></span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.assistant-reply-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-sizing: border-box;
  width: 100%;
  margin-top: 0;
  padding: 6px 8px 6px 15px;
  border-top: 1px solid var(--noesis-color-border-subtle);
  border-bottom-right-radius: 15px;
  border-bottom-left-radius: 15px;
  background-color: transparent;
  color: var(--noesis-color-text-secondary);
}

.assistant-reply-toolbar--borderless {
  border-top: none;
  padding-top: 2px;
}

.assistant-reply-toolbar__left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.assistant-reply-toolbar__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
}

.assistant-reply-toolbar__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: 0;
  margin-right: 0;
  padding: 0;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text-hint);
  cursor: pointer;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.assistant-reply-toolbar__btn span {
  display: inline-block;
  width: 16px;
  height: 16px;
  font-size: 16px;
  line-height: 1;
}

.assistant-reply-toolbar__btn:hover {
  color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-subtle);
}

.assistant-reply-toolbar__time {
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  white-space: nowrap;
}
</style>
