<script setup lang="ts">
import type { SubagentRunStatus } from '@/utils/parseTaskTool'
import type { ToolRunStatus, ToolUiPart, UiPart } from '@/views/chat/messageParts'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { GitNetworkOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag, NTooltip } from 'naive-ui'
import { computed } from 'vue'
import { shouldRenderToolCallCollapse } from '@/utils/parseWriteTodosInput'
import {
  parseTaskToolInput,
  parseTaskToolOutput,
} from '@/utils/parseTaskTool'
import { formatDurationMs } from '@/views/chat/messageParts'

interface Props {
  input?: Record<string, unknown>
  output?: string
  status?: ToolRunStatus
  error?: string | null
  duration_ms?: number
  /** 子 Agent 内部 parts（text / reasoning / tool，带 parent_task_call_id） */
  childParts?: UiPart[]
  defaultOpen?: boolean
  /** dark：独立深色块；light：嵌入助手气泡、与 ToolCallCollapse 对齐 */
  appearance?: 'dark' | 'light'
}

const props = withDefaults(defineProps<Props>(), {
  input: () => ({}),
  output: '',
  status: undefined,
  error: null,
  duration_ms: undefined,
  childParts: () => [],
  defaultOpen: false,
  appearance: 'light',
})

const childTimelineParts = computed(() => props.childParts ?? [])

const DISPLAY_MAX = 32_000

function truncateForDisplay(s: string, max: number): string {
  if (s.length <= max) {
    return s
  }
  return `${s.slice(0, max)}\n\n…（共 ${s.length} 字符，已截断展示）`
}

const parsedInput = computed(() => parseTaskToolInput(props.input ?? {}))

const parsedOutput = computed(() =>
  parseTaskToolOutput({
    output: props.output,
    status: props.status,
    error: props.error,
  }),
)

const runStatus = computed<SubagentRunStatus>(() => parsedOutput.value.status)

const resultDisplay = computed(() => {
  const raw = parsedOutput.value.result?.trim()
  if (!raw) {
    return ''
  }
  return truncateForDisplay(raw, DISPLAY_MAX)
})

const errorDisplay = computed(() => {
  const raw = parsedOutput.value.error?.trim()
  if (!raw) {
    return ''
  }
  return truncateForDisplay(raw, DISPLAY_MAX)
})

const promptDisplay = computed(() => {
  const raw = parsedInput.value.prompt
  if (!raw) {
    return ''
  }
  return truncateForDisplay(raw, DISPLAY_MAX)
})

const subagentTypeLabel = computed(() => parsedInput.value.subagent_type)

const TITLE_TOOLTIP_MAX = 500

const descriptionTooltip = computed(() => {
  const raw = parsedInput.value.description?.trim()
  if (!raw) {
    return ''
  }
  if (raw.length <= TITLE_TOOLTIP_MAX) {
    return raw
  }
  return `${raw.slice(0, TITLE_TOOLTIP_MAX)}…`
})

const durationDisplay = computed(() => {
  if (props.duration_ms == null || props.duration_ms < 0) {
    return ''
  }
  return formatDurationMs(props.duration_ms)
})
</script>

<template>
  <n-collapse class="subagent-call" :class="{ 'subagent-call--light': appearance === 'light' }">
    <n-collapse-item :name="parsedInput.description" :default-expanded="defaultOpen">
      <template #header>
        <div class="subagent-header">
          <div class="subagent-header__icon">
            <n-icon :size="17">
              <GitNetworkOutline />
            </n-icon>
          </div>
          <div class="subagent-header__middle">
            <n-tooltip
              placement="top"
              :delay="2000"
              :disabled="!descriptionTooltip"
              :style="{ maxWidth: '420px' }"
            >
              <template #trigger>
                <span class="subagent-title">{{ parsedInput.description }}</span>
              </template>
              {{ descriptionTooltip }}
            </n-tooltip>
            <div class="subagent-header__tags">
              <span v-if="durationDisplay" class="subagent-duration">{{ durationDisplay }}</span>
              <n-tag type="info" size="small" round bordered>{{ subagentTypeLabel }}</n-tag>
              <n-tag v-if="runStatus === 'in_progress'" type="warning" size="small" round bordered>进行中</n-tag>
              <n-tag v-else-if="runStatus === 'completed'" type="success" size="small" round bordered>完成</n-tag>
              <n-tag v-else-if="runStatus === 'failed'" type="error" size="small" round bordered>失败</n-tag>
            </div>
          </div>
        </div>
      </template>

      <div class="subagent-content">
        <div v-if="promptDisplay" class="subagent-section subagent-section--prompt">
          <div class="subagent-section__label">任务指令</div>
          <div class="subagent-section__body">
            <pre>{{ promptDisplay }}</pre>
          </div>
        </div>
        <div v-if="resultDisplay" class="subagent-section subagent-section--result">
          <div class="subagent-section__label">结果</div>
          <div class="subagent-section__body">
            <pre>{{ resultDisplay }}</pre>
          </div>
        </div>
        <div v-if="errorDisplay" class="subagent-section subagent-section--error">
          <div class="subagent-section__label">错误</div>
          <div class="subagent-section__body">
            <pre>{{ errorDisplay }}</pre>
          </div>
        </div>
        <div v-if="childTimelineParts.length > 0" class="subagent-section subagent-section--timeline">
          <div class="subagent-section__label">执行过程</div>
          <div class="subagent-timeline">
            <template v-for="(child, ci) in childTimelineParts" :key="child.id ?? ci">
              <div
                v-if="child.type === 'text' && (child.content || child.status === 'streaming')"
                class="subagent-narrative"
              >
                <pre>{{ child.content }}</pre>
              </div>
              <ReasoningBlock
                v-else-if="child.type === 'reasoning' && (child.content || child.status === 'streaming')"
                :reasoning="child.content"
                :defaultOpen="false"
                :streaming="child.status === 'streaming'"
                appearance="light"
              />
              <ToolCallCollapse
                v-else-if="child.type === 'tool' && shouldRenderToolCallCollapse(child.name, child.input)"
                appearance="light"
                :name="child.name"
                :arguments="child.input"
                :result="child.output"
                :error="child.error"
                :status="child.status"
                :duration_ms="child.duration_ms"
              />
            </template>
          </div>
        </div>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
