<script setup lang="ts">
import type { SearchResult } from '@/api/knowledgeBase'
import { ChevronDown, ChevronForward } from '@vicons/ionicons-v5'
import { NButton, NEmpty, NIcon, NTag } from 'naive-ui'
import { computed, ref } from 'vue'

const props = defineProps<{
  results: SearchResult[]
  loading?: boolean
}>()

const emit = defineEmits<{
  viewShard: [shardId: string]
}>()

const expandedFiles = ref<Record<string, boolean>>({})
const expandedHits = ref<Record<string, boolean>>({})

function hitKey(item: SearchResult, idx: number) {
  return `${item.id}-${idx}`
}

function isHitExpanded(item: SearchResult, idx: number) {
  return expandedHits.value[hitKey(item, idx)] === true
}

function toggleHit(item: SearchResult, idx: number) {
  const key = hitKey(item, idx)
  expandedHits.value[key] = !isHitExpanded(item, idx)
}

const grouped = computed(() => {
  const map = new Map<string, SearchResult[]>()
  for (const r of props.results) {
    const key = r.file_name || '（未知文档）'
    if (!map.has(key)) {
      map.set(key, [])
    }
    map.get(key)!.push(r)
  }
  return Array.from(map.entries()).map(([fileName, items]) => ({
    fileName,
    items,
  }))
})

function isExpanded(fileName: string) {
  return expandedFiles.value[fileName] !== false
}

function toggle(fileName: string) {
  expandedFiles.value[fileName] = !isExpanded(fileName)
}

function formatScore(result: SearchResult) {
  if (result.search_mode === 'vector' || !result.search_mode) {
    return `${(result.score * 100).toFixed(1)}%`
  }
  return result.score.toFixed(3)
}

function modeLabel(mode?: string) {
  if (mode === 'bm25') {
    return '关键词'
  }
  if (mode === 'hybrid') {
    return '混合'
  }
  return '语义'
}

function scoreBreakdown(result: SearchResult): string {
  const parts: string[] = []
  if (result.recall_score != null) {
    parts.push(`召回 ${result.recall_score.toFixed(3)}`)
  }
  if (result.rerank_score != null) {
    parts.push(`精排 ${result.rerank_score.toFixed(3)}`)
  }
  return parts.join(' · ')
}
</script>

<template>
  <div class="kb-search-results">
    <div v-if="loading" class="results-loading">
      检索中…
    </div>
    <n-empty v-else-if="results.length === 0" description="输入问题后点击检索，验证召回与精排效果" size="small" />
    <template v-else>
      <div class="results-meta">
        共 {{ results.length }} 条命中 · {{ grouped.length }} 个文档
      </div>
      <div
        v-for="group in grouped"
        :key="group.fileName"
        class="file-group"
      >
        <button type="button" class="file-group-header" @click="toggle(group.fileName)">
          <n-icon size="16">
            <ChevronDown v-if="isExpanded(group.fileName)" />
            <ChevronForward v-else />
          </n-icon>
          <span class="file-name">{{ group.fileName }}</span>
          <n-tag size="small" :bordered="false">
            {{ group.items.length }}
          </n-tag>
        </button>
        <div v-show="isExpanded(group.fileName)" class="file-group-body">
          <div
            v-for="(item, idx) in group.items"
            :key="`${item.id}-${idx}`"
            class="hit-card"
          >
            <div class="hit-meta">
              <n-tag size="small" type="info" :bordered="false">
                {{ modeLabel(item.search_mode) }}
              </n-tag>
              <span class="hit-score">{{ formatScore(item) }}</span>
              <span v-if="scoreBreakdown(item)" class="hit-breakdown">{{ scoreBreakdown(item) }}</span>
              <n-button size="tiny" quaternary class="hit-detail-btn" @click="emit('viewShard', item.id)">
                分片详情
              </n-button>
            </div>
            <p v-if="item.header_path" class="hit-path">
              {{ item.header_path }}
            </p>
            <p
              class="hit-content"
              :class="{ expanded: isHitExpanded(item, idx) }"
            >
              {{ item.content }}
            </p>
            <n-button
              v-if="item.content.length > 180"
              size="tiny"
              quaternary
              @click="toggleHit(item, idx)"
            >
              {{ isHitExpanded(item, idx) ? '收起' : '展开全文' }}
            </n-button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.kb-search-results {
  min-height: 120px;
}

.results-loading {
  padding: 24px;
  text-align: center;
  color: var(--noesis-color-text-muted);
  font-size: 14px;
}

.results-meta {
  font-size: 12px;
  color: var(--noesis-color-text-muted);
  margin-bottom: 12px;
}

.file-group {
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-sm);
  margin-bottom: 10px;
  background: var(--noesis-color-bg-elevated);
}

.file-group-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: none;
  background: var(--noesis-color-bg-muted);
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  border-radius: var(--noesis-radius-sm) var(--noesis-radius-sm) 0 0;
}

.file-group-header:hover {
  background: var(--noesis-color-bg-hover);
}

.file-name {
  flex: 1;
  font-weight: 500;
  color: var(--noesis-color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-group-body {
  padding: 8px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.hit-card {
  padding: 10px 12px;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border);
}

.hit-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.hit-score {
  font-size: 12px;
  color: var(--noesis-color-primary);
  font-weight: 600;
}

.hit-breakdown {
  font-size: 11px;
  color: var(--noesis-color-text-muted);
}

.hit-detail-btn {
  margin-left: auto;
}

.hit-path {
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--noesis-color-text-muted);
}

.hit-content {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--noesis-color-text);
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.hit-content.expanded {
  display: block;
  -webkit-line-clamp: unset;
  overflow: visible;
}
</style>
