<script setup lang="ts">
import { BuildOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { computed } from 'vue'
import { formatDurationMs } from '@/views/chat/messageParts'

interface Props {
  name: string
  arguments?: any
  result?: string
  error?: string | null
  status?: 'running' | 'success' | 'error'
  duration_ms?: number
  defaultOpen?: boolean
  /** dark：独立深色块；light：嵌入助手气泡、与正文对齐的浅色样式 */
  appearance?: 'dark' | 'light'
}

const props = withDefaults(defineProps<Props>(), {
  arguments: null,
  result: '',
  error: null,
  status: undefined,
  duration_ms: undefined,
  defaultOpen: false,
  appearance: 'dark',
})

/** 展示上限，避免超大 JSON 阻塞主线程与布局 */
const DISPLAY_MAX = 32_000

function truncateForDisplay(s: string, max: number): string {
  if (s.length <= max) {
    return s
  }
  return `${s.slice(0, max)}\n\n…（共 ${s.length} 字符，已截断展示）`
}

function safeStringify(obj: unknown): string {
  const seen = new WeakSet<object>()
  try {
    return JSON.stringify(obj, (_key, value) => {
      if (typeof value === 'object' && value !== null) {
        if (seen.has(value)) {
          return '[Circular]'
        }
        seen.add(value)
      }
      return value
    })
  } catch {
    return String(obj)
  }
}

function stringifyArguments(args: unknown): string {
  if (args == null || args === '') {
    return ''
  }
  if (typeof args === 'string') {
    return args
  }
  return safeStringify(args)
}

const argumentsDisplay = computed(() => {
  const raw = stringifyArguments(props.arguments)
  if (!raw) {
    return ''
  }
  return truncateForDisplay(raw, DISPLAY_MAX)
})

const errorDisplay = computed(() => {
  const raw = props.error?.trim() || (props.status === 'error' ? props.result?.trim() : '')
  if (!raw) {
    return props.status === 'error' ? '执行失败' : ''
  }
  return raw
})

const resultDisplay = computed(() => {
  const raw = props.result?.trim() ? props.result : ''
  if (!raw) {
    return ''
  }
  return truncateForDisplay(raw, DISPLAY_MAX)
})

const HEADER_SUMMARY_MAX = 240

function truncateOneLine(s: string, max: number): string {
  const t = s.replace(/\s+/g, ' ').trim()
  if (t.length <= max) {
    return t
  }
  return `${t.slice(0, max - 1)}…`
}

/** 标题行右侧摘要：把命令/路径等从参数里提出来，避免 header-main 与 header-extra 之间大块空白 */
const headerSummary = computed(() => {
  const args = props.arguments
  if (args == null || args === '') {
    return ''
  }
  if (typeof args === 'string') {
    const t = args.trim()
    return t ? truncateOneLine(t, HEADER_SUMMARY_MAX) : ''
  }
  if (typeof args !== 'object' || Array.isArray(args)) {
    return ''
  }
  const o = args as Record<string, unknown>
  const preferKeys = [
    'command',
    'cmd',
    'shell',
    'bash',
    'script',
    'query',
    'path',
    'file_path',
    'filepath',
    'url',
    'target_file',
  ]
  for (const k of preferKeys) {
    const v = o[k]
    if (typeof v === 'string' && v.trim()) {
      return truncateOneLine(v, HEADER_SUMMARY_MAX)
    }
  }
  for (const nestKey of ['args', 'arguments', 'input'] as const) {
    const inner = o[nestKey]
    if (inner && typeof inner === 'object' && !Array.isArray(inner)) {
      const ni = inner as Record<string, unknown>
      for (const k of preferKeys) {
        const v = ni[k]
        if (typeof v === 'string' && v.trim()) {
          return truncateOneLine(v, HEADER_SUMMARY_MAX)
        }
      }
    }
  }
  const tw = o._tw_tool_input
  if (typeof tw === 'string' && tw.trim()) {
    return truncateOneLine(tw, HEADER_SUMMARY_MAX)
  }
  return ''
})

const durationDisplay = computed(() => {
  if (props.duration_ms == null || props.duration_ms < 0) {
    return ''
  }
  return formatDurationMs(props.duration_ms)
})
</script>

<template>
  <n-collapse class="tool-call" :class="{ 'tool-call--light': appearance === 'light' }">
    <n-collapse-item :name="name" :default-expanded="defaultOpen">
      <template #header>
        <div class="tool-header">
          <div class="tool-header__icon">
            <n-icon :size="17" :color="appearance === 'light' ? '#3d5a80' : '#8bd9f0'">
              <BuildOutline />
            </n-icon>
          </div>
          <div class="tool-header__middle">
            <span class="tool-name" :class="{ 'tool-name--with-summary': !!headerSummary }">{{ name }}</span>
            <span
              v-if="headerSummary"
              class="tool-summary"
              :title="headerSummary"
            >{{ headerSummary }}</span>
            <div class="tool-header__tags">
              <span v-if="durationDisplay" class="tool-duration">{{ durationDisplay }}</span>
              <n-tag v-if="status === 'running'" type="warning" size="small" round bordered>运行中</n-tag>
              <n-tag v-else-if="status === 'success'" type="success" size="small" round bordered>完成</n-tag>
              <n-tag v-else-if="status === 'error'" type="error" size="small" round bordered>错误</n-tag>
            </div>
          </div>
        </div>
      </template>

      <div class="tool-content">
        <div v-if="argumentsDisplay" class="tool-section tool-section--args">
          <div class="tool-section__label">参数</div>
          <div class="tool-section__body">
            <pre>{{ argumentsDisplay }}</pre>
          </div>
        </div>
        <div v-if="errorDisplay && status === 'error'" class="tool-section tool-section--error">
          <div class="tool-section__label">错误</div>
          <div class="tool-section__body">
            <pre>{{ errorDisplay }}</pre>
          </div>
        </div>
        <div v-if="resultDisplay && status !== 'error'" class="tool-section tool-section--result">
          <div class="tool-section__label">输出</div>
          <div class="tool-section__body">
            <pre>{{ resultDisplay }}</pre>
          </div>
        </div>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
.tool-call {
  --tool-accent: #5ec8eb;
  background: linear-gradient(165deg, #252830 0%, #1a1d24 100%);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 12px;
  margin: 10px 0;
  box-shadow:
    0 1px 0 rgb(255 255 255 / 6%) inset,
    0 8px 24px rgb(0 0 0 / 28%);
  /* 勿用 overflow:hidden，会裁切右侧 header-extra（状态标签「完成」等） */
  border-left: 3px solid var(--tool-accent);
}

.tool-call--light {
  --tool-accent: #5b8bd9;
  box-sizing: border-box;
  width: 90%;
  max-width: 100%;
  margin: 8px auto;
  background: linear-gradient(180deg, #fbfcfe 0%, #f4f6fb 100%);
  border: 1px solid #e1e6ef;
  border-radius: 12px;
  border-left: 3px solid var(--tool-accent);
  box-shadow: 0 1px 2px rgb(15 23 42 / 5%);
}

.tool-call :deep(.n-collapse-item) {
  margin: 0;
}

.tool-call :deep(.n-collapse-item__header) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: 0;
  padding: 0 10px 0 0;
}

.tool-call :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.tool-call :deep(.n-collapse-item__header-extra) {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.tool-call :deep(.n-collapse-item-arrow) {
  flex-shrink: 0;
}

.tool-call :deep(.n-collapse-item__content-inner) {
  padding-top: 0;
}

.tool-call :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid rgb(255 255 255 / 6%);
}

.tool-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid #e8ecf2;
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  color: #a8dff5;
  font-size: 13px;
  padding: 11px 14px 11px 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.tool-header__middle {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.tool-header__tags {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 8px;
}

.tool-duration {
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 11px;
  color: rgb(168 223 245 / 70%);
  flex-shrink: 0;
}

.tool-call--light .tool-duration {
  color: #64748b;
}

.tool-header__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: rgb(94 200 235 / 12%);
  flex-shrink: 0;
}

.tool-header:hover {
  background: rgb(255 255 255 / 4%);
}

.tool-call--light .tool-header {
  color: #334e68;
}

.tool-call--light .tool-header__icon {
  background: rgb(91 139 217 / 12%);
}

.tool-call--light .tool-header:hover {
  background: rgb(91 139 217 / 8%);
}

.tool-name {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 12.5px;
  color: #e8f4ff;
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-name--with-summary {
  flex: 0 1 auto;
  max-width: 38%;
}

.tool-summary {
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 12px;
  font-weight: 500;
  color: rgb(168 223 245 / 88%);
}

.tool-call--light .tool-name {
  color: #1e3a5f;
}

.tool-call--light .tool-summary {
  color: #475569;
}

.tool-content {
  padding: 0 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tool-section__label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: rgb(168 223 245 / 55%);
  margin-bottom: 6px;
}

.tool-call--light .tool-section__label {
  color: #64748b;
}

.tool-section__body {
  border-radius: 8px;
  padding: 10px 12px;
  border: 1px solid rgb(255 255 255 / 7%);
  background: rgb(0 0 0 / 22%);
}

.tool-section--args .tool-section__body {
  border-color: rgb(94 200 235 / 15%);
}

.tool-section--result .tool-section__body {
  border-color: rgb(152 195 121 / 18%);
  background: rgb(20 28 22 / 55%);
}

.tool-content pre {
  margin: 0;
  color: #d0d8e0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.tool-section--result pre {
  color: #a8d997;
}

.tool-call--light .tool-section__body {
  background: #fff;
  border: 1px solid #e5e9f0;
}

.tool-call--light .tool-section--args .tool-section__body {
  border-color: #d8e2f0;
}

.tool-call--light .tool-content pre {
  color: #334155;
}

.tool-call--light .tool-section--result .tool-section__body {
  border-color: #c5e4d0;
  background: linear-gradient(180deg, #f8fdf9 0%, #f1faf3 100%);
}

.tool-call--light .tool-section--result pre {
  color: #166534;
}
</style>
