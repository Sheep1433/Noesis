<script setup lang="ts">
import type { ShardInfo } from '@/api/knowledgeBase'
import { Copy, EyeOutline } from '@vicons/ionicons-v5'
import { NAlert, NButton, NDrawer, NDrawerContent, NEmpty, NIcon, NPagination, NSpace, NSpin, NTag, useMessage } from 'naive-ui'
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

const paginatedShards = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const end = start + pageSize.value
  return allShards.value.slice(start, end)
})

const totalPages = computed(() => Math.ceil(allShards.value.length / pageSize.value))

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
    allShards.value = await getDocumentShards(props.collectionName, props.fileName)
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
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
    :width="1100"
    placement="right"
    @update:show="(val) => emit('update:show', val)"
  >
    <n-drawer-content :title="`分片浏览 · ${fileName}`" closable>
      <div v-if="loading" class="loading">
        <n-spin size="large" />
      </div>

      <div v-else-if="error" class="error">
        <n-alert type="error" :title="error" />
      </div>

      <div v-else-if="allShards.length > 0" class="shards-container">
        <div class="shards-header">
          <span class="shards-count">共 {{ allShards.length }} 个分片（DeepDoc 结构分块）</span>
        </div>

        <div class="shards-grid">
          <div
            v-for="(shard, index) in paginatedShards"
            :key="shard.id"
            class="shard-card"
          >
            <div class="shard-header">
              <n-space :size="6" align="center">
                <span class="shard-index">#{{ shard.chunk_index ?? (page - 1) * pageSize + index + 1 }}</span>
                <n-tag v-if="shard.header_path" size="tiny" :bordered="false">
                  {{ shard.header_path }}
                </n-tag>
              </n-space>
              <n-space :size="4">
                <span class="shard-length">{{ shard.char_length }} 字</span>
                <n-button size="tiny" quaternary @click="emit('viewShard', shard.id)">
                  <template #icon>
                    <n-icon><EyeOutline /></n-icon>
                  </template>
                </n-button>
                <n-button size="tiny" quaternary @click="copyContent(shard)">
                  <template #icon>
                    <n-icon><Copy /></n-icon>
                  </template>
                </n-button>
              </n-space>
            </div>
            <div class="shard-content">{{ shard.content }}</div>
            <div class="shard-footer">{{ formatKbDate(shard.created_at) }}</div>
          </div>
        </div>

        <div v-if="totalPages > 1" class="pagination">
          <n-pagination
            v-model:page="page"
            :page-count="totalPages"
            :page-slot="5"
            @update:page="handlePageChange"
          />
        </div>
      </div>

      <n-empty v-else description="暂无分片" />
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
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
}

.shards-header {
  margin-bottom: 16px;
}

.shards-count {
  color: var(--noesis-color-text-muted);
  font-size: 14px;
}

.shards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
  flex: 1;
  overflow-y: auto;
}

.shard-card {
  display: flex;
  flex-direction: column;
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-sm);
  padding: 12px;
  min-height: 180px;
  max-height: 320px;
  overflow: hidden;
}

.shard-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.shard-index {
  font-weight: 600;
  color: var(--noesis-color-text);
  font-size: 13px;
}

.shard-length {
  color: var(--noesis-color-text-placeholder);
  font-size: 12px;
}

.shard-content {
  flex: 1;
  background: var(--noesis-color-bg-elevated);
  border-radius: var(--noesis-radius-sm);
  padding: 8px;
  font-family: ui-monospace, 'SF Mono', Monaco, monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 8px;
}

.shard-footer {
  color: var(--noesis-color-text-placeholder);
  font-size: 11px;
  text-align: right;
}

.pagination {
  display: flex;
  justify-content: center;
  padding: 16px 0;
  margin-top: auto;
}
</style>
