<script setup lang="ts">
import type { ShardInfo } from '@/api/knowledgeBase'
import { Copy, EyeOutline } from '@vicons/ionicons-v5'
import { NAlert, NButton, NDrawer, NDrawerContent, NEmpty, NIcon, NPagination, NSpin, NTooltip, useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { getDocumentShards } from '@/api/knowledgeBase'
import { formatKbDate } from '@/utils/kbFormat'

const props = defineProps<{
  show: boolean
  collectionName: string
  fileName: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  'viewShard': [shardId: string]
}>()

const message = useMessage()
const loading = ref(false)
const error = ref<string | null>(null)
const allShards = ref<ShardInfo[]>([])

const page = ref(1)
const pageSize = ref(20)

const { width: windowWidth } = useWindowSize()

const drawerWidth = computed(() => {
  const w = windowWidth.value
  if (w <= 480) {
    return w
  }
  if (w <= 768) {
    return Math.min(w - 24, 640)
  }
  return Math.min(w - 48, 1100)
})

function sortShardsByChunkIndex(shards: ShardInfo[]): ShardInfo[] {
  return [...shards].sort((a, b) => {
    const ai = a.chunk_index ?? Number.MAX_SAFE_INTEGER
    const bi = b.chunk_index ?? Number.MAX_SAFE_INTEGER
    return ai - bi
  })
}

const paginatedShards = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const end = start + pageSize.value
  return allShards.value.slice(start, end)
})

const totalPages = computed(() => Math.ceil(allShards.value.length / pageSize.value))

const totalChars = computed(() =>
  allShards.value.reduce((sum, shard) => sum + (shard.char_length ?? 0), 0),
)

const pageRangeLabel = computed(() => {
  if (allShards.value.length === 0) {
    return ''
  }
  const start = (page.value - 1) * pageSize.value + 1
  const end = Math.min(page.value * pageSize.value, allShards.value.length)
  return `第 ${start}–${end} 条，共 ${allShards.value.length} 个分片`
})

watch(
  () => [props.show, props.collectionName, props.fileName],
  async ([newShow, newCollection, newFile]) => {
    if (newShow && newCollection && newFile) {
      page.value = 1
      await loadShards()
    }
  },
)

