<script setup lang="ts">
import type { ToolRowVariant } from '@/utils/toolCallModel'
import type { ToolLifecycleState } from '@/views/chat/messageParts'
import { BuildOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useToolDisplayMode } from '@/hooks/useToolDisplayMode'
import { collapseCompactStyle } from '@/utils/collapseCompact'
import {
  classifyTool,
  deriveSummary,
  toolTitle,
} from '@/utils/toolCallModel'
import { formatDurationMs, isTerminalToolState, TOOL_STATE_LABELS } from '@/views/chat/messageParts'
import SearchBlock from './SearchBlock.vue'
import TerminalBlock from './TerminalBlock.vue'

interface Props {
  name: string
  arguments?: any
  result?: string
  error?: string | null
  status?: 'running' | 'success' | 'error'
  state?: ToolLifecycleState
  errorCategory?: string | null
  exitCode?: number
  truncated?: boolean
  durationMs?: number
  defaultOpen?: boolean
  /** 整轮回复结束信号（递增数字）：变化时强制收起，忽略 userTouched。 */
  collapseSignal?: number
  /** 「全部展开」信号（递增数字）：变化时展开本工具。 */
  expandSignal?: number
  /** dark：独立深色块；light：嵌入助手气泡、与正文对齐的浅色样式 */
  appearance?: 'dark' | 'light'
}

const props = withDefaults(defineProps<Props>(), {
  arguments: null,
  result: '',
  error: null,
  status: undefined,
  state: undefined,
  errorCategory: null,
  exitCode: undefined,
  truncated: undefined,
  durationMs: undefined,
  defaultOpen: false,
  collapseSignal: 0,
  expandSignal: 0,
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
  const categoryCopy: Record<string, string> = {
    network_unreachable: '连接失败，请稍后重试',
    execution_timeout: '执行超时，可调整任务后重试',
    tool_timeout: '执行超时，可调整任务后重试',
    permission_denied: '当前操作没有所需权限',
    environment_unavailable: '执行环境暂时不可用',
    command_failed: '命令执行失败',
  }
  const category = props.errorCategory || ''
  const stateCopy: Partial<Record<ToolLifecycleState, string>> = {
    failed: categoryCopy[category] || '执行失败，请检查后重试',
    timed_out: '执行超时，可调整任务后重试',
    rejected: '你已拒绝本次操作',
    cancelled: '本次操作已停止',
  }
  const base = props.state ? stateCopy[props.state] : undefined
  if (!base) {
    return ''
  }
  return props.exitCode != null ? `${base}（退出码 ${props.exitCode}）` : base
})

const state = computed<ToolLifecycleState>(() => props.state
  ?? (props.status === 'running' ? 'running' : props.status === 'error' ? 'failed' : 'succeeded'))

const failed = computed(() => ['failed', 'timed_out', 'rejected', 'cancelled'].includes(state.value))

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
  if (props.durationMs == null || props.durationMs < 0) {
    return ''
  }
  return formatDurationMs(props.durationMs)
})

// ===== compact 模式（简洁展示）=====
const { mode } = useToolDisplayMode()
const isCompact = computed(() => mode.value === 'compact')

const variant = computed<ToolRowVariant>(() => classifyTool(props.name))

/** compact 模式 header 标题：others variant 用工具名，其余用专属/variant 标题。 */
const compactTitle = computed(() => variant.value === 'others' ? props.name : toolTitle(props.name, variant.value))

/** compact 模式 header 摘要：用 deriveSummary 替代原始 command。 */
const compactSummary = computed(() => {
  if (variant.value === 'todo') {
    return ''
  }
  return deriveSummary(variant.value, props.arguments)
})

/** compact 模式失败行：错误首行替代 summary（红色）。 */
const failureLine = computed(() => {
  if (!failed.value) {
    return null
  }
  const err = props.error?.trim()
  if (err) {
    const nl = err.indexOf('\n')
    return nl === -1 ? err : err.slice(0, nl)
  }
  // 回退到 result 首行
  const r = props.result?.trim()
  if (r) {
    const nl = r.indexOf('\n')
    return nl === -1 ? r : r.slice(0, nl)
  }
  return null
})

/** compact 模式 header 实际显示的摘要：失败时用错误首行，否则用 deriveSummary。 */
const compactHeaderSummary = computed(() => failureLine.value ?? compactSummary.value)

