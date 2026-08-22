<script setup lang="ts">
import type { DisplayPartEntry } from '@/utils/groupAssistantParts'
import type { SubagentRunStatus } from '@/utils/parseTaskTool'
import type { ToolLifecycleState, ToolRunStatus, UiPart } from '@/views/chat/messageParts'
import { GitNetworkOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag, NTooltip } from 'naive-ui'
import { computed } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview/index.vue'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { buildChildDisplayParts } from '@/utils/groupAssistantParts'
import {
  parseTaskToolInput,
  parseTaskToolOutput,
} from '@/utils/parseTaskTool'
import { shouldRenderToolCallCollapse } from '@/utils/parseWriteTodosInput'
import { formatDurationMs } from '@/views/chat/messageParts'

interface Props {
  input?: Record<string, unknown>
  output?: string
  status?: ToolRunStatus
  state?: ToolLifecycleState
  error?: string | null
  durationMs?: number
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
  state: undefined,
  error: null,
  durationMs: undefined,
  childParts: () => [],
  defaultOpen: false,
  appearance: 'light',
})

const childTimelineParts = computed(() => props.childParts ?? [])
const childDisplayParts = computed(() => buildChildDisplayParts(childTimelineParts.value))

function entryKey(entry: DisplayPartEntry, fallback: number): string {
  if (entry.kind === 'parallel_tools') {
    return `pg:${entry.parts[0]?.tool_call_id ?? entry.parts[0]?.id ?? fallback}`
  }
  return entry.part.tool_call_id ?? entry.part.id ?? String(fallback)
}

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
    state: props.state,
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
              <n-tag v-else-if="runStatus === 'completed'" type="success" size="small" round bordered>已完成</n-tag>
              <n-tag v-else-if="runStatus === 'failed'" type="error" size="small" round bordered>失败</n-tag>
            </div>
          </div>
        </div>
      </template>

      <div class="subagent-content">
        <div v-if="promptDisplay" class="subagent-section subagent-section--prompt">
          <div class="subagent-section__label">任务指令</div>
          <MarkdownPreview
            class="subagent-markdown"
            :content="promptDisplay"
            :show-action-bar="false"
            variant="segment"
          />
        </div>
        <div v-if="resultDisplay" class="subagent-section subagent-section--result">
          <div class="subagent-section__label">结果</div>
          <MarkdownPreview
            class="subagent-markdown"
            :content="resultDisplay"
            :show-action-bar="false"
            variant="segment"
          />
        </div>
        <div v-if="errorDisplay" class="subagent-section subagent-section--error">
          <div class="subagent-section__label">错误</div>
          <MarkdownPreview
            class="subagent-markdown"
            :content="errorDisplay"
            :show-action-bar="false"
            variant="segment"
          />
        </div>
        <div v-if="childTimelineParts.length > 0" class="subagent-section subagent-section--timeline">
          <div class="subagent-section__label">执行过程</div>
          <div class="subagent-timeline">
            <template
              v-for="(entry, ci) in childDisplayParts"
              :key="entryKey(entry, ci)"
            >
              <div
                v-if="entry.kind === 'part' && entry.part.type === 'text' && (entry.part.content || entry.part.status === 'streaming')"
                class="subagent-narrative"
              >
                <MarkdownPreview
                  class="subagent-markdown"
                  :content="entry.part.content"
                  :show-action-bar="false"
                  variant="segment"
                />
              </div>
              <ReasoningBlock
                v-else-if="entry.kind === 'part' && entry.part.type === 'reasoning' && (entry.part.content || entry.part.status === 'streaming')"
                :reasoning="entry.part.content"
                :defaultOpen="false"
                :streaming="entry.part.status === 'streaming'"
                appearance="light"
              />
              <ToolCallCollapse
                v-else-if="entry.kind === 'part' && entry.part.type === 'tool' && shouldRenderToolCallCollapse(entry.part.name, entry.part.input)"
                appearance="light"
                :name="entry.part.name"
                :arguments="entry.part.input"
                :result="entry.part.output"
                :error="entry.part.error"
                :status="entry.part.status"
                :state="entry.part.state"
                :error-category="entry.part.errorCategory"
                :exit-code="entry.part.exit_code"
                :truncated="entry.part.truncated"
                :duration-ms="entry.part.duration_ms"
              />
              <div
                v-else-if="entry.kind === 'parallel_tools'"
                class="subagent-parallel-tools"
              >
                <n-collapse>
                  <n-collapse-item name="parallel-tools" :default-expanded="true">
                    <template #header>
                      <div class="subagent-parallel-tools__header">
                        并行工具 · {{ entry.parts.length }} 个
                      </div>
                    </template>
                    <div class="subagent-parallel-tools__body">
                      <ToolCallCollapse
                        v-for="tp in entry.parts"
                        :key="tp.tool_call_id ?? tp.id"
                        appearance="light"
                        :name="tp.name"
                        :arguments="tp.input"
                        :result="tp.output"
                        :error="tp.error"
                        :status="tp.status"
                        :state="tp.state"
                        :error-category="tp.errorCategory"
                        :exit-code="tp.exit_code"
                        :truncated="tp.truncated"
                        :duration-ms="tp.duration_ms"
                      />
                    </div>
                  </n-collapse-item>
                </n-collapse>
              </div>
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
  width: 100%;
  max-width: 100%;
  margin: 5px 0;
  background: var(--noesis-block-light-bg);
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-md);
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
  justify-content: flex-end;
  margin-left: auto;
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

