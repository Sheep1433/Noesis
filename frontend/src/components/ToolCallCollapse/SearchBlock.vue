<script setup lang="ts">
import type { RetrievalUiPart } from '@/views/chat/messageParts'
import { computed } from 'vue'

interface Props {
  /** 工具名（决定如何解析 output） */
  name: string
  /** 工具输出（纯文本或 JSON 字符串） */
  output: string
  /** 工具参数（用于判断 grep output_mode 等） */
  input?: unknown
  truncated?: boolean
  appearance?: 'dark' | 'light'
  /**
   * 同 tool_call_id 的检索结果 part：结构化结果的优先来源
   *  （主/子会话同构——tool part 只存摘要；无 part 的旧数据回退解析 output）
   */
  retrievalPart?: RetrievalUiPart | null
}

const props = withDefaults(defineProps<Props>(), {
  input: undefined,
  truncated: undefined,
  appearance: 'dark',
  retrievalPart: null,
})

/** 解析 grep 的 content 模式：按文件分组。 */
interface GrepFileGroup {
  path: string
  lines: { no: string, text: string }[]
}
function parseGrepContent(text: string): GrepFileGroup[] {
  const groups: GrepFileGroup[] = []
  let cur: GrepFileGroup | null = null
  for (const line of text.split('\n')) {
    const head = line.match(/^\/.+:$/)
    if (head) {
      cur = { path: line.slice(0, -1), lines: [] }
      groups.push(cur)
      continue
    }
    const m = line.match(/^\s+(\d+): (.*)$/)
    if (m && cur) {
      cur.lines.push({ no: m[1], text: m[2] })
    }
  }
  return groups
}

/** 从 Python list repr 或 JSON 数组提取路径列表。 */
function parsePathList(text: string): string[] {
  const trimmed = text.trim()
  // JSON 数组优先
  try {
    const j = JSON.parse(trimmed)
    if (Array.isArray(j)) {
      return j.filter((x): x is string => typeof x === 'string')
    }
  } catch {
    // 不是 JSON，可能是 Python list repr（单引号）
  }
  // Python list repr：粗略正则提取引号内字符串
  const matches = trimmed.match(/['"]([^'"]+)['"]/g)
  if (matches) {
    return matches.map((s) => s.slice(1, -1))
  }
  // 回退：按行
  return trimmed.split('\n').map((s) => s.trim()).filter(Boolean)
}

/** 解析 web_search / search_knowledge_base 的 JSON 输出。 */
interface ResultItem {
  title?: string
  url?: string
  excerpt?: string
  file_name?: string
  collection_name?: string
  score?: number
}
function parseJsonResults(text: string): { items: ResultItem[], total?: number } | null {
  try {
    const j = JSON.parse(text)
    if (j && typeof j === 'object') {
      const results = (j as Record<string, unknown>).results
      if (Array.isArray(results)) {
        return { items: results as ResultItem[], total: typeof j.total_results === 'number' ? j.total_results : undefined }
      }
    }
  } catch {
    // 非 JSON
  }
  return null
}

/** 解析 web_fetch 旧格式原始 JSON 输出（{url, content}）。 */
function parseJsonFetch(text: string): { url: string, content: string } | null {
  try {
    const j = JSON.parse(text)
    if (j && typeof j === 'object' && typeof (j as Record<string, unknown>).url === 'string') {
      const rec = j as Record<string, unknown>
      return { url: rec.url, content: String(rec.content ?? '') }
    }
  } catch {
    // 非 JSON
  }
  return null
}

/** chat 行内最多展示 8 项，超出折叠（详情面才看全部）。 */
const MAX_ROWS = 8

/** web_fetch 正文展示上限：抓取内容可能很长，行内给预览即可。 */
const FETCH_CONTENT_MAX = 6_000

