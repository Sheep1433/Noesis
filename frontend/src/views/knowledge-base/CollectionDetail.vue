<script setup lang="ts">
import type {
  DocumentInfo,
  CollectionDetail as KbCollectionDetail,
  KbSearchMode,
  SearchCollectionRequest,
  SearchResult,
} from '@/api/knowledgeBase'
import { ArrowBack, CloudUpload } from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NEmpty,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NSpace,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  NUpload,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  deleteDocument,
  getCollection,
  getDocuments,
  KB_DEFAULT_QUERY,
  searchCollection,
  uploadDocument,
} from '@/api/knowledgeBase'
import DocumentDrawer from '@/components/KnowledgeBase/DocumentDrawer.vue'
import KbSearchPanel from '@/components/KnowledgeBase/KbSearchPanel.vue'
import KbSearchResults from '@/components/KnowledgeBase/KbSearchResults.vue'

const router = useRouter()
const route = useRoute()
const message = useMessage()

const collectionName = route.params.collectionName as string

const loading = ref(true)
const searching = ref(false)
const uploading = ref(false)
const error = ref<string | null>(null)
const collectionDetail = ref<KbCollectionDetail | null>(null)
const documents = ref<DocumentInfo[]>([])

const drawerShow = ref(false)
const selectedFileName = ref('')

const showUploadModal = ref(false)
const selectedFile = ref<File | null>(null)

const showDeleteModal = ref(false)
const deleteTargetFile = ref('')

const searchQuery = ref('')
const searchMode = ref<KbSearchMode>('vector')
const searchLimitOverride = ref<number | null>(null)
const searchFilterFileName = ref('')
const searchResults = ref<SearchResult[]>([])

const defaultQueryLimit = computed(() => KB_DEFAULT_QUERY.limit ?? 10)

onMounted(async () => {
  await loadData()
})

