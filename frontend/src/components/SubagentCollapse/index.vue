<script setup lang="ts">
import type { SubagentRunStatus } from '@/utils/parseTaskTool'
import type { ToolRunStatus, ToolUiPart, UiPart } from '@/views/chat/messageParts'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { GitNetworkOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { computed } from 'vue'
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
  durationMs?: number
  /** 子 Agent 内部 parts（text / reasoning / tool，带 parentTaskCallId） */
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
  durationMs: undefined,
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

const durationDisplay = computed(() => {
  if (props.durationMs == null || props.durationMs < 0) {
    return ''
  }
  return formatDurationMs(props.durationMs)
})
</script>

<template>
  <n-collapse class="subagent-call" :class="{ 'subagent-call--light': appearance === 'light' }">
    <n-collapse-item :name="parsedInput.description" :default-expanded="defaultOpen">
      <template #header>
        <div class="subagent-header">
          <div class="subagent-header__icon">
            <n-icon :size="17" :color="appearance === 'light' ? '#3d5a80' : '#8bd9f0'">
              <GitNetworkOutline />
            </n-icon>
          </div>
          <div class="subagent-header__middle">
            <span class="subagent-title">{{ parsedInput.description }}</span>
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
                v-else-if="child.type === 'tool'"
                appearance="light"
                :toolName="child.toolName"
                :arguments="child.input"
                :result="child.status === 'error' ? (child.error || child.output || '') : child.output"
                :status="child.status"
                :duration-ms="child.durationMs"
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
  --tool-accent: #5ec8eb;
  background: linear-gradient(165deg, #252830 0%, #1a1d24 100%);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 12px;
  margin: 10px 0;
  box-shadow:
    0 1px 0 rgb(255 255 255 / 6%) inset,
    0 8px 24px rgb(0 0 0 / 28%);
  border-left: 3px solid var(--tool-accent);
}

.subagent-call--light {
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
  border-top: 1px solid rgb(255 255 255 / 6%);
}

.subagent-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid #e8ecf2;
}

.subagent-header {
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
  color: rgb(168 223 245 / 70%);
  flex-shrink: 0;
}

.subagent-call--light .subagent-duration {
  color: #64748b;
}

.subagent-header__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: rgb(94 200 235 / 12%);
  flex-shrink: 0;
}

.subagent-header:hover {
  background: rgb(255 255 255 / 4%);
}

.subagent-call--light .subagent-header {
  color: #334e68;
}

.subagent-call--light .subagent-header__icon {
  background: rgb(91 139 217 / 12%);
}

.subagent-call--light .subagent-header:hover {
  background: rgb(91 139 217 / 8%);
}

.subagent-title {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-size: 13px;
  color: #e8f4ff;
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-call--light .subagent-title {
  color: #1e3a5f;
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
  color: rgb(168 223 245 / 55%);
  margin-bottom: 6px;
}

.subagent-call--light .subagent-section__label {
  color: #64748b;
}

.subagent-section__body {
  border-radius: 8px;
  padding: 10px 12px;
  border: 1px solid rgb(255 255 255 / 7%);
  background: rgb(0 0 0 / 22%);
}

.subagent-section--prompt .subagent-section__body {
  border-color: rgb(94 200 235 / 15%);
}

.subagent-section--result .subagent-section__body {
  border-color: rgb(152 195 121 / 18%);
  background: rgb(20 28 22 / 55%);
}

.subagent-section--error .subagent-section__body {
  border-color: rgb(239 68 68 / 25%);
  background: rgb(40 20 20 / 55%);
}

.subagent-content pre {
  margin: 0;
  color: #d0d8e0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.subagent-section--result pre {
  color: #a8d997;
}

.subagent-section--error pre {
  color: #fca5a5;
}

.subagent-call--light .subagent-section__body {
  background: #fff;
  border: 1px solid #e5e9f0;
}

.subagent-call--light .subagent-section--prompt .subagent-section__body {
  border-color: #d8e2f0;
}

.subagent-call--light .subagent-content pre {
  color: #334155;
}

.subagent-call--light .subagent-section--result .subagent-section__body {
  border-color: #c5e4d0;
  background: linear-gradient(180deg, #f8fdf9 0%, #f1faf3 100%);
}

.subagent-call--light .subagent-section--result pre {
  color: #166534;
}

.subagent-call--light .subagent-section--error .subagent-section__body {
  border-color: #fecaca;
  background: linear-gradient(180deg, #fffbfb 0%, #fef2f2 100%);
}

.subagent-call--light .subagent-section--error pre {
  color: #b91c1c;
}

.subagent-timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.subagent-timeline :deep(.tool-call) {
  margin: 0;
  width: 100%;
}

.subagent-narrative {
  border-radius: 8px;
  padding: 10px 12px;
  border: 1px solid #e5e9f0;
  background: #fff;
}

.subagent-call--light .subagent-narrative {
  border-color: #e1e6ef;
  background: linear-gradient(180deg, #fff 0%, #f8fafc 100%);
}

.subagent-narrative pre {
  margin: 0;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  font-family: inherit;
}
</style>
