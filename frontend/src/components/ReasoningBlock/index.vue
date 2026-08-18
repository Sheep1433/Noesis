<script setup lang="ts">
import { BulbOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { computed, ref, watch, watchEffect } from 'vue'
import { useToolDisplayMode } from '@/hooks/useToolDisplayMode'
import { collapseCompactStyle } from '@/utils/collapseCompact'

interface Props {
  reasoning?: string
  defaultOpen?: boolean
  /** 与 ToolCallCollapse 的 appearance 一致：嵌入助手气泡用 light */
  appearance?: 'dark' | 'light'
  /** 流式生成中：标题为「思考中…」，状态标签为「运行中」 */
  streaming?: boolean
  /** 整轮操作：与工具行共享全部折叠/展开控制。 */
  collapseSignal?: number
  expandSignal?: number
}

const props = withDefaults(defineProps<Props>(), {
  reasoning: '',
  defaultOpen: false,
  appearance: 'light',
  streaming: false,
  collapseSignal: 0,
  expandSignal: 0,
})

const { mode } = useToolDisplayMode()
const isCompact = computed(() => mode.value === 'compact')

// compact 行式：streaming 显示末行（滚到底），完成显示首行（对齐 dsh ReasoningRow）
function firstLine(text: string): string {
  const nl = text.indexOf('\n')
  return nl === -1 ? text : text.slice(0, nl)
}
function latestLine(text: string): string {
  const visible = text.trimEnd()
  const nl = visible.lastIndexOf('\n')
  return nl === -1 ? visible : visible.slice(nl + 1)
}

const compactSummary = computed(() => {
  const text = props.reasoning || ''
  if (!text) {
    return ''
  }
  return props.streaming ? latestLine(text) : firstLine(text)
})

// streaming 时 summary 容器滚到底
const summaryRef = ref<HTMLElement | null>(null)
watchEffect(() => {
  if (isCompact.value && props.streaming && summaryRef.value && compactSummary.value) {
    const el = summaryRef.value
    el.scrollLeft = el.scrollWidth - el.clientWidth
  }
})

// compact 受控展开 + 跑完收起
const collapseName = 'reasoning'
const expandedNames = ref<string[]>(props.defaultOpen ? [collapseName] : [])
let userTouched = false
function onUpdateExpandedNames(names: string[]) {
  expandedNames.value = names
  userTouched = true
}
watch(
  () => props.streaming,
  (cur, prev) => {
    if (isCompact.value && prev === true && cur === false && !userTouched) {
      expandedNames.value = []
    }
  },
)
watch(
  () => props.collapseSignal,
  (cur, prev) => {
    if (isCompact.value && cur !== prev && cur > 0) {
      expandedNames.value = []
      userTouched = false
    }
  },
)
watch(
  () => props.expandSignal,
  (cur, prev) => {
    if (isCompact.value && cur !== prev && cur > 0) {
      expandedNames.value = [collapseName]
      userTouched = true
    }
  },
)
</script>

<template>
  <!-- compact 模式：行式 Think · 首行/末行（对齐 dsh ReasoningRow） -->
  <n-collapse
    v-if="isCompact"
    class="reasoning-compact"
    :class="{ 'reasoning-compact--expanded': expandedNames.includes(collapseName) }"
    :data-state="streaming ? 'running' : 'ok'"
    :expanded-names="expandedNames"
    @update:expanded-names="onUpdateExpandedNames"
  >
    <n-collapse-item :name="collapseName">
      <template #header>
        <div class="disclosure-row" :data-state="streaming ? 'running' : 'ok'">
          <n-icon :size="14" class="disclosure-row__icon">
            <BulbOutline />
          </n-icon>
          <span class="disclosure-row__title">{{ streaming ? '思考中…' : 'Think' }}</span>
          <span v-if="compactSummary" class="disclosure-row__sep" aria-hidden></span>
          <span
            v-if="compactSummary"
            ref="summaryRef"
            class="disclosure-row__summary"
            :data-follow-end="streaming || undefined"
            :title="compactSummary"
          >{{ compactSummary }}</span>
          <span class="disclosure-row__state-dot" :data-state="streaming ? 'running' : 'ok'" aria-hidden></span>
        </div>
      </template>

      <div class="reasoning-body-compact">
        <pre>{{ reasoning }}</pre>
      </div>
    </n-collapse-item>
  </n-collapse>

  <!-- verbose 模式：原块状卡片 -->
  <n-collapse
    v-else
    class="reasoning-call"
    :class="{ 'reasoning-call--light': appearance === 'light', 'reasoning-call--dark': appearance === 'dark' }"
    :style="collapseCompactStyle"
  >
    <n-collapse-item name="reasoning" :default-expanded="defaultOpen">
      <template #header>
        <div class="reasoning-header">
          <div class="reasoning-header__icon">
            <n-icon :size="14">
              <BulbOutline />
            </n-icon>
          </div>
          <div class="reasoning-header__middle">
            <span class="reasoning-name">{{ streaming ? '思考中…' : '思考过程' }}</span>
            <div class="reasoning-header__tags">
              <n-tag v-if="streaming" type="warning" size="small" round bordered>运行中</n-tag>
              <n-tag v-else type="success" size="small" round bordered>已完成</n-tag>
            </div>
          </div>
        </div>
      </template>

      <div class="reasoning-content">
        <div class="reasoning-section__body">
          <pre>{{ reasoning }}</pre>
        </div>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
/* ===== verbose 模式：原块状卡片 ===== */
.reasoning-call {
  --reasoning-accent: var(--noesis-block-dark-accent);
  background: var(--noesis-block-dark-bg);
  border: 1px solid var(--noesis-block-dark-border);
  border-radius: var(--noesis-radius-sm);
  margin: 3px 0;
  box-shadow: var(--noesis-shadow-block-dark);
  border-left: 3px solid var(--reasoning-accent);
}

.reasoning-call--light {
  --reasoning-accent: var(--noesis-block-light-accent);
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  margin: 5px 0;
  background: var(--noesis-block-light-bg);
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-md);
  border-left: 3px solid var(--reasoning-accent);
  box-shadow: var(--noesis-shadow-sm);
}