// ===== 受控展开：跑完自动收起 =====
// Naive UI n-collapse 受控用 expanded-names 数组（非 collapse-item 的 :expanded）。
// collapse name 必须唯一（并行组里同名工具会冲突），用模块级自增 id 而非 props.name。
let _collapseSeq = 0
const collapseName = `tc-${++_collapseSeq}`
const expandedNames = ref<string[]>(props.defaultOpen ? [collapseName] : [])
let userTouched = false

function onUpdateExpandedNames(names: string[]) {
  expandedNames.value = names
  userTouched = true
}

// 跑完（进入终态）自动收起。用户已手动展开则保持。
watch(
  () => state.value,
  (s, prev) => {
    if (isCompact.value && prev === 'running' && isTerminalToolState(s) && !userTouched) {
      expandedNames.value = []
    }
  },
)

// 整轮回复结束信号：强制收起所有工具（忽略 userTouched，对齐"回复结束清场"）。
watch(
  () => props.collapseSignal,
  (cur, prev) => {
    if (isCompact.value && cur !== prev && cur > 0) {
      expandedNames.value = []
      userTouched = false
    }
  },
)

// 「全部展开」信号：展开本工具（用户显式动作，置 userTouched 防止被后续收起误伤）。
watch(
  () => props.expandSignal,
  (cur, prev) => {
    if (isCompact.value && cur !== prev && cur > 0) {
      expandedNames.value = [collapseName]
      userTouched = true
    }
  },
)

/** 判断 compact 模式该用哪个卡片渲染展开内容。 */
const compactCard = computed<'terminal' | 'search' | 'text'>(() => {
  if (variant.value === 'bash') {
    return 'terminal'
  }
  if (variant.value === 'search') {
    return 'search'
  }
  return 'text'
})
</script>

<template>
  <!-- compact 模式：variant 标题 + deriveSummary + 跑完收起 + variant 卡片（受控展开） -->
  <n-collapse
    v-if="isCompact"
    class="tool-compact"
    :class="{ 'tool-compact--expanded': expandedNames.includes(collapseName) }"
    :data-state="state"
    :expanded-names="expandedNames"
    @update:expanded-names="onUpdateExpandedNames"
  >
    <n-collapse-item :name="collapseName">
      <template #header>
        <div class="disclosure-row" :data-state="state" :data-failed="failureLine !== null || undefined">
          <n-icon :size="14" class="disclosure-row__icon">
            <BuildOutline />
          </n-icon>
          <span class="disclosure-row__title">{{ compactTitle }}</span>
          <span v-if="compactHeaderSummary" class="disclosure-row__sep" aria-hidden></span>
          <span
            v-if="compactHeaderSummary"
            class="disclosure-row__summary"
            :class="{ 'disclosure-row__summary--error': failureLine !== null }"
            :title="compactHeaderSummary"
          >{{ compactHeaderSummary }}</span>
          <span v-if="durationDisplay" class="disclosure-row__duration">{{ durationDisplay }}</span>
          <span class="disclosure-row__state-dot" :data-state="failed ? 'error' : state" aria-hidden></span>
        </div>
      </template>

      <div class="tool-body-compact">
        <!-- bash → 终端块 -->
        <TerminalBlock
          v-if="compactCard === 'terminal' && resultDisplay"
          :output="resultDisplay"
          :exit-code="exitCode"
          :truncated="truncated"
          :appearance="appearance"
        />
        <!-- search → 搜索结果块 -->
        <SearchBlock
          v-else-if="compactCard === 'search' && resultDisplay"
          :name="name"
          :output="resultDisplay"
          :input="arguments"
          :truncated="truncated"
          :appearance="appearance"
        />
        <!-- 回退：参数 + 输出文本（read/write/edit/others 都走这里，read 保留后端 cat -n 行号） -->
        <template v-else>
          <pre v-if="argumentsDisplay" class="tool-body-compact__args">{{ argumentsDisplay }}</pre>
          <pre v-if="errorDisplay && failed" class="tool-body-compact__error">{{ errorDisplay }}</pre>
          <pre v-if="resultDisplay && !failed" class="tool-body-compact__output">{{ resultDisplay }}</pre>
        </template>
      </div>
    </n-collapse-item>
  </n-collapse>

  <!-- verbose 模式：原行为（原始命令 + 完整输出，非受控） -->
  <n-collapse
    v-else
    class="tool-call"
    :class="{ 'tool-call--light': appearance === 'light' }"
    :style="collapseCompactStyle"
  >
    <n-collapse-item :name="collapseName" :default-expanded="defaultOpen">
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
              <n-tag
                :type="state === 'succeeded' ? 'success' : ['failed', 'timed_out'].includes(state) ? 'error' : ['running', 'approval_pending'].includes(state) ? 'warning' : 'default'"
                size="small"
                round
                bordered
              >
                {{ TOOL_STATE_LABELS[state] }}
              </n-tag>
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
        <div v-if="errorDisplay && failed" class="tool-section tool-section--error">
          <div class="tool-section__label">状态</div>
          <div class="tool-section__body">
            <pre>{{ errorDisplay }}</pre>
          </div>
        </div>
        <div v-if="resultDisplay && !failed" class="tool-section tool-section--result">
          <div class="tool-section__label">输出</div>
          <div class="tool-section__body">
            <pre>{{ resultDisplay }}</pre>
          </div>
        </div>
        <div v-if="truncated" class="tool-section tool-section--result">
          <div class="tool-section__label">提示</div>
          <div class="tool-section__body">输出较长，已截断展示</div>
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
  justify-content: flex-end;
  margin-left: auto;
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

