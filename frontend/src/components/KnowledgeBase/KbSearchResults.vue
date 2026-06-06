<script setup lang="ts">
import type { SearchResult } from '@/api/knowledgeBase'
import { ChevronDown, ChevronForward } from '@vicons/ionicons-v5'
import { NEmpty, NIcon, NTag } from 'naive-ui'
import { computed, ref } from 'vue'

const props = defineProps<{
  results: SearchResult[]
  loading?: boolean
}>()

const expandedFiles = ref<Record<string, boolean>>({})

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
</script>

<template>
  <div class="kb-search-results">
    <div v-if="loading" class="results-loading">
      检索中…
    </div>
    <n-empty v-else-if="results.length === 0" description="输入问题后点击检索" size="small" />
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
            </div>
            <p v-if="item.header_path" class="hit-path">
              {{ item.header_path }}
            </p>
            <p class="hit-content">{{ item.content }}</p>
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
  color: #8c8c8c;
  font-size: 14px;
}

.results-meta {
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 12px;
}

.file-group {
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  margin-bottom: 10px;
  overflow: hidden;
  background: #fff;
}

.file-group-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: none;
  background: #fafafa;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
}

.file-group-header:hover {
  background: #f5f5f5;
}

.file-name {
  flex: 1;
  font-weight: 500;
  color: #262626;
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
  border-radius: 6px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
}

.hit-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.hit-score {
  font-size: 12px;
  color: #1890ff;
  font-weight: 500;
}

.hit-path {
  margin: 0 0 6px;
  font-size: 12px;
  color: #8c8c8c;
}

.hit-content {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: #434343;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
