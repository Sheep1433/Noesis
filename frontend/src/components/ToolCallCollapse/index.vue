<script setup lang="ts">
import { BuildOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { computed } from 'vue'
import { collapseCompactStyle } from '@/utils/collapseCompact'
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
  <n-collapse
    class="tool-call"
    :class="{ 'tool-call--light': appearance === 'light' }"
    :style="collapseCompactStyle"
  >
    <n-collapse-item :name="name" :default-expanded="defaultOpen">
      <template #header>
        <div class="tool-header">
          <div class="tool-header__icon">
            <n-icon :size="14">
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
  --tool-accent: var(--noesis-block-dark-accent);
  background: var(--noesis-block-dark-bg);
  border: 1px solid var(--noesis-block-dark-border);
  border-radius: var(--noesis-radius-sm);
  margin: 3px 0;
  box-shadow: var(--noesis-shadow-block-dark);
  border-left: 3px solid var(--tool-accent);
}

.tool-call--light {
  --tool-accent: var(--noesis-block-light-accent);
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  margin: 5px 0;
  background: var(--noesis-block-light-bg);
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-md);
  border-left: 3px solid var(--tool-accent);
  box-shadow: var(--noesis-shadow-sm);
}

.tool-call :deep(.n-collapse-item) {
  margin: 0 !important;
}

.tool-call :deep(.n-collapse-item__header) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 4px;
  min-width: 0;
  min-height: 0;
  padding: 0 6px 0 0 !important;
}

.tool-call :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.tool-call :deep(.n-collapse-item__header-extra) {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.tool-call :deep(.n-collapse-item-arrow) {
  flex-shrink: 0;
  font-size: 14px !important;
  margin-right: 4px !important;
}

.tool-call :deep(.n-collapse-item__content-inner) {
  padding-top: 0 !important;
}

.tool-call :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-dark-border-inner);
}

.tool-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  color: var(--noesis-block-dark-text);
  font-size: 12px;
  padding: 7px 10px 7px 8px;
  cursor: pointer;
  transition: background 0.15s ease;
  line-height: 1.3;
}

.tool-header__middle {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.tool-header__tags {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 6px;
}

.tool-duration {
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--noesis-block-dark-text-muted);
  flex-shrink: 0;
}

.tool-call--light .tool-duration {
  color: var(--noesis-color-text-muted);
}

.tool-header__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--noesis-block-dark-bg-icon);
  color: var(--noesis-block-dark-icon);
  flex-shrink: 0;
}

.tool-header:hover {
  background: var(--noesis-block-dark-bg-hover);
}

.tool-call--light .tool-header {
  color: var(--noesis-block-light-text);
}

.tool-call--light .tool-header__icon {
  background: var(--noesis-color-primary-bg-icon);
  color: var(--noesis-block-light-icon);
}

.tool-call--light .tool-header:hover {
  background: var(--noesis-color-primary-bg-hover);
}

.tool-name {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 12px;
  color: var(--noesis-block-dark-text-name);
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-name--with-summary {
  flex: 0 1 auto;
  max-width: 34%;
}

.tool-summary {
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 11px;
  font-weight: 500;
  color: var(--noesis-block-dark-text-summary);
}

.tool-call--light .tool-name {
  color: var(--noesis-block-light-text-name);
}

.tool-call--light .tool-summary {
  color: var(--noesis-color-text-muted);
}

.tool-content {
  padding: 0 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tool-section__label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--noesis-block-dark-text-label);
  margin-bottom: 4px;
}

.tool-call--light .tool-section__label {
  color: var(--noesis-color-text-muted);
}

.tool-section__body {
  border-radius: 7px;
  padding: 8px 10px;
  border: 1px solid var(--noesis-block-dark-border-section);
  background: var(--noesis-block-dark-bg-section);
}

.tool-section--args .tool-section__body {
  border-color: var(--noesis-block-dark-border-args);
}

.tool-section--result .tool-section__body {
  border-color: var(--noesis-block-dark-border-result);
  background: var(--noesis-block-dark-bg-result);
}

.tool-content pre {
  margin: 0;
  color: var(--noesis-block-dark-text-code);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.tool-section--result pre {
  color: var(--noesis-block-dark-text-result);
}

.tool-call--light .tool-section__body {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-code);
}

.tool-call--light .tool-section--args .tool-section__body {
  border-color: var(--noesis-color-border-args);
}

.tool-call--light .tool-content pre {
  color: var(--noesis-block-light-text-code);
}

.tool-call--light .tool-section--result .tool-section__body {
  border-color: var(--noesis-color-border-result);
  background: var(--noesis-block-light-bg-result);
}

.tool-call--light .tool-section--result pre {
  color: var(--noesis-block-light-text-result);
}
</style>
