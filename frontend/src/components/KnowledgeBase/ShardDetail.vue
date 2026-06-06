<script setup lang="ts">
import type { ShardDetail as ShardDetailType } from '@/api/knowledgeBase'
import { Copy } from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NIcon,
  NInput,
  NModal,
  NSpin,
  useMessage,
} from 'naive-ui'
import { ref, watch } from 'vue'
import { getShardDetail } from '@/api/knowledgeBase'

const props = defineProps<{
  show: boolean
  collectionName: string
  shardId: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const message = useMessage()
const loading = ref(false)
const error = ref<string | null>(null)
const detail = ref<ShardDetailType | null>(null)

const showModal = computed({
  get: () => props.show,
  set: (val) => emit('update:show', val),
})

watch(
  () => [props.show, props.collectionName, props.shardId],
  async ([newShow, newCollection, newShard]) => {
    if (newShow && newCollection && newShard) {
      await loadDetail()
    }
  },
)

async function loadDetail() {
  if (!props.collectionName || !props.shardId) {
    return
  }

  loading.value = true
  error.value = null

  try {
    detail.value = await getShardDetail(props.collectionName, props.shardId)
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function truncateId(id: string): string {
  if (id.length > 16) {
    return `${id.substring(0, 8)}...${id.substring(id.length - 8)}`
  }
  return id
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

async function copyContent() {
  if (!detail.value) {
    return
  }

  try {
    await navigator.clipboard.writeText(detail.value.content)
    message.success('复制成功')
  } catch {
    message.error('复制失败')
  }
}
</script>

<template>
  <n-modal
    v-model:show="showModal"
    preset="card"
    :title="`分片详情 ${shardId ? `(${truncateId(shardId)})` : ''}`"
    style="width: 600px"
  >
    <template v-if="loading">
      <div class="loading">
        <n-spin size="large" />
      </div>
    </template>

    <template v-else-if="error">
      <n-alert type="error" :title="error" />
    </template>

    <template v-else-if="detail">
      <div class="detail-content">
        <n-descriptions :column="2" bordered size="small">
          <n-descriptions-item label="分片 ID">
            <code>{{ detail.id }}</code>
          </n-descriptions-item>
          <n-descriptions-item label="向量维度">
            {{ detail.vector_dimension }}
          </n-descriptions-item>
          <n-descriptions-item label="字符数">
            {{ detail.char_length }}
          </n-descriptions-item>
          <n-descriptions-item label="创建时间">
            {{ formatDate(detail.created_at) }}
          </n-descriptions-item>
          <n-descriptions-item v-if="detail.chunk_index !== null && detail.chunk_index !== undefined" label="分片序号">
            {{ detail.chunk_index }}
          </n-descriptions-item>
          <n-descriptions-item v-if="detail.header_path" label="标题路径" :span="2">
            {{ detail.header_path }}
          </n-descriptions-item>
          <n-descriptions-item v-if="detail.Header_2" label="二级标题">
            {{ detail.Header_2 }}
          </n-descriptions-item>
        </n-descriptions>

        <div class="content-preview">
          <div class="preview-header">
            <span>内容预览</span>
            <n-button size="small" @click="copyContent">
              <template #icon>
                <n-icon><Copy /></n-icon>
              </template>
              复制
            </n-button>
          </div>
          <n-input
            :value="detail.content"
            type="textarea"
            readonly
            :rows="10"
            placeholder="内容预览"
          />
        </div>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.loading {
  display: flex;
  justify-content: center;
  padding: 40px;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.content-preview {
  margin-top: 8px;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 14px;
  color: #666;
}

code {
  font-family: monospace;
  font-size: 12px;
  word-break: break-all;
}

.json-snapshot {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  background: #f6f8fa;
  padding: 8px;
  border-radius: 6px;
  max-height: 160px;
  overflow-y: auto;
}
</style>
