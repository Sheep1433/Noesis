<script setup lang="ts">
import type { SearchResult, ShardDetail } from '@/api/knowledgeBase'
import { CopyOutline } from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NIcon,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
} from 'naive-ui'
import { computed } from 'vue'
import { copyToClipboard } from '@/utils/copy'
import {
  chunkElementLabel,
  formatChunkLocator,
  formatKbDate,
} from '@/utils/kbFormat'

const props = defineProps<{
  detail: ShardDetail | null
  loading?: boolean
  error?: string | null
  searchContext?: SearchResult | null
}>()

const message = useMessage()

const titlePath = computed(() => {
  const detail = props.detail
  if (!detail) {
    return ''
  }
  return detail.header_path
    || [detail.Header_1, detail.Header_2, detail.Header_3, detail.Header_4].filter(Boolean).join(' / ')
})

const metadataJson = computed(() => {
  const detail = props.detail
  if (!detail) {
    return '{}'
  }
  const metadata: Record<string, unknown> = {
    point_id: detail.id,
    chunk_index: detail.chunk_index,
    element_type: detail.element_type,
    char_length: detail.char_length,
    token_count: detail.token_count,
    file_name: detail.file_name,
    source: detail.source,
    locator: detail.locator,
    header_path: detail.header_path,
    file_hash: detail.file_hash,
    content_hash: detail.content_hash,
    document_id: detail.document_id,
    document_version_id: detail.document_version_id,
    segment_id: detail.segment_id,
    vector_dimension: detail.vector_dimension,
    created_at: detail.created_at,
    effective_processing_params: detail.effective_processing_params,
    raw_metadata: detail.raw_metadata,
  }
  const visibleMetadata = Object.fromEntries(
    Object.entries(metadata).filter(([, value]) => {
      if (value === null || value === undefined || value === '') {
        return false
      }
      return typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length > 0
    }),
  )
  return JSON.stringify(visibleMetadata, null, 2)
})

async function copyValue(value: string | null | undefined, label: string) {
  if (!value) {
    return
  }
  try {
    await copyToClipboard(value)
    message.success(`${label}已复制`)
  } catch {
    message.error('复制失败')
  }
}

function score(value: number | null | undefined) {
  return value == null ? '—' : value.toFixed(3)
}
</script>

<template>
  <div class="chunk-detail-panel">
    <div v-if="loading" class="detail-state">
      <n-spin size="large" />
    </div>
    <n-alert v-else-if="error" type="error" :title="error" />
    <n-empty v-else-if="!detail" description="选择一个分块查看详情" />
    <template v-else>
      <header class="detail-heading">
        <div class="detail-heading-main">
          <div class="detail-kicker">分块 #{{ detail.chunk_index ?? '—' }}</div>
          <h3>{{ titlePath || '未命名章节' }}</h3>
          <div class="detail-tags">
            <n-tag size="small" :bordered="false">{{ chunkElementLabel(detail.element_type) }}</n-tag>
            <n-tag size="small" :bordered="false">{{ formatChunkLocator(detail.locator) }}</n-tag>
            <n-tag size="small" :bordered="false">{{ detail.char_length.toLocaleString() }} 字</n-tag>
            <n-tag v-if="detail.token_count !== null && detail.token_count !== undefined" size="small" :bordered="false">
              {{ detail.token_count.toLocaleString() }} tokens
            </n-tag>
          </div>
        </div>
        <n-button quaternary size="small" @click="copyValue(detail.content, '正文')">
          <template #icon><n-icon><CopyOutline /></n-icon></template>
          复制正文
        </n-button>
      </header>

      <section v-if="searchContext" class="score-strip">
        <span>本次检索</span>
        <strong>最终 {{ score(searchContext.score) }}</strong>
        <span>召回 {{ score(searchContext.recall_score) }}</span>
        <span>精排 {{ score(searchContext.rerank_score) }}</span>
      </section>

      <section class="detail-section">
        <h4>章节内容</h4>
        <div class="chapter-path">{{ titlePath || '未提供章节路径' }}</div>
      </section>

      <section class="detail-section">
        <h4>来源结构</h4>
        <n-descriptions :column="2" size="small" label-placement="top">
          <n-descriptions-item label="文件">{{ detail.file_name || '—' }}</n-descriptions-item>
          <n-descriptions-item label="来源">{{ detail.source || '—' }}</n-descriptions-item>
          <n-descriptions-item label="来源位置">{{ formatChunkLocator(detail.locator) }}</n-descriptions-item>
          <n-descriptions-item label="入库时间">{{ formatKbDate(detail.created_at) }}</n-descriptions-item>
          <n-descriptions-item label="向量维度">{{ detail.vector_dimension || '—' }}</n-descriptions-item>
          <n-descriptions-item label="内容类型">{{ chunkElementLabel(detail.element_type) }}</n-descriptions-item>
        </n-descriptions>
      </section>

      <section class="detail-section content-section">
        <h4>原文</h4>
        <n-tabs type="line" size="small">
          <n-tab-pane name="content" tab="入库正文">
            <pre>{{ detail.content }}</pre>
          </n-tab-pane>
          <n-tab-pane v-if="detail.clean_text && detail.clean_text !== detail.content" name="clean" tab="清洗文本">
            <pre>{{ detail.clean_text }}</pre>
          </n-tab-pane>
          <n-tab-pane v-if="detail.raw_text && detail.raw_text !== detail.content" name="raw" tab="原始文本">
            <pre>{{ detail.raw_text }}</pre>
          </n-tab-pane>
        </n-tabs>
      </section>

      <section class="detail-section metadata-section">
        <div class="section-heading">
          <h4>元数据</h4>
          <n-button quaternary size="small" @click="copyValue(metadataJson, '元数据')">复制 JSON</n-button>
        </div>
        <pre>{{ metadataJson }}</pre>
      </section>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.chunk-detail-panel {
  min-width: 0;
  height: 100%;
  overflow-y: auto;
  padding: 20px;
  box-sizing: border-box;
}

.detail-state {
  min-height: 240px;
  display: grid;
  place-items: center;
}

.detail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
}

.detail-heading-main {
  min-width: 0;
}

.detail-kicker {
  color: var(--noesis-color-primary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.detail-heading h3 {
  margin: 4px 0 10px;
  font-size: 18px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.score-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 16px;
  padding: 10px 12px;
  background: var(--noesis-color-primary-bg-subtle);
  border-radius: var(--noesis-radius-sm);
  font-size: 12px;
}

.detail-section {
  min-width: 0;
  overflow: hidden;
  padding: 18px 0;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
}

.detail-section h4 {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 700;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chapter-path {
  padding: 12px;
  border-left: 3px solid var(--noesis-color-primary);
  background: var(--noesis-color-bg-muted);
  color: var(--noesis-color-text-body);
  font-size: 14px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.content-section pre,
.metadata-section pre {
  margin: 0;
  padding: 14px;
  overflow: auto;
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-sm);
  color: var(--noesis-color-text-body);
  font-family: ui-monospace, 'SF Mono', Monaco, monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: $bp-md) {

  .chunk-detail-panel {
    padding: 14px;
  }

  .detail-heading {
    flex-direction: column;
  }
}
</style>
