<script setup lang="ts">
import type { CollectionInfo, KnowledgeBaseStatus } from '@/api/knowledgeBase'
import { ArrowForward } from '@vicons/ionicons-v5'
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
  useMessage,
} from 'naive-ui'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  createCollection,
  getCollections,
  getKnowledgeBaseStatus,
} from '@/api/knowledgeBase'

const router = useRouter()
const message = useMessage()

const loading = ref(true)
const error = ref<string | null>(null)
const status = ref<KnowledgeBaseStatus | null>(null)
const collections = ref<CollectionInfo[]>([])

// 创建 Collection 对话框
const showCreateModal = ref(false)
const createLoading = ref(false)
const createForm = ref({
  name: '',
  vector_dimension: 1024,
})

onMounted(async () => {
  await loadData()
})

async function loadData() {
  loading.value = true
  error.value = null

  try {
    // 并行加载状态和列表
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
    message.error('请输入 Collection 名称')
    return
  }

  createLoading.value = true
  try {
    await createCollection({
      name: createForm.value.name.trim(),
      vector_dimension: createForm.value.vector_dimension,
    })
    message.success('创建成功')
    showCreateModal.value = false
    await loadData()
  } catch (e: any) {
    message.error(e.message || '创建失败')
  } finally {
    createLoading.value = false
  }
}
</script>

<template>
  <div class="knowledge-base">
    <!-- 头部状态栏 -->
    <div class="header">
      <div class="header-left">
        <div class="status-indicator" :class="{ connected: status?.connected }">
          <span class="status-dot"></span>
          <span class="status-text">{{ status?.connected ? '已连接' : '未连接' }}</span>
          <span v-if="status?.connected" class="status-info">
            {{ status.host }}:{{ status.port }} · {{ status.collections_count }} 个 Collection
          </span>
        </div>
      </div>
      <div class="header-right">
        <n-button type="primary" :disabled="!status?.connected" @click="openCreateModal">
          创建 Collection
        </n-button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading">
      <n-spin size="large" />
      <span>加载中...</span>
    </div>

    <!-- 错误提示 -->
    <div v-else-if="error" class="error">
      <n-alert type="error" :title="error" />
    </div>

    <!-- Collection 列表 -->
    <div v-else class="collections-grid">
      <div
        v-for="collection in collections"
        :key="collection.name"
        class="collection-card"
        @click="goToDetail(collection.name)"
      >
        <div class="card-header">
          <h3 class="collection-name">{{ collection.name }}</h3>
          <n-button text @click.stop="goToDetail(collection.name)">
            <template #icon>
              <n-icon><ArrowForward /></n-icon>
            </template>
          </n-button>
        </div>
        <div class="card-desc-row">
          <span class="card-desc">知识库</span>
        </div>
        <div class="card-body">
          <div class="stat-item">
            <span class="stat-label">文档</span>
            <span class="stat-value">{{ collection.documents_count }} 个</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">分片</span>
            <span class="stat-value">{{ collection.points_count }} 段</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">维度</span>
            <span class="stat-value">{{ collection.vector_dimension }}</span>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="collections.length === 0" class="empty-state">
        <n-empty description="暂无知识库">
          <template #extra>
            <n-button type="primary" :disabled="!status?.connected" @click="openCreateModal">
              创建第一个知识库
            </n-button>
          </template>
        </n-empty>
      </div>
    </div>

    <!-- 创建 Collection 对话框 -->
    <n-modal v-model:show="showCreateModal" preset="card" title="创建 Collection" style="width: 520px;">
      <n-form label-placement="top">
        <n-form-item label="Collection 名称" required>
          <n-input
            v-model:value="createForm.name"
            placeholder="请输入 Collection 名称"
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
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCreateModal = false">取消</n-button>
          <n-button type="primary" :loading="createLoading" @click="handleCreate">创建</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.knowledge-base {
  padding: 20px;
  height: 100%;
  overflow-y: auto;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.header-left {
  flex: 1;
}

.header-right {
  flex-shrink: 0;
}

.status-indicator {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--noesis-color-bg-muted);
  border-radius: var(--noesis-radius-pill);
  font-size: 14px;
}

.status-indicator.connected {
  background: rgb(81 207 102 / 12%);
  color: var(--noesis-color-success);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--noesis-radius-round);
  background: var(--noesis-color-danger);
}

.status-indicator.connected .status-dot {
  background: var(--noesis-color-success);
}

.status-info {
  color: var(--noesis-color-text-muted);
  margin-left: 8px;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 300px;
  gap: 16px;
  color: var(--noesis-color-text-muted);
}

.error {
  max-width: 400px;
  margin: 40px auto;
}

.collections-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.collection-card {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-sm);
  padding: 16px;
  cursor: pointer;
  transition: all 0.3s;
}

.collection-card:hover {
  border-color: var(--noesis-color-primary);
  box-shadow: var(--noesis-shadow-md);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.collection-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--noesis-color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-desc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.card-desc {
  font-size: 12px;
  color: var(--noesis-color-primary);
  padding: 4px 8px;
  background: var(--noesis-color-primary-bg-subtle);
  border-radius: 4px;
  display: inline-block;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stat-item {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
}

.stat-label {
  color: var(--noesis-color-text-placeholder);
}

.stat-value {
  color: var(--noesis-color-text);
  font-weight: 500;
}

.empty-state {
  grid-column: 1 / -1;
  padding: 60px 0;
}

.hint-text {
  color: var(--noesis-color-text-placeholder);
  font-size: 14px;
}
</style>