/* ===== compact 模式：无装饰行式 ===== */
.tool-compact {
  /* 无 border/background/box-shadow/padding —— 一行文字混入正文流 */
}
.tool-compact :deep(.n-collapse-item) {
  margin: 0 !important;
}
.tool-compact :deep(.n-collapse-item__header) {
  padding: 0 !important;
  min-height: 0 !important;
}
.tool-compact :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}
.tool-compact :deep(.n-collapse-item-arrow) {
  font-size: 12px !important;
  margin-left: 4px !important;
  margin-right: 0 !important;
  color: var(--noesis-color-text-muted);
  /* 默认隐藏（行太密），hover 或展开时显示 */
  opacity: 0;
  transition: opacity 0.15s ease;
}
.tool-compact :deep(.n-collapse-item__header:hover) .n-collapse-item-arrow,
.tool-compact--expanded :deep(.n-collapse-item-arrow) {
  opacity: 1;
}
.tool-compact :deep(.n-collapse-item__content-wrapper) {
  border-top: none !important;
}

.disclosure-row {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  width: 100%;
  font-size: 14px;
  line-height: 24px;
  color: var(--noesis-color-text);
  padding: 1px 0;
}
.disclosure-row__icon {
  flex-shrink: 0;
  color: var(--noesis-color-text-muted);
}
.disclosure-row__title {
  flex: 0 1 auto;
  font-weight: 400;
  white-space: nowrap;
}
.disclosure-row__sep {
  flex: none;
  width: 2px;
  height: 2px;
  border-radius: 1px;
  margin: 0 2px;
  background: var(--noesis-color-text-muted);
  opacity: 0.6;
}
.disclosure-row__summary {
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--noesis-color-text-muted);
}
.disclosure-row__summary--error {
  color: #f85149 !important;
}
.disclosure-row__duration {
  flex: none;
  margin-left: auto;
  font-size: 11px;
  color: var(--noesis-color-text-muted);
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
}
/* 状态点：running 动画、error 红、succeeded 不显示 */
.disclosure-row__state-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: transparent;
}
.disclosure-row__state-dot[data-state='running'] {
  background: var(--noesis-color-primary);
  animation: tool-row-pulse 1.4s ease-in-out infinite;
}
.disclosure-row__state-dot[data-state='error'] {
  background: #f85149;
}
@keyframes tool-row-pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
/* running 扫光（可选装饰） */
.tool-compact[data-state='running'] :deep(.n-collapse-item__header) {
  position: relative;
  /* 扫光是行内装饰：越出行右缘的绝对定位盒会参与滚动容器溢出，
     制造随动画涨缩的幽灵水平滚动条，必须在行内裁切 */
  overflow: clip;
}
.tool-compact[data-state='running'] :deep(.n-collapse-item__header)::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -200px;
  width: 200px;
  background: linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--noesis-color-bg) 50%, transparent) 50%, transparent 100%);
  animation: tool-row-sweep 2.6s ease-out infinite;
  pointer-events: none;
}
@keyframes tool-row-sweep {
  0% { left: -200px; }
  100% { left: 100%; }
}

/* 展开内容：轻边框容器（收起时无此容器） */
.tool-body-compact {
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: 8px;
  background: var(--noesis-color-bg-elevated);
  padding: 8px 10px;
  margin: 4px 0 6px;
}
.tool-body-compact pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
.tool-body-compact pre + pre {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--noesis-color-border-light);
}
.tool-body-compact__args {
  color: var(--noesis-color-text);
}
.tool-body-compact__error {
  color: #f85149;
}
.tool-body-compact__output {
  color: var(--noesis-color-text);
}
</style>