const view = computed(() => {
  const out = props.output || ''
  const name = props.name

  // web_fetch：抓取内容（URL + 正文）——输出已被替换为「检索到 N 条来源」
  // 摘要，内容在 retrieval part 的单条结果里；旧数据的原始 JSON 输出回退解析
  if (name === 'web_fetch') {
    const fetched = props.retrievalPart?.results[0]
    if (fetched) {
      return { kind: 'fetch' as const, url: fetched.url || '', content: fetched.excerpt || fetched.title || '' }
    }
    const legacy = parseJsonFetch(out)
    if (legacy) {
      return { kind: 'fetch' as const, ...legacy }
    }
  }

  // retrieval part 的结构化结果优先（主/子会话统一数据来源）
  if (props.retrievalPart && props.retrievalPart.results.length > 0) {
    return {
      kind: 'results' as const,
      items: props.retrievalPart.results as unknown as ResultItem[],
      total: props.retrievalPart.results.length,
    }
  }

  // JSON 结构化结果（web_search / search_knowledge_base / search_memory）
  const json = parseJsonResults(out)
  if (json) {
    return { kind: 'results' as const, ...json }
  }

  // grep content 模式
  const inputObj = (typeof props.input === 'object' && props.input !== null) ? props.input as Record<string, unknown> : undefined
  const mode = typeof inputObj?.output_mode === 'string' ? inputObj.output_mode : undefined
  if (name === 'grep' && mode === 'content') {
    const groups = parseGrepContent(out)
    if (groups.length) {
      return { kind: 'grepContent' as const, groups }
    }
  }

  // 路径列表（grep files_with_matches / glob / grep count）
  if (name === 'grep' || name === 'glob' || name === 'grep_attachment') {
    if (mode === 'count') {
      // count: 每行 "/path: N"
      const rows = out.split('\n').map((l) => {
        const m = l.match(/^(.+):\s*(\d+)\s*$/)
        return m ? { path: m[1], count: Number(m[2]) } : null
      }).filter((x): x is { path: string, count: number } => x !== null)
      if (rows.length) {
        return { kind: 'count' as const, rows }
      }
    }
    const paths = parsePathList(out)
    if (paths.length) {
      return { kind: 'paths' as const, paths }
    }
  }

  // 回退：纯文本
  return { kind: 'text' as const, text: out }
})

/** fetch 正文截断展示（原文在 retrieval part / 原始输出中，未丢失）。 */
const fetchTruncated = computed(() =>
  view.value.kind === 'fetch' && view.value.content.length > FETCH_CONTENT_MAX,
)
const fetchDisplay = computed(() =>
  view.value.kind === 'fetch' ? view.value.content.slice(0, FETCH_CONTENT_MAX) : '',
)
</script>

