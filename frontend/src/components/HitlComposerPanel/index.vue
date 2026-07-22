<script setup lang="ts">
import { ChevronBack, ChevronForward } from '@vicons/ionicons-v5'
import { NButton, NIcon, NInput, NSpace, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'

export type HitlActionRequest = {
  tool_call_id?: string
  name?: string
  args?: Record<string, unknown>
  description?: string
}

export type HitlDraftDecision = {
  type: 'approve' | 'reject' | 'respond'
  message?: string
  grant_scope?: 'once' | 'session'
} | null

const props = defineProps<{
  kind: 'approval' | 'clarification' | string
  actionRequests: HitlActionRequest[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', payload: {
    decisions: Array<{ type: string, message?: string }>
    grant_scope?: 'once' | 'session' | null
  }): void
}>()

const index = ref(0)
const drafts = ref<HitlDraftDecision[]>([])
const freeText = ref('')
const selectedOption = ref<string | null>(null)
const slideKey = ref(0)

function isNetworkExecute(command: unknown): boolean {
  const cmd = typeof command === 'string' ? command : ''
  return /\b(?:curl|wget|ssh|scp|nc|ncat|netcat|git\s+push|pip3?\s+install|npm\s+install|pnpm\s+(?:i|install|add)|yarn\s+add)\b/i.test(cmd)
    || /\|\s*(?:ba)?sh\b/i.test(cmd)
}

function resetDrafts() {
  drafts.value = props.actionRequests.map(() => null)
  index.value = 0
  freeText.value = ''
  selectedOption.value = null
  slideKey.value = 0
}

watch(
  () => [props.kind, props.actionRequests] as const,
  () => resetDrafts(),
  { immediate: true, deep: true },
)

const total = computed(() => props.actionRequests.length)
const current = computed(() => props.actionRequests[index.value] || {})
const title = computed(() => (props.kind === 'clarification' ? 'Questions' : 'Approvals'))
const allFilled = computed(() => drafts.value.length > 0 && drafts.value.every(Boolean))
const isLast = computed(() => index.value >= total.value - 1)

const preview = computed(() => {
  const args = current.value.args || {}
  if (typeof args.command === 'string') {
    return args.command
  }
  if (typeof args.path === 'string') {
    return args.path
  }
  if (typeof args.question === 'string') {
    return args.question
  }
  return current.value.description || current.value.name || ''
})

/** 模型给的互斥选项；UI 额外提供末行自定义输入（单选，不支持多选） */
const questionOptions = computed(() => {
  const opts = current.value.args?.options
  return Array.isArray(opts) ? opts.filter((x): x is string => typeof x === 'string') : []
})

const allowSessionGrant = computed(() => {
  return current.value.name === 'execute' && isNetworkExecute(current.value.args?.command)
})

const canSubmitCustom = computed(() => freeText.value.trim().length > 0)

function goPrev() {
  if (index.value <= 0) {
    return
  }
  index.value -= 1
  slideKey.value -= 1
  syncAnswerInputs()
}

function goNext() {
  if (index.value >= total.value - 1) {
    return
  }
  index.value += 1
  slideKey.value += 1
  syncAnswerInputs()
}

function syncAnswerInputs() {
  const d = drafts.value[index.value]
  if (d?.type === 'respond') {
    const msg = d.message || ''
    if (questionOptions.value.includes(msg)) {
      selectedOption.value = msg
      freeText.value = ''
    } else {
      selectedOption.value = null
      freeText.value = msg
    }
  } else {
    freeText.value = ''
    selectedOption.value = null
  }
}

function recordAndAdvance(decision: NonNullable<HitlDraftDecision>) {
  const next = [...drafts.value]
  next[index.value] = decision
  drafts.value = next
  if (!isLast.value) {
    index.value += 1
    slideKey.value += 1
    syncAnswerInputs()
    return
  }
  if (props.kind === 'approval') {
    submitAll()
  }
}

function onApprove(grant_scope: 'once' | 'session' = 'once') {
  recordAndAdvance({ type: 'approve', grant_scope })
}

function onReject() {
  recordAndAdvance({ type: 'reject' })
}

/** 点选预设选项：单选并立即进入下一题 */
function onSelectOption(opt: string) {
  if (props.disabled) {
    return
  }
  selectedOption.value = opt
  freeText.value = ''
  recordAndAdvance({ type: 'respond', message: opt })
}

function onCustomInput() {
  if (selectedOption.value) {
    selectedOption.value = null
  }
}

/** 自定义输入提交（Enter / Next） */
function onCustomSubmit() {
  const message = freeText.value.trim()
  if (!message || props.disabled) {
    return
  }
  selectedOption.value = null
  recordAndAdvance({ type: 'respond', message })
}

function submitAll() {
  if (!allFilled.value || props.disabled) {
    return
  }
  const decisions = drafts.value.map((d) => {
    const item: { type: string, message?: string } = { type: d!.type }
    if (d!.message != null) {
      item.message = d!.message
    }
    return item
  })
  let grant_scope: 'once' | 'session' | null = null
  for (const d of drafts.value) {
    if (d?.type === 'approve' && d.grant_scope === 'session') {
      grant_scope = 'session'
      break
    }
  }
  emit('submit', { decisions, grant_scope })
}
</script>