.subagent-markdown {
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--noesis-color-border-code);
  border-radius: 8px;
  background: var(--noesis-color-bg-elevated);
}

.subagent-markdown :deep(.markdown-preview__body) {
  height: auto;
  overflow: visible;
  padding: 0;
}

.subagent-markdown :deep(.markdown-wrapper) {
  margin: 0;
  padding: 10px 12px;
  border-radius: 0;
  background: transparent;
  color: var(--noesis-color-text-body);
  font-size: 13px;
  line-height: 1.6;
}

.subagent-section--result .subagent-markdown {
  border-color: var(--noesis-color-border-result);
}

.subagent-section--error .subagent-markdown {
  border-color: var(--noesis-color-border-error);
}

.subagent-timeline {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.subagent-parallel-tools {
  margin: 2px 0;
  padding: 4px 8px;
  border-left: 3px solid var(--noesis-block-light-accent);
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-block-light-bg);
}

.subagent-parallel-tools :deep(.n-collapse-item__header) {
  padding: 0 !important;
}

.subagent-parallel-tools :deep(.n-collapse-item__content-inner) {
  padding: 0 !important;
}

.subagent-parallel-tools :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.subagent-parallel-tools__header {
  font-size: 11px;
  color: var(--noesis-color-text-secondary);
  margin-bottom: 2px;
}

.subagent-parallel-tools__body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.subagent-parallel-tools__body :deep(.tool-call) {
  margin: 0;
  box-shadow: none;
}

.subagent-timeline :deep(.tool-call),
.subagent-timeline :deep(.reasoning-call) {
  margin: 0;
  width: 100%;
  border-radius: var(--noesis-radius-sm);
  box-shadow: none;
}

/* 子过程略缩小：与主时间线区分层级 */

.subagent-timeline :deep(.reasoning-header),
.subagent-timeline :deep(.tool-header) {
  padding: 5px 8px 5px 6px;
  gap: 6px;
  font-size: 11px;
}

.subagent-timeline :deep(.reasoning-header__icon),
.subagent-timeline :deep(.tool-header__icon) {
  width: 22px;
  height: 22px;
  border-radius: 6px;
}

.subagent-timeline :deep(.reasoning-name),
.subagent-timeline :deep(.tool-name) {
  font-size: 11px;
}

.subagent-timeline :deep(.reasoning-content),
.subagent-timeline :deep(.tool-content) {
  padding: 0 8px 8px;
}

.subagent-timeline :deep(.reasoning-section__body),
.subagent-timeline :deep(.tool-section__body) {
  padding: 6px 8px;
  border-radius: 6px;
}

.subagent-timeline :deep(.reasoning-content pre),
.subagent-timeline :deep(.tool-content pre),
.subagent-timeline :deep(.tool-section__label) {
  font-size: 11px;
  line-height: 1.4;
}

.subagent-timeline :deep(.n-tag) {
  font-size: 10px;
  height: 18px;
  line-height: 18px;
  padding: 0 6px;
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
</style>