.reasoning-call :deep(.n-collapse-item) {
  margin: 0 !important;
}

.reasoning-call :deep(.n-collapse-item__header) {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 4px;
  min-width: 0;
  min-height: 0;
  padding: 0 6px 0 0 !important;
}

.reasoning-call :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.reasoning-call :deep(.n-collapse-item-arrow) {
  flex-shrink: 0;
  font-size: 14px !important;
  margin-right: 4px !important;
}

.reasoning-call :deep(.n-collapse-item__content-inner) {
  padding-top: 0 !important;
}

.reasoning-call :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-dark-border-inner);
}

.reasoning-call--light :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.reasoning-header {
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

.reasoning-header__middle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.reasoning-header__tags {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-left: auto;
  flex-shrink: 0;
}

.reasoning-header__icon {
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

.reasoning-header:hover {
  background: var(--noesis-block-dark-bg-hover);
}

.reasoning-call--light .reasoning-header {
  color: var(--noesis-block-light-text);
}

.reasoning-call--light .reasoning-header__icon {
  background: var(--noesis-color-primary-bg-icon);
  color: var(--noesis-block-light-icon);
}

.reasoning-call--light .reasoning-header:hover {
  background: var(--noesis-color-primary-bg-hover);
}

.reasoning-name {
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

.reasoning-call--light .reasoning-name {
  color: var(--noesis-block-light-text-name);
}

.reasoning-content {
  padding: 0 10px 10px;
}

.reasoning-section__body {
  border-radius: 7px;
  padding: 8px 10px;
  border: 1px solid var(--noesis-block-dark-border-section);
  background: var(--noesis-block-dark-bg-section);
  border-color: var(--noesis-block-dark-border-args);
}

.reasoning-content pre {
  margin: 0;
  color: var(--noesis-block-dark-text-code);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.reasoning-call--light .reasoning-section__body {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-code);
  border-color: var(--noesis-color-border-args);
}

.reasoning-call--light .reasoning-content pre {
  color: var(--noesis-block-light-text-code);
}

/* ===== compact 模式：无装饰行式（与 ToolCallCollapse 共用 disclosure-row 结构）===== */
.reasoning-compact {
  /* 无 border/background/padding —— 一行文字混入正文流 */
}
.reasoning-compact :deep(.n-collapse-item) {
  margin: 0 !important;
}
.reasoning-compact :deep(.n-collapse-item__header) {
  padding: 0 !important;
  min-height: 0 !important;
}
.reasoning-compact :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}
.reasoning-compact :deep(.n-collapse-item-arrow) {
  font-size: 12px !important;
  margin-left: 4px !important;
  margin-right: 0 !important;
  color: var(--noesis-color-text-muted);
  opacity: 0;
  transition: opacity 0.15s ease;
}
.reasoning-compact :deep(.n-collapse-item__header:hover) .n-collapse-item-arrow,
.reasoning-compact--expanded :deep(.n-collapse-item-arrow) {
  opacity: 1;
}
.reasoning-compact :deep(.n-collapse-item__content-wrapper) {
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
.disclosure-row__state-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: transparent;
}
.disclosure-row__state-dot[data-state='running'] {
  background: var(--noesis-color-primary);
  animation: reasoning-row-pulse 1.4s ease-in-out infinite;
}
@keyframes reasoning-row-pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
.reasoning-compact[data-state='running'] :deep(.n-collapse-item__header) {
  position: relative;
}
.reasoning-compact[data-state='running'] :deep(.n-collapse-item__header)::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -200px;
  width: 200px;
  background: linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--noesis-color-bg) 50%, transparent) 50%, transparent 100%);
  animation: reasoning-row-sweep 2.6s ease-out infinite;
  pointer-events: none;
}
@keyframes reasoning-row-sweep {
  0% { left: -200px; }
  100% { left: 100%; }
}

.reasoning-body-compact {
  border: 1px solid var(--noesis-color-border);
  border-radius: 8px;
  background: var(--noesis-color-bg-elevated);
  padding: 8px 10px;
  margin: 4px 0 6px;
}
.reasoning-body-compact pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  color: var(--noesis-color-text);
}
</style>
