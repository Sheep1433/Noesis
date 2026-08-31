<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  /** execute 工具的 combined stdout/stderr（含 `[Command ... with exit code N]` 后缀） */
  output: string
  /** 命令行原文（compact 展开态显示在输出上方） */
  command?: string
  /** 独立传入的退出码（若可用） */
  exitCode?: number
  truncated?: boolean
  /** dark=独立深色块；light=嵌入气泡浅色 */
  appearance?: 'dark' | 'light'
}

const props = withDefaults(defineProps<Props>(), {
  exitCode: undefined,
  truncated: undefined,
  appearance: 'dark',
})

/** 从 output 末尾解析出 exit code 行与正文。 */
const parsed = computed(() => {
  const raw = props.output || ''
  const m = raw.match(/\n*\[Command (succeeded|failed) with exit code (-?\d+)\]\s*$/)
  if (m) {
    const body = raw.slice(0, m.index).replace(/\n+$/, '')
    return { body, exitCode: Number(m[2]), ok: m[1] === 'succeeded', tail: m[0] }
  }
  return { body: raw, exitCode: props.exitCode, ok: props.exitCode === undefined ? null : props.exitCode === 0, tail: '' }
})

const effectiveExitCode = computed(() => parsed.value.exitCode ?? props.exitCode)
const succeeded = computed(() => parsed.value.ok ?? (effectiveExitCode.value === 0))
const hasExitInfo = computed(() => effectiveExitCode.value !== undefined || parsed.value.tail !== '')
</script>

<template>
  <div class="terminal-block" :data-appearance="appearance" :data-state="hasExitInfo ? (succeeded ? 'ok' : 'error') : 'neutral'">
    <div v-if="command" class="terminal-command"><span class="terminal-command__prompt" aria-hidden="true">$</span>{{ command }}</div>
    <pre v-if="parsed.body" class="terminal-body">{{ parsed.body }}</pre>
    <div v-if="hasExitInfo || truncated" class="terminal-meta">
      <span v-if="hasExitInfo" class="exit-badge" :data-ok="succeeded">
        {{ succeeded ? '✓ exit 0' : `✗ exit ${effectiveExitCode}` }}
      </span>
      <span v-if="truncated" class="trunc-badge">已截断</span>
    </div>
  </div>
</template>

<style scoped>
.terminal-block {
  border-radius: 6px;
  padding: 8px 10px;
  font-family: var(--noesis-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
}
.terminal-block[data-appearance='dark'] {
  background: var(--noesis-block-dark-bg, #0d1117);
  color: var(--noesis-block-dark-text, #c9d1d9);
  --terminal-muted-color: #8b949e;
}
.terminal-block[data-appearance='light'] {
  background: var(--noesis-color-bg-elevated, #f6f8fa);
  color: var(--noesis-color-text, #24292f);
  --terminal-muted-color: var(--noesis-color-text-secondary, #404040);
}
.terminal-command {
  margin-bottom: 6px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--terminal-muted-color, #8b949e);
  opacity: 0.33;
  font-family: inherit;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 11px;
}
.terminal-command__prompt {
  margin-right: 6px;
}
.terminal-block:hover .terminal-command {
  opacity: 1;
}
.terminal-body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: inherit;
}
.terminal-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
  font-size: 11px;
}
.exit-badge {
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 500;
}
.exit-badge[data-ok='true'] {
  background: rgba(46, 160, 67, 0.15);
  color: #3fb950;
}
.exit-badge[data-ok='false'] {
  background: rgba(248, 81, 73, 0.15);
  color: #f85149;
}
.trunc-badge {
  color: var(--terminal-muted-color, #8b949e);
}
</style>