async function loadData() {
  loading.value = true
  error.value = null

  try {
    const [collectionData, documentsData] = await Promise.all([
      getCollection(collectionName),
      getDocuments(collectionName),
    ])
    collectionDetail.value = collectionData
    documents.value = documentsData
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function goBack() {
  router.push({ name: 'KnowledgeBase' })
}

function openUploadModal() {
  selectedFile.value = null
  showUploadModal.value = true
}

function openDrawer(fileName: string) {
  selectedFileName.value = fileName
  drawerShow.value = true
}

function useDocumentAsQuery(fileName: string) {
  searchFilterFileName.value = fileName
  searchQuery.value = `关于《${fileName}》的内容`
}

async function handleSearch() {
  if (!searchQuery.value.trim()) {
    return
  }

  searching.value = true
  searchResults.value = []

  try {
    const body: SearchCollectionRequest = {
      query: searchQuery.value.trim(),
      search_mode: searchMode.value,
    }
    if (searchLimitOverride.value !== null && searchLimitOverride.value !== undefined) {
      body.limit = searchLimitOverride.value
    }
    if (searchFilterFileName.value.trim()) {
      body.filters = { file_name: searchFilterFileName.value.trim() }
    }
    const results = await searchCollection(collectionName, body)
    searchResults.value = results
    if (results.length === 0) {
      message.warning('未找到相关结果')
    }
  } catch (e: any) {
    message.error(e.message || '检索失败')
  } finally {
    searching.value = false
  }
}

function handleDelete(fileName: string) {
  deleteTargetFile.value = fileName
  showDeleteModal.value = true
}

async function confirmDelete() {
  try {
    const result = await deleteDocument(collectionName, deleteTargetFile.value)
    if (result.success) {
      message.success(result.message)
      await loadData()
    } else {
      message.error(result.message)
    }
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
  showDeleteModal.value = false
}

function handleUploadChange(options: any) {
  const file = options.file.file
  if (file) {
    selectedFile.value = file
  }
}

async function handleUpload() {
  if (!selectedFile.value) {
    return
  }

  uploading.value = true

  try {
    const result = await uploadDocument(collectionName, selectedFile.value)
    if (result.success) {
      const shards = typeof result.shards_created === 'number' ? `，共 ${result.shards_created} 个分片` : ''
      message.success((result.message || '上传成功') + shards)
      showUploadModal.value = false
      selectedFile.value = null
      await loadData()
    } else {
      message.error(result.message || '上传失败')
    }
  } catch (e: any) {
    message.error(e.message || '上传失败')
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="collection-detail">
    <header class="page-header">
      <n-button quaternary circle class="back-btn" @click="goBack">
        <template #icon>
          <n-icon><ArrowBack /></n-icon>
        </template>
      </n-button>
      <div class="header-main">
        <h1 class="page-title">
          {{ collectionName }}
        </h1>
        <div v-if="collectionDetail" class="tag-row">
          <n-tag size="small" :bordered="false">
            维度 {{ collectionDetail.vector_dimension }}
          </n-tag>
          <n-tag size="small" :bordered="false">
            {{ collectionDetail.documents_count }} 文档
          </n-tag>
          <n-tag size="small" :bordered="false">
            {{ collectionDetail.points_count }} 分片
          </n-tag>
          <n-tag size="small" :bordered="false">
            平台默认 Top {{ defaultQueryLimit }}
          </n-tag>
        </div>
      </div>
      <n-space class="header-actions">
        <n-button type="primary" @click="openUploadModal">
          <template #icon>
            <n-icon><CloudUpload /></n-icon>
          </template>
          上传
        </n-button>
      </n-space>
    </header>

    <div v-if="loading" class="state-center">
      <n-spin size="large" />
      <span>加载中…</span>
    </div>

    <div v-else-if="error" class="state-center">
      <n-alert type="error" :title="error" style="max-width: 480px" />
    </div>

    <div v-else class="split-layout">
      <section class="panel panel-docs">
        <div class="panel-head">
          <h2>文档</h2>
          <span class="panel-sub">{{ documents.length }} 个文件</span>
        </div>
        <div class="doc-list">
          <div
            v-for="doc in documents"
            :key="doc.file_name"
            class="doc-row"
          >
            <button type="button" class="doc-name" @click="openDrawer(doc.file_name)">
              {{ doc.file_name }}
            </button>
            <span class="doc-meta">{{ doc.shard_count }} 片</span>
            <n-space :size="4">
              <n-button size="tiny" quaternary @click="useDocumentAsQuery(doc.file_name)">
                检索
              </n-button>
              <n-button size="tiny" quaternary @click="openDrawer(doc.file_name)">
                分片
              </n-button>
              <n-button size="tiny" quaternary type="error" @click="handleDelete(doc.file_name)">
                删除
              </n-button>
            </n-space>
          </div>
          <n-empty v-if="documents.length === 0" description="暂无文档，点击上传" size="small" />
        </div>
      </section>

      <section class="panel panel-search">
        <n-tabs type="line" animated default-value="search">
          <n-tab-pane name="search" tab="检索测试">
            <KbSearchPanel
              v-model:query="searchQuery"
              v-model:search-mode="searchMode"
              v-model:limit-override="searchLimitOverride"
              v-model:filter-file-name="searchFilterFileName"
              :loading="searching"
              :default-limit="defaultQueryLimit as number"
              @search="handleSearch"
            />
            <KbSearchResults
              class="results-block"
              :results="searchResults"
              :loading="searching"
            />
          </n-tab-pane>
        </n-tabs>
      </section>
    </div>

    <DocumentDrawer
      v-model:show="drawerShow"
      :collection-name="collectionName"
      :file-name="selectedFileName"
    />

    <n-modal v-model:show="showUploadModal" preset="card" title="上传文档" style="width: 480px">
      <n-upload accept=".txt,.md,.markdown,.pdf,.docx,.doc,.xlsx,.xls,.csv" :max="1" @change="handleUploadChange">
        <n-button>选择文件</n-button>
      </n-upload>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showUploadModal = false">
            取消
          </n-button>
          <n-button type="primary" :loading="uploading" :disabled="!selectedFile" @click="handleUpload">
            上传
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="showDeleteModal"
      preset="dialog"
      title="确认删除"
      :content="`确定要删除文档 ${deleteTargetFile} 吗？`"
      positive-text="删除"
      negative-text="取消"
      @positive-click="confirmDelete"
    />
  </div>
</template>

<style scoped>
.collection-detail {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px 20px 20px;
  box-sizing: border-box;
  overflow: hidden;
}

.page-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
  flex-shrink: 0;
}

.header-main {
  flex: 1;
  min-width: 0;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
}

.page-desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--noesis-color-info);
  line-height: 1.5;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.header-actions {
  flex-shrink: 0;
}

.state-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--noesis-color-info);
}

.split-layout {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(280px, 38%) 1fr;
  gap: 16px;
  min-height: 0;
}

.panel {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-md);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--noesis-color-border);
  flex-shrink: 0;
}

.panel-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.panel-sub {
  font-size: 12px;
  color: var(--noesis-color-info);
}

.panel-search {
  padding: 0 4px 12px;
}

.panel-search :deep(.n-tabs-pane-wrapper) {
  padding: 0 12px;
  overflow-y: auto;
  max-height: calc(100vh - 220px);
}

.doc-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.doc-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 8px;
  padding: 10px 8px;
  border-radius: 6px;
}

.doc-row:hover {
  background: var(--noesis-color-bg-hover);
}

.doc-name {
  border: none;
  background: none;
  padding: 0;
  text-align: left;
  color: var(--noesis-color-primary);
  cursor: pointer;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 12px;
  color: var(--noesis-color-info);
  white-space: nowrap;
}

.results-block {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed var(--noesis-color-border);
}

@media (max-width: 960px) {
  .split-layout {
    grid-template-columns: 1fr;
    overflow-y: auto;
  }

  .panel-search :deep(.n-tabs-pane-wrapper) {
    max-height: none;
  }
}
</style>