async function loadShards() {
  loading.value = true
  error.value = null

  try {
    const shards = await getDocumentShards(props.collectionName, props.fileName)
    allShards.value = sortShardsByChunkIndex(shards)
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function shardIndex(shard: ShardInfo, index: number) {
  return shard.chunk_index ?? (page.value - 1) * pageSize.value + index + 1
}

async function copyContent(shard: ShardInfo) {
  try {
    await navigator.clipboard.writeText(shard.content)
    message.success('复制成功')
  } catch {
    message.error('复制失败')
  }
}

function handlePageChange(newPage: number) {
  page.value = newPage
}
</script>

<template>
  <n-drawer
    :show="show"
    :width="drawerWidth"
    placement="right"
    :trap-focus="false"
    :block-scroll="true"
    @update:show="(val) => emit('update:show', val)"
  >
    <n-drawer-content :title="`分片预览 · ${fileName}`" closable class="shard-drawer-content">
      <div v-if="loading" class="loading">
        <n-spin size="large" />
      </div>

      <div v-else-if="error" class="error">
        <n-alert type="error" :title="error" />
      </div>

      <div v-else-if="allShards.length > 0" class="shards-container">
        <header class="shards-toolbar">
          <div class="shards-stats">
            <span class="stat-pill">{{ allShards.length }} 个分片</span>
            <span class="stat-pill">{{ totalChars.toLocaleString() }} 字</span>
          </div>
          <span v-if="totalPages > 1" class="shards-range">{{ pageRangeLabel }}</span>
        </header>

        <div class="shards-grid">
          <article
            v-for="(shard, index) in paginatedShards"
            :key="shard.id"
            class="shard-card"
          >
            <header class="shard-header">
              <div class="shard-meta">
                <span class="shard-badge">#{{ shardIndex(shard, index) }}</span>
                <span
                  v-if="shard.header_path"
                  class="shard-path"
                  :title="shard.header_path"
                >
                  {{ shard.header_path }}
                </span>
                <span class="shard-length">{{ shard.char_length }} 字</span>
              </div>
              <div class="shard-actions">
                <n-tooltip trigger="hover" placement="top">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle @click="emit('viewShard', shard.id)">
                      <template #icon>
                        <n-icon><EyeOutline /></n-icon>
                      </template>
                    </n-button>
                  </template>
                  查看详情
                </n-tooltip>
                <n-tooltip trigger="hover" placement="top">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle @click="copyContent(shard)">
                      <template #icon>
                        <n-icon><Copy /></n-icon>
                      </template>
                    </n-button>
                  </template>
                  复制内容
                </n-tooltip>
              </div>
            </header>

            <div class="shard-content">{{ shard.content }}</div>

            <footer class="shard-footer">
              {{ formatKbDate(shard.created_at) }}
            </footer>
          </article>
        </div>

        <footer v-if="totalPages > 1" class="pagination">
          <n-pagination
            v-model:page="page"
            :page-count="totalPages"
            :page-slot="5"
            size="small"
            @update:page="handlePageChange"
          />
        </footer>
      </div>

      <n-empty v-else description="暂无分片" />
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.shard-drawer-content :deep(.n-drawer-body-content-wrapper) {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.loading {
  display: flex;
  justify-content: center;
  padding: 40px;
}

.error {
  padding: 16px;
}

.shards-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  gap: 12px;
}

.shards-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  flex-shrink: 0;
  padding-bottom: 12px;
  border-bottom: 1px dashed var(--noesis-color-border-subtle);
}

.shards-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.stat-pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  font-size: 12px;
  color: var(--noesis-color-text-secondary);
  background: var(--noesis-color-bg-subtle);
  border: 1px solid var(--noesis-color-border-light);
  border-radius: var(--noesis-radius-pill);
}

.shards-range {
  font-size: 12px;
  color: var(--noesis-color-text-muted);
}

.shards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr));
  gap: 12px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 2px;
  align-content: start;
}

.shard-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-light);
  border-radius: var(--noesis-radius-md);
  padding: 12px;
  min-height: 160px;
  max-height: 300px;
  overflow: hidden;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.shard-card:hover {
  border-color: var(--noesis-color-border);
  background: var(--noesis-color-bg-hover);
}

.shard-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  flex-shrink: 0;
}

.shard-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.shard-badge {
  flex-shrink: 0;
  font-family: ui-monospace, 'SF Mono', Monaco, monospace;
  font-size: 11px;
  font-weight: 600;
  color: var(--noesis-color-text);
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-sm);
  padding: 1px 6px;
  line-height: 1.5;
}

.shard-path {
  flex: 1;
  min-width: 0;
  max-width: 100%;
  font-size: 12px;
  color: var(--noesis-color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shard-length {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--noesis-color-text-placeholder);
}

.shard-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.shard-content {
  flex: 1;
  min-height: 0;
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-sm);
  padding: 10px;
  font-family: ui-monospace, 'SF Mono', Monaco, monospace;
  font-size: 12px;
  line-height: 1.55;
  color: var(--noesis-color-text-body);
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.shard-footer {
  flex-shrink: 0;
  margin-top: 8px;
  color: var(--noesis-color-text-placeholder);
  font-size: 11px;
  text-align: right;
}

.pagination {
  display: flex;
  justify-content: center;
  padding-top: 12px;
  flex-shrink: 0;
  border-top: 1px dashed var(--noesis-color-border-subtle);
}

@media (max-width: 480px) {
  .shards-toolbar {
    flex-direction: column;
    align-items: flex-start;
  }

  .shard-header {
    flex-direction: column;
    align-items: stretch;
  }

  .shard-actions {
    justify-content: flex-end;
  }

  .shard-card {
    max-height: 360px;
  }
}
</style>
