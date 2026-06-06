<script setup lang="ts">
import type { ShardInfo } from '@/api/knowledgeBase'
import { Copy } from '@vicons/ionicons-v5'
import { NAlert, NButton, NDrawer, NDrawerContent, NEmpty, NIcon, NPagination, NSpace, NSpin, useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { getDocumentShards } from '@/api/knowledgeBase'

const props = defineProps<{
  show: boolean
  collectionName: string
  fileName: string
}>()

defineEmits<{
  'update:show': [value: boolean]
}>()

const message = useMessage()
const loading = ref(false)
const error = ref<string | null>(null)
const allShards = ref<ShardInfo[]>([])

// 分页状态
const page = ref(1)
const pageSize = ref(20)

// 计算分页后的分片
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

function formatDate(dateStr: string | null): string {
  if (!dateStr) {
    return '-'
  }
  try {
    const date = new Date(dateStr)
    return date.toLocaleString('zh-CN')
  } catch {
    return dateStr
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
    :width="1200"
    placement="right"
    @update:show="(val) => $emit('update:show', val)"
  >
    <n-drawer-content :title="`文档分片: ${fileName}`" closable>
      <!-- 加载状态 -->
      <div v-if="loading" class="loading">
        <n-spin size="large" />
      </div>

      <!-- 错误 -->
      <div v-else-if="error" class="error">
        <n-alert type="error" :title="error" />
      </div>

      <!-- 分片内容列表 -->
      <div v-else-if="allShards.length > 0" class="shards-container">
        <div class="shards-header">
          <span class="shards-count">共 {{ allShards.length }} 个分片</span>
        </div>

        <!-- 卡片网格布局 -->
        <div class="shards-grid">
          <div
            v-for="(shard, index) in paginatedShards"
            :key="shard.id"
            class="shard-card"
          >
            <div class="shard-header">
              <span class="shard-index">#{{ (page - 1) * pageSize + index + 1 }}</span>
              <n-space>
                <span class="shard-length">{{ shard.char_length }} 字</span>
                <n-button size="tiny" @click="copyContent(shard)">
                  <template #icon>
                    <n-icon><Copy /></n-icon>
                  </template>
                </n-button>
              </n-space>
            </div>
            <div class="shard-content">{{ shard.content }}</div>
            <div class="shard-footer">{{ formatDate(shard.created_at) }}</div>
          </div>
        </div>

        <!-- 分页 -->
        <div v-if="totalPages > 1" class="pagination">
          <n-pagination
            v-model:page="page"
            :page-count="totalPages"
            :page-slot="5"
            @update:page="handlePageChange"
          />
        </div>
      </div>

      <!-- 空状态 -->
      <n-empty
        v-else
        description="暂无分片"
      />
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
  color: #666;
  font-size: 14px;
}

.shards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  flex: 1;
  overflow-y: auto;
}

.shard-card {
  display: flex;
  flex-direction: column;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 12px;
  min-height: 200px;
  max-height: 300px;
  overflow: hidden;
}

.shard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.shard-index {
  font-weight: 600;
  color: #333;
  font-size: 14px;
}

.shard-length {
  color: #999;
  font-size: 12px;
}

.shard-content {
  flex: 1;
  background: #fff;
  border-radius: 4px;
  padding: 8px;
  font-family: 'SF Mono', Monaco, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin-bottom: 8px;
}

.shard-footer {
  color: #999;
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