<template>
  <div class="hitl-composer-panel" :class="{ disabled }">
    <div class="hitl-composer-panel__header">
      <span class="hitl-composer-panel__title">{{ title }}</span>
      <div class="hitl-composer-panel__pager">
        <button type="button" class="hitl-pager-btn" :disabled="index <= 0" @click="goPrev">
          <n-icon :size="14">
            <ChevronBack />
          </n-icon>
        </button>
        <span class="hitl-pager-label">{{ index + 1 }} of {{ total }}</span>
        <button type="button" class="hitl-pager-btn" :disabled="isLast" @click="goNext">
          <n-icon :size="14">
            <ChevronForward />
          </n-icon>
        </button>
      </div>
    </div>

    <div :key="slideKey" class="hitl-composer-panel__body">
      <template v-if="kind === 'clarification'">
        <NText class="hitl-question">
          {{ preview }}
        </NText>

        <div v-if="questionOptions.length" class="hitl-options">
          <button
            v-for="(opt, i) in questionOptions"
            :key="opt"
            type="button"
            class="hitl-option-btn"
            :class="{ selected: selectedOption === opt }"
            :disabled="disabled"
            @click="onSelectOption(opt)"
          >
            <span class="hitl-option-letter">{{ String.fromCharCode(65 + i) }}</span>
            <span class="hitl-option-text">{{ opt }}</span>
          </button>
        </div>

        <!-- Cursor 风格：末行始终可自定义输入；Enter 仅换行，点 Next 才提交 -->
        <div class="hitl-custom">
          <NInput
            v-model:value="freeText"
            type="textarea"
            :rows="questionOptions.length ? 2 : 3"
            :placeholder="questionOptions.length ? '输入自定义回答…' : '输入你的回答'"
            :disabled="disabled"
            @update:value="onCustomInput"
          />
        </div>
      </template>
      <template v-else>
        <div class="hitl-approval-meta">
          {{ current.name === 'execute' ? '确认执行命令' : '确认写入记忆' }}
        </div>
        <pre class="hitl-preview">{{ preview }}</pre>
      </template>
    </div>

    <div class="hitl-composer-panel__footer">
      <template v-if="kind === 'clarification'">
        <div class="hitl-footer-spacer"></div>
        <NButton
          v-if="!allFilled"
          size="small"
          type="primary"
          :disabled="disabled || !canSubmitCustom"
          @click="onCustomSubmit"
        >
          Next
        </NButton>
        <NButton
          v-else
          size="small"
          type="primary"
          :disabled="disabled"
          @click="submitAll"
        >
          Continue
        </NButton>
      </template>
      <template v-else>
        <NSpace>
          <NButton size="small" type="primary" :disabled="disabled" @click="onApprove('once')">
            允许一次
          </NButton>
          <NButton
            v-if="allowSessionGrant"
            size="small"
            :disabled="disabled"
            @click="onApprove('session')"
          >
            本会话允许
          </NButton>
          <NButton size="small" type="error" ghost :disabled="disabled" @click="onReject">
            拒绝
          </NButton>
        </NSpace>
      </template>
    </div>
  </div>
</template>

<style scoped lang="scss">
.hitl-composer-panel {
  width: 100%;
  border: 1px solid var(--noesis-border-subtle, #e5e5e5);
  border-radius: 10px;
  background: var(--noesis-surface-muted, #f7f7f5);
  overflow: hidden;
}

.hitl-composer-panel.disabled {
  opacity: 0.75;
  pointer-events: none;
}

.hitl-composer-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 6px;
}

.hitl-composer-panel__title {
  font-weight: 600;
  font-size: 13px;
}

.hitl-composer-panel__pager {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--noesis-text-muted, #666);
}

.hitl-pager-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  color: inherit;

  &:disabled {
    opacity: 0.35;
    cursor: default;
  }

  &:not(:disabled):hover {
    background: rgba(0, 0, 0, 0.06);
  }
}

.hitl-composer-panel__body {
  padding: 4px 12px 10px;
  animation: hitl-slide-in 0.18s ease-out;
}

@keyframes hitl-slide-in {
  from {
    opacity: 0.4;
    transform: translateX(12px);
  }

  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.hitl-question {
  display: block;
  margin-bottom: 10px;
  white-space: pre-wrap;
  font-size: 14px;
  line-height: 1.45;
}

.hitl-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.hitl-option-btn {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  text-align: left;
  border: 1px solid var(--noesis-border-subtle, #e5e5e5);
  border-radius: 8px;
  background: var(--noesis-surface, #fff);
  cursor: pointer;
  font-size: 13px;
  line-height: 1.4;
  color: inherit;

  &:hover:not(:disabled) {
    border-color: #c8c8c4;
    background: #fafaf8;
  }

  &.selected {
    border-color: #3b82f6;
    background: #eff6ff;
  }

  &:disabled {
    cursor: default;
    opacity: 0.6;
  }
}

.hitl-option-letter {
  flex-shrink: 0;
  width: 18px;
  font-weight: 600;
  color: var(--noesis-text-muted, #666);
}

.hitl-option-text {
  flex: 1;
  white-space: pre-wrap;
  word-break: break-word;
}

.hitl-custom {
  margin-top: 2px;
}

.hitl-approval-meta {
  font-size: 12px;
  color: var(--noesis-text-muted, #666);
  margin-bottom: 6px;
}

.hitl-preview {
  margin: 0;
  padding: 8px 10px;
  max-height: 140px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 6px;
  background: var(--noesis-surface, #fff);
  border: 1px solid var(--noesis-border-subtle, #ebebeb);
}

.hitl-composer-panel__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 12px 12px;
}

.hitl-footer-spacer {
  flex: 1;
}
</style>