<template>
  <div class="search-block" :data-appearance="appearance">
    <!-- 结构化检索结果 -->
    <template v-if="view.kind === 'results'">
      <div v-if="view.total !== undefined" class="result-meta">共 {{ view.total }} 条结果</div>
      <div v-for="(item, i) in view.items.slice(0, MAX_ROWS)" :key="i" class="result-item">
        <div class="result-item__head">
          <span v-if="item.collection_name" class="result-item__src">{{ item.collection_name }}</span>
          <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="result-item__title">{{ item.title || item.url }}</a>
          <span v-else class="result-item__title">{{ item.file_name || item.title || `结果 ${i + 1}` }}</span>
          <span v-if="item.score !== undefined" class="result-item__score">{{ item.score.toFixed(2) }}</span>
        </div>
        <div v-if="item.excerpt" class="result-item__excerpt">{{ item.excerpt }}</div>
      </div>
      <div v-if="view.items.length > MAX_ROWS" class="capped-hint">仅展示前 {{ MAX_ROWS }} 条，共 {{ view.items.length }} 条</div>
    </template>

    <!-- grep content 分组 -->
    <template v-else-if="view.kind === 'grepContent'">
      <template v-for="(g, i) in view.groups.slice(0, MAX_ROWS)" :key="i">
        <div class="grep-group">
          <div class="grep-group__path">{{ g.path }}</div>
          <div v-for="(ln, j) in g.lines" :key="j" class="grep-group__line">
            <span class="grep-group__no">{{ ln.no }}</span>
            <span class="grep-group__text">{{ ln.text }}</span>
          </div>
        </div>
      </template>
      <div v-if="view.groups.length > MAX_ROWS" class="capped-hint">仅展示前 {{ MAX_ROWS }} 个文件，共 {{ view.groups.length }} 个</div>
    </template>

    <!-- count -->
    <template v-else-if="view.kind === 'count'">
      <div v-for="(r, i) in view.rows.slice(0, MAX_ROWS)" :key="i" class="count-row">
        <span class="count-row__path">{{ r.path }}</span>
        <span class="count-row__num">{{ r.count }}</span>
      </div>
      <div v-if="view.rows.length > MAX_ROWS" class="capped-hint">仅展示前 {{ MAX_ROWS }} 个文件，共 {{ view.rows.length }} 个</div>
    </template>

    <!-- 路径列表 -->
    <template v-else-if="view.kind === 'paths'">
      <div v-for="(p, i) in view.paths.slice(0, MAX_ROWS)" :key="i" class="path-row">{{ p }}</div>
      <div v-if="view.paths.length > MAX_ROWS" class="capped-hint">仅展示前 {{ MAX_ROWS }} 条，共 {{ view.paths.length }} 条</div>
    </template>

    <!-- web_fetch：抓取内容（URL + 正文预览） -->
    <template v-else-if="view.kind === 'fetch'">
      <a v-if="view.url" :href="view.url" target="_blank" rel="noopener" class="fetch-url">{{ view.url }}</a>
      <pre v-if="view.content" class="fetch-content">{{ fetchDisplay }}</pre>
      <div v-if="fetchTruncated" class="capped-hint">正文较长，仅展示前 {{ FETCH_CONTENT_MAX }} 字符</div>
    </template>

    <!-- 回退纯文本 -->
    <pre v-else class="search-text">{{ view.text }}</pre>

    <div v-if="truncated" class="trunc-hint">输出较长，已截断展示</div>
  </div>
</template>

<style scoped>
.search-block {
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
}
.search-block[data-appearance='dark'] {
  background: var(--noesis-block-dark-bg, #0d1117);
  color: var(--noesis-block-dark-text, #c9d1d9);
  --search-summary-color: #8b949e;
}
.search-block[data-appearance='light'] {
  background: var(--noesis-color-bg-elevated, #f6f8fa);
  color: var(--noesis-color-text, #24292f);
  --search-summary-color: var(--noesis-color-text-secondary, #404040);
}
.result-meta {
  color: var(--search-summary-color, #404040);
  margin-bottom: 6px;
}
.result-item {
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.result-item:last-child {
  border-bottom: none;
}
.result-item__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.result-item__src {
  color: var(--search-summary-color, #404040);
  font-size: 11px;
}
.result-item__title {
  font-weight: 500;
}
a.result-item__title {
  color: var(--noesis-color-text, #24292f);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.fetch-url {
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
  color: var(--noesis-color-text, #24292f);
  text-decoration: underline;
  text-underline-offset: 2px;
  word-break: break-all;
}
.fetch-content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  max-height: 320px;
  overflow-y: auto;
}
.result-item__score {
  margin-left: auto;
  color: var(--search-summary-color, #404040);
  font-size: 11px;
}
.result-item__excerpt {
  margin-top: 2px;
  color: var(--search-summary-color, #404040);
  white-space: pre-wrap;
  word-break: break-all;
}
.grep-group {
  margin-bottom: 8px;
}
.grep-group__path {
  color: var(--noesis-color-text, #24292f);
  font-weight: 500;
  margin-bottom: 2px;
}
.grep-group__line {
  display: flex;
  gap: 8px;
}
.grep-group__no {
  color: var(--search-summary-color, #404040);
  min-width: 3em;
}
.count-row {
  display: flex;
  justify-content: space-between;
}
.count-row__num {
  color: var(--search-summary-color, #404040);
}
.path-row {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.search-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: var(--noesis-mono, ui-monospace, monospace);
}
.trunc-hint {
  margin-top: 6px;
  color: var(--search-summary-color, #404040);
  font-size: 11px;
}
.capped-hint {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--noesis-color-border, rgba(255, 255, 255, 0.06));
  color: var(--search-summary-color, #404040);
  font-size: 11px;
  text-align: center;
}
</style>