.subagent-call {
  --tool-accent: var(--noesis-block-dark-accent);
  background: var(--noesis-block-dark-bg);
  border: 1px solid var(--noesis-block-dark-border);
  border-radius: var(--noesis-radius-lg);
  margin: 10px 0;
  box-shadow: var(--noesis-shadow-block-dark-lg);
  border-left: 3px solid var(--tool-accent);
}

.subagent-call--light {
  --tool-accent: var(--noesis-block-light-accent);
  box-sizing: border-box;
  width: 90%;
  max-width: 100%;
  margin: 8px auto;
  background: var(--noesis-block-light-bg);
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-lg);
  border-left: 3px solid var(--tool-accent);
  box-shadow: var(--noesis-shadow-sm);
}

.subagent-call :deep(.n-collapse-item) {
  margin: 0;
}

.subagent-call :deep(.n-collapse-item__header) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: 0;
  padding: 0 10px 0 0;
}

.subagent-call :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.subagent-call :deep(.n-collapse-item__content-inner) {
  padding-top: 0;
}

.subagent-call :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-dark-border-inner);
}

.subagent-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.subagent-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  color: var(--noesis-block-dark-text);
  font-size: 13px;
  padding: 11px 14px 11px 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.subagent-header__middle {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.subagent-header__tags {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 6px;
}

.subagent-duration {
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--noesis-block-dark-text-muted);
  flex-shrink: 0;
}

.subagent-call--light .subagent-duration {
  color: var(--noesis-color-text-muted);
}

.subagent-header__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-block-dark-bg-icon);
  color: var(--noesis-block-dark-icon);
  flex-shrink: 0;
}

.subagent-header:hover {
  background: var(--noesis-block-dark-bg-hover);
}

.subagent-call--light .subagent-header {
  color: var(--noesis-block-light-text);
}

.subagent-call--light .subagent-header__icon {
  background: var(--noesis-color-primary-bg-icon);
  color: var(--noesis-block-light-icon);
}

.subagent-call--light .subagent-header:hover {
  background: var(--noesis-color-primary-bg-hover);
}

.subagent-title {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-size: 13px;
  color: var(--noesis-block-dark-text-name);
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-call--light .subagent-title {
  color: var(--noesis-block-light-text-name);
}

.subagent-content {
  padding: 0 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.subagent-section__label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--noesis-block-dark-text-label);
  margin-bottom: 6px;
}

.subagent-call--light .subagent-section__label {
  color: var(--noesis-color-text-muted);
}

.subagent-section__body {
  border-radius: 8px;
  padding: 10px 12px;
  border: 1px solid var(--noesis-block-dark-border-section);
  background: var(--noesis-block-dark-bg-section);
}

.subagent-section--prompt .subagent-section__body {
  border-color: var(--noesis-block-dark-border-args);
}

.subagent-section--result .subagent-section__body {
  border-color: var(--noesis-block-dark-border-result);
  background: var(--noesis-block-dark-bg-result);
}

.subagent-section--error .subagent-section__body {
  border-color: var(--noesis-block-dark-border-error);
  background: var(--noesis-block-dark-bg-error);
}

.subagent-content pre {
  margin: 0;
  color: var(--noesis-block-dark-text-code);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.subagent-section--result pre {
  color: var(--noesis-block-dark-text-result);
}

.subagent-section--error pre {
  color: var(--noesis-block-dark-text-error);
}

.subagent-call--light .subagent-section__body {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-code);
}

.subagent-call--light .subagent-section--prompt .subagent-section__body {
  border-color: var(--noesis-color-border-args);
}

.subagent-call--light .subagent-content pre {
  color: var(--noesis-block-light-text-code);
}

.subagent-call--light .subagent-section--result .subagent-section__body {
  border-color: var(--noesis-color-border-result);
  background: var(--noesis-block-light-bg-result);
}

.subagent-call--light .subagent-section--result pre {
  color: var(--noesis-block-light-text-result);
}

.subagent-call--light .subagent-section--error .subagent-section__body {
  border-color: var(--noesis-color-border-error);
  background: var(--noesis-block-light-bg-error);
}

.subagent-call--light .subagent-section--error pre {
  color: var(--noesis-block-light-text-error);
}

.subagent-timeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.subagent-timeline :deep(.tool-call) {
  margin: 0;
  width: 100%;
}

.subagent-narrative {
  border-radius: 8px;
  padding: 10px 12px;
  border: 1px solid var(--noesis-color-border-code);
  background: var(--noesis-color-bg-elevated);
}

.subagent-call--light .subagent-narrative {
  border-color: var(--noesis-block-light-border);
  background: var(--noesis-block-light-bg-narrative);
}

.subagent-narrative pre {
  margin: 0;
  color: var(--noesis-block-light-text-code);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  font-family: inherit;
}
</style>
