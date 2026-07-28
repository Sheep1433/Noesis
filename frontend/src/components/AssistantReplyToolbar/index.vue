<script lang="ts" setup>
import { computed } from 'vue'
import { copyToClipboard } from '@/utils/copy'

const props = withDefaults(defineProps<{
  qaType?: string
  copyText?: string
  /** token 用量摘要（如 "↑24.2K ↓593 · 共 24.8K"），非空时显示在左侧 */
  usageText?: string
  /** 与 SSE message-start.langfuse_session_id 一致 */
  langfuse_session_id?: string
  /** VITE_LANGFUSE_UI_ORIGIN，非空时显示「观测」 */
  langfuseUiOrigin?: string
}>(), {
  qaType: 'COMMON_QA',
  copyText: '',
  usageText: '',
  langfuse_session_id: '',
  langfuseUiOrigin: '',
})

const emit = defineEmits<{
  recycleQa: []
}>()

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
      <span
        v-if="usageText"
        class="assistant-reply-toolbar__usage"
      >{{ usageText }}</span>
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
      <n-button ghost size="tiny" icon-placement="left" type="default" :bordered="false" class="assistant-reply-toolbar__btn" @click="handlePassClip()">
        <template #icon>
          <n-icon size="20" class="assistant-reply-toolbar__icon">
            <svg t="1734515176870" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="26346" width="200" height="200">
              <path d="M955.85804 265.068028l0 595.439364L195.018625 860.507392 195.018625 728.187761 62.698994 728.187761 62.698994 132.748397l760.839415 0 0 132.319631L955.85804 265.068028zM195.018625 695.108365 195.018625 265.068028l595.439364 0 0-99.240235L95.779414 165.827793l0 529.279548L195.018625 695.107341zM922.778644 298.148447 228.099045 298.148447 228.099045 827.427996l694.679599 0L922.778644 298.148447z" fill="currentColor" p-id="26347" />
            </svg>
          </n-icon>
        </template>
      </n-button>
      <n-button ghost :bordered="false" icon-placement="left" type="default" size="tiny" @click="emit('recycleQa')">
        <template #icon>
          <n-icon size="22" class="assistant-reply-toolbar__icon">
            <svg t="1734598608672" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="60134" width="256" height="256">
              <path d="M934.625972 772.390495l-66.949808-130.025379C814.153156 797.841144 670.155555 903.623375 505.826905 903.623375c-174.766372 0-327.506079-120.400161-371.418194-292.809859l27.833929-7.085372c40.686654 159.656233 181.963285 271.148513 343.584266 271.148513 153.86125 0 288.48946-100.409874 336.555176-247.354598l-137.110751 51.179636-10.044774-26.907836 175.818331-65.659419 89.115644 173.096337L934.625972 772.390495zM89.766978 234.477312l-25.927509 12.339026 81.259722 170.634262 176.954201-48.850591-7.631818-27.694759-139.03252 38.356586c53.03182-138.688689 182.650947-230.154867 330.437851-230.154867 156.344814 0 292.572452 102.429881 339.010087 254.889201l27.497261-8.361435c-50.155307-164.636664-197.436698-275.259134-366.507348-275.259134-157.678182 0-296.178583 96.150874-354.877473 242.543012L89.766978 234.477312z" fill="currentColor" p-id="60135" />
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
}

.assistant-reply-toolbar__btn {
  margin-right: 15px;
}

.assistant-reply-toolbar__icon {
  color: var(--noesis-color-text-secondary);
}
</style>
