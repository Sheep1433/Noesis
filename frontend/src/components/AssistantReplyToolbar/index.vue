<script lang="ts" setup>
import { computed } from 'vue'
import { copyToClipboard } from '@/utils/copy'
import { formatTokenCount } from '@/views/chat/messageParts'

interface AttributionSummary {
  cumulative?: Record<string, number>
  by_caller?: Record<string, Record<string, number>>
  by_model?: Record<string, Record<string, number>>
}

const props = withDefaults(defineProps<{
  qaType?: string
  copyText?: string
  /** 本轮 token 用量摘要（如 "本轮用量 ↑24.2K ↓593 · 共 24.8K"），非空时显示在左侧 */
  usageText?: string
  /** 回复完成时间，显示在复制按钮左侧 */
  timeText?: string
  /** 按 caller/model 归因摘要（按需调试视图，非默认摘要） */
  attribution?: AttributionSummary | null
  /** 与 SSE message-start.langfuse_session_id 一致 */
  langfuse_session_id?: string
  /** VITE_LANGFUSE_UI_ORIGIN，非空时显示「观测」 */
  langfuseUiOrigin?: string
}>(), {
  qaType: 'COMMON_QA',
  copyText: '',
  usageText: '',
  timeText: '',
  attribution: null,
  langfuse_session_id: '',
  langfuseUiOrigin: '',
})


/** 归因 tooltip 文本：展示 caller/model 汇总（按需，不展示无界 steps） */
const attributionTooltip = computed(() => {
  const attr = props.attribution
  if (!attr?.by_caller && !attr?.by_model) {
    return ''
  }
  const lines: string[] = []
  if (attr.by_caller) {
    lines.push('按调用方：')
    for (const [caller, usage] of Object.entries(attr.by_caller)) {
      const inp = usage.input_tokens ?? 0
      const out = usage.output_tokens ?? 0
      lines.push(`  ${caller}: ↑${formatTokenCount(inp)} ↓${formatTokenCount(out)}`)
    }
  }
  if (attr.by_model) {
    lines.push('按模型：')
    for (const [model, usage] of Object.entries(attr.by_model)) {
      const inp = usage.input_tokens ?? 0
      const out = usage.output_tokens ?? 0
      lines.push(`  ${model}: ↑${formatTokenCount(inp)} ↓${formatTokenCount(out)}`)
    }
  }
  return lines.join('\n')
})

const showAttribution = computed(() => Boolean(attributionTooltip.value))

const showLangfuse = computed(
  () => Boolean(props.langfuse_session_id?.trim() && props.langfuseUiOrigin?.trim()),
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
  <div class="assistant-reply-toolbar">
    <div class="assistant-reply-toolbar__left">
      <n-tooltip v-if="usageText && showAttribution" placement="top">
        <template #trigger>
          <span
            v-if="usageText"
            class="assistant-reply-toolbar__usage"
          >{{ usageText }}</span>
        </template>
        <div style="max-width: 320px; font-size: 12px; line-height: 1.6; white-space: pre-line">
          {{ attributionTooltip }}
        </div>
      </n-tooltip>
      <span
        v-else-if="usageText"
        class="assistant-reply-toolbar__usage"
      >{{ usageText }}</span>
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
          Langfuse 会话 ID：<span style="word-break: break-all">{{ langfuse_session_id }}</span>
          。点击打开控制台后在 Session / Traces 中检索。
        </div>
      </n-tooltip>
    </div>
    <div class="assistant-reply-toolbar__actions">
      <span v-if="timeText" class="assistant-reply-toolbar__time">{{ timeText }}</span>
      <n-button ghost size="tiny" icon-placement="left" type="default" :bordered="false" class="assistant-reply-toolbar__btn" @click="handlePassClip()">
        <template #icon>
          <n-icon size="20" class="assistant-reply-toolbar__icon">
            <svg t="1734515176870" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="26346" width="200" height="200">
              <path d="M955.85804 265.068028l0 595.439364L195.018625 860.507392 195.018625 728.187761 62.698994 728.187761 62.698994 132.748397l760.839415 0 0 132.319631L955.85804 265.068028zM195.018625 695.108365 195.018625 265.068028l595.439364 0 0-99.240235L95.779414 165.827793l0 529.279548L195.018625 695.107341zM922.778644 298.148447 228.099045 298.148447 228.099045 827.427996l694.679599 0L922.778644 298.148447z" fill="currentColor" p-id="26347" />
            </svg>
          </n-icon>
        </template>
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.assistant-reply-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  margin-top: 0;
  padding: 18px 15px;
  border-top: 1px solid var(--noesis-color-border-subtle);
  border-bottom-right-radius: 15px;
  border-bottom-left-radius: 15px;
  background-color: transparent;
  color: var(--noesis-color-text-secondary);
}

.assistant-reply-toolbar__left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.assistant-reply-toolbar__usage {
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.assistant-reply-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.assistant-reply-toolbar__btn {
  margin-right: 0;
}

.assistant-reply-toolbar__time {
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  white-space: nowrap;
}

.assistant-reply-toolbar__icon {
  color: var(--noesis-color-text-secondary);
}
</style>
