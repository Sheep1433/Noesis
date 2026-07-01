<script setup lang="ts">
import type { CollectionInfo, KnowledgeBaseStatus } from '@/api/knowledgeBase'
import { Add, ArrowForward, Library, Refresh, TrashOutline } from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NSpace,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  createCollection,
  deleteCollection,
  getCollections,
  getKnowledgeBaseStatus,
} from '@/api/knowledgeBase'
import { formatKbDate } from '@/utils/kbFormat'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const loading = ref(true)
const error = ref<string | null>(null)
const status = ref<KnowledgeBaseStatus | null>(null)
const collections = ref<CollectionInfo[]>([])

const showCreateModal = ref(false)
const createLoading = ref(false)
const createForm = ref({
  name: '',
  vector_dimension: 1024,
})

const totalDocuments = computed(() =>
  collections.value.reduce((sum, c) => sum + (c.documents_count || 0), 0),
)
const totalShards = computed(() =>
  collections.value.reduce((sum, c) => sum + (c.points_count || 0), 0),
)

onMounted(async () => {
  await loadData()
})

async function loadData() {
  loading.value = true
  error.value = null

  try {
    const [statusData, collectionsData] = await Promise.all([
      getKnowledgeBaseStatus(),
      getCollections(),
    ])
    status.value = statusData
    collections.value = collectionsData
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function goToDetail(name: string) {
  router.push({
    name: 'KnowledgeBaseDetail',
    params: { collectionName: name },
  })
}

function openCreateModal() {
  createForm.value = {
    name: '',
    vector_dimension: 1024,
  }
  showCreateModal.value = true
}

async function handleCreate() {
  if (!createForm.value.name.trim()) {
    message.error('请输入知识库名称')
    return
  }

  createLoading.value = true
  try {
    await createCollection({
      name: createForm.value.name.trim(),
      vector_dimension: createForm.value.vector_dimension,
    })
    message.success('知识库创建成功')
    showCreateModal.value = false
    await loadData()
  } catch (e: any) {
    message.error(e.message || '创建失败')
  } finally {
    createLoading.value = false
  }
}

function confirmDeleteCollection(collection: CollectionInfo, event: Event) {
  event.stopPropagation()
  dialog.warning({
    title: '删除知识库',
    content: `确定删除「${collection.name}」？将同时清除其中 ${collection.documents_count} 个文档与 ${collection.points_count} 个向量分片，且不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await deleteCollection(collection.name)
        message.success(result.message || '已删除')
        await loadData()
      } catch (e: any) {
        message.error(e.message || '删除失败')
      }
    },
  })
}
</script>

<template>
  <div class="kb-page">
    <header class="kb-hero">
      <div class="kb-hero-text">
        <h1 class="kb-title">
          知识库
        </h1>
        <p class="kb-subtitle">
          智能文档解析 · 结构分块 · 混合检索 · 向量精排
        </p>
      </div>
      <n-space>
        <n-button quaternary :loading="loading" @click="loadData">
          <template #icon>
            <n-icon><Refresh /></n-icon>
          </template>
          刷新
        </n-button>
        <n-button type="primary" :disabled="!status?.connected" @click="openCreateModal">
          <template #icon>
            <n-icon><Add /></n-icon>
          </template>
          新建知识库
        </n-button>
      </n-space>
    </header>

    <div class="kb-status-bar">
      <div class="status-pill" :class="{ online: status?.connected }">
        <span class="status-dot"></span>
        <span>{{ status?.connected ? '向量库已连接' : '向量库未连接' }}</span>
        <span v-if="status?.connected" class="status-detail">
          {{ status.host }}:{{ status.port }}
        </span>
      </div>
      <div v-if="status?.connected && collections.length" class="summary-stats">
        <span>{{ collections.length }} 个知识库</span>
        <span class="sep">·</span>
        <span>{{ totalDocuments }} 篇文档</span>
        <span class="sep">·</span>
        <span>{{ totalShards }} 个分片</span>
      </div>
    </div>

    <div v-if="loading" class="state-block">
      <n-spin size="large" />
      <span>加载知识库列表…</span>
    </div>

    <div v-else-if="error" class="state-block">
      <n-alert type="error" :title="error" style="max-width: 480px" />
    </div>

    <n-alert
      v-else-if="!status?.connected"
      type="warning"
      title="向量库未连接"
      class="disconnect-alert"
    >
      请确认 Qdrant 已启动，并检查后端 <code>config.yaml</code> 中的 qdrant 配置。
    </n-alert>

    <div v-else-if="collections.length === 0" class="state-block">
      <n-empty description="还没有知识库，创建第一个开始入库">
        <template #extra>
          <n-button type="primary" :disabled="!status?.connected" @click="openCreateModal">
            新建知识库
          </n-button>
        </template>
      </n-empty>
    </div>

    <div v-else class="kb-grid">
      <article
        v-for="collection in collections"
        :key="collection.name"
        class="kb-card"
        @click="goToDetail(collection.name)"
      >
        <div class="kb-card-icon">
          <n-icon size="22">
            <Library />
          </n-icon>
        </div>
        <div class="kb-card-body">
          <div class="kb-card-top">
            <h3 class="kb-card-name">
              {{ collection.name }}
            </h3>
            <n-space :size="4" @click.stop>
              <n-button text size="small" @click="goToDetail(collection.name)">
                <template #icon>
                  <n-icon><ArrowForward /></n-icon>
                </template>
              </n-button>
              <n-button text size="small" type="error" @click="confirmDeleteCollection(collection, $event)">
                <template #icon>
                  <n-icon><TrashOutline /></n-icon>
                </template>
              </n-button>
            </n-space>
          </div>
          <div class="kb-card-tags">
            <n-tag size="small" :bordered="false" type="success">
              智能解析
            </n-tag>
            <n-tag size="small" :bordered="false">
              dim {{ collection.vector_dimension }}
            </n-tag>
          </div>
          <dl class="kb-card-stats">
            <div>
              <dt>文档</dt>
              <dd>{{ collection.documents_count }}</dd>
            </div>
            <div>
              <dt>分片</dt>
              <dd>{{ collection.points_count }}</dd>
            </div>
            <div>
              <dt>创建</dt>
              <dd>{{ formatKbDate(collection.created_at) }}</dd>
            </div>
          </dl>
        </div>
      </article>
    </div>

    <n-modal v-model:show="showCreateModal" preset="card" title="新建知识库" style="width: 520px">
      <n-form label-placement="top">
        <n-form-item label="知识库名称" required>
          <n-input
            v-model:value="createForm.name"
            placeholder="例如 product-docs、ops-manual"
            @keyup.enter="handleCreate"
          />
        </n-form-item>
        <n-form-item label="向量维度">
          <n-input-number
            v-model:value="createForm.vector_dimension"
            :min="1"
            :max="4096"
            style="width: 100%"
          />
          <p class="form-hint">
            须与 Embedding 模型输出维度一致（text-embedding-v4 默认 1024）
          </p>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCreateModal = false">
            取消
          </n-button>
          <n-button type="primary" :loading="createLoading" @click="handleCreate">
            创建
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.kb-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  padding: 20px 24px;
  box-sizing: border-box;
  overflow-y: auto;
}

.kb-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.kb-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.kb-subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--noesis-color-text-muted);
  line-height: 1.5;
}

.kb-status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-md);
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--noesis-color-text-muted);
}

.status-pill.online {
  color: var(--noesis-color-success);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--noesis-radius-round);
  background: var(--noesis-color-danger);
}

.status-pill.online .status-dot {
  background: var(--noesis-color-success);
}

.status-detail {
  color: var(--noesis-color-text-placeholder);
  font-size: 12px;
}

.summary-stats {
  font-size: 13px;
  color: var(--noesis-color-text-muted);
}

.sep {
  margin: 0 6px;
  opacity: 0.5;
}

.state-block {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 280px;
  color: var(--noesis-color-text-muted);
}

.kb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.kb-card {
  display: flex;
  gap: 14px;
  padding: 16px;
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-md);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.kb-card:hover {
  border-color: var(--noesis-color-primary);
  box-shadow: var(--noesis-shadow-md);
}

.kb-card-icon {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-primary-bg-subtle);
  color: var(--noesis-color-primary);
}

.kb-card-body {
  flex: 1;
  min-width: 0;
}

.kb-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kb-card-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.kb-card-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin: 12px 0 0;
}

.kb-card-stats div {
  min-width: 0;
}

.kb-card-stats dt {
  margin: 0;
  font-size: 11px;
  color: var(--noesis-color-text-placeholder);
}

.kb-card-stats dd {
  margin: 2px 0 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--noesis-color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.form-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--noesis-color-text-placeholder);
}

.disconnect-alert {
  margin-bottom: 4px;
}

.disconnect-alert code {
  font-size: 12px;
}
</style>
