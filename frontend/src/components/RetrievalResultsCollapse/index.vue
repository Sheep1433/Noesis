<script setup lang="ts">
import type { RetrievalResultUi } from '@/views/chat/messageParts'
import { SearchOutline } from '@vicons/ionicons-v5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { collapseCompactStyle } from '@/utils/collapseCompact'

defineProps<{
  results: RetrievalResultUi[]
}>()
</script>

<template>
  <n-collapse class="retrieval-results" :style="collapseCompactStyle">
    <n-collapse-item name="retrieval-results">
      <template #header>
        <div class="retrieval-results__header">
          <div class="retrieval-results__icon">
            <n-icon :size="14"><SearchOutline /></n-icon>
          </div>
          <span class="retrieval-results__title">本轮检索结果</span>
          <n-tag size="small" round bordered>{{ results.length }} 条</n-tag>
        </div>
      </template>

      <div class="retrieval-results__content">
        <article v-for="result in results" :key="result.evidence_id" class="retrieval-result">
          <a
            v-if="result.url"
            class="retrieval-result__title"
            :href="result.url"
            target="_blank"
            rel="noopener noreferrer"
          >{{ result.title || '网页来源' }}</a>
          <div v-else class="retrieval-result__title">{{ result.title || '知识库文档' }}</div>
          <p v-if="result.excerpt" class="retrieval-result__excerpt">{{ result.excerpt }}</p>
        </article>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
.retrieval-results {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  margin: 5px 0;
  background: var(--noesis-block-light-bg);
  border: 1px solid var(--noesis-block-light-border);
  border-left: 3px solid var(--noesis-block-light-accent);
  border-radius: var(--noesis-radius-md);
  box-shadow: var(--noesis-shadow-sm);
}

.retrieval-results :deep(.n-collapse-item) {
  margin: 0 !important;
}

.retrieval-results :deep(.n-collapse-item__header) {
  min-height: 0;
  padding: 0 6px 0 0 !important;
}

.retrieval-results :deep(.n-collapse-item-arrow) {
  flex-shrink: 0;
  margin-right: 4px !important;
  font-size: 14px !important;
}

.retrieval-results :deep(.n-collapse-item__content-inner) {
  padding-top: 0 !important;
}

.retrieval-results :deep(.n-collapse-item__content-wrapper) {
  border-top: 1px solid var(--noesis-block-light-divider);
}

.retrieval-results__header {
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 7px 10px 7px 8px;
  color: var(--noesis-block-light-text);
  font-size: 12px;
  line-height: 1.3;
  cursor: pointer;
}

.retrieval-results__icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  color: var(--noesis-block-light-icon);
  background: var(--noesis-color-primary-bg-icon);
  border-radius: 7px;
}

.retrieval-results__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--noesis-block-light-text-name);
  font-weight: 600;
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.retrieval-results__content {
  display: grid;
  gap: 8px;
  padding: 8px 10px 10px;
}

.retrieval-result {
  padding: 8px 10px;
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-code);
  border-radius: 7px;
}

.retrieval-result__title {
  color: var(--noesis-block-light-text-name);
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
}

a.retrieval-result__title:hover {
  color: var(--noesis-color-primary);
  text-decoration: underline;
}

.retrieval-result__excerpt {
  display: -webkit-box;
  margin: 4px 0 0;
  overflow: hidden;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
</style>
