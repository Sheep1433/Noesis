<script setup lang="ts">
import type { DataTableColumns } from 'naive-ui'
import type {
  CollectionConfig,
  DocumentInfo,
  CollectionDetail as KbCollectionDetail,
  KbSearchMode,
  SearchCollectionRequest,
  SearchResult,
  SearchTiming,
} from '@/api/knowledgeBase'
import { ArrowBack, CloudUpload, DocumentTextOutline, Refresh, Search, SettingsOutline } from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NFormItem,
  NIcon,
  NInputNumber,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  NUpload,
  useDialog,
  useMessage,
} from 'naive-ui'
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  deleteCollection,
  deleteDocument,
  getCollection,
  getCollectionConfig,
  getDocuments,
  KB_DEFAULT_QUERY,
  patchCollectionConfig,
  searchCollection,
  uploadDocument,
} from '@/api/knowledgeBase'
import DocumentDrawer from '@/components/KnowledgeBase/DocumentDrawer.vue'
import KbSearchPanel from '@/components/KnowledgeBase/KbSearchPanel.vue'
import KbSearchResults from '@/components/KnowledgeBase/KbSearchResults.vue'
import ShardDetail from '@/components/KnowledgeBase/ShardDetail.vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { copyToClipboard } from '@/utils/copy'
import { fileTypeLabel, formatKbDate, shortHash, SUPPORTED_DOC_FORMATS } from '@/utils/kbFormat'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const dialog = useDialog()
const { isMobile } = useBreakpoint()
const uploadModalWidth = computed(() => (isMobile.value ? 'min(560px, calc(100vw - 32px))' : '560px'))

const collectionName = computed(() => String(route.params.collectionName || ''))

const loading = ref(true)
const searching = ref(false)
const uploading = ref(false)
const error = ref<string | null>(null)
const collectionDetail = ref<KbCollectionDetail | null>(null)
const documents = ref<DocumentInfo[]>([])

const drawerShow = ref(false)
const selectedFileName = ref('')

const shardDetailShow = ref(false)
const selectedShardId = ref('')

const showUploadModal = ref(false)
const selectedFile = ref<File | null>(null)
const uploadPreview = ref<string | null>(null)

const showDeleteModal = ref(false)
const deleteTargetFile = ref('')

const activeTab = ref('documents')

const searchQuery = ref('')
const searchMode = ref<KbSearchMode>('hybrid')
const searchFinalTopKOverride = ref<number | null>(null)
const searchRecallTopKOverride = ref<number | null>(null)
const searchRerankTopKOverride = ref<number | null>(null)
const searchUseRerankerOverride = ref<boolean | null>(null)
const searchScoreThresholdOverride = ref<number | null>(null)
const searchRrfKOverride = ref<number | null>(null)
const searchFilterFileName = ref('')
const searchResults = ref<SearchResult[]>([])
const searchTiming = ref<SearchTiming | null>(null)

const collectionConfig = ref<CollectionConfig | null>(null)
const configSaving = ref(false)
const configChunkSize = ref<number | null>(null)
const configChunkOverlap = ref<number | null>(null)
const configFinalTopK = ref<number | null>(null)
const configRecallTopK = ref<number | null>(null)
const configRerankTopK = ref<number | null>(null)
const configUseReranker = ref<boolean | null>(null)
const configScoreThreshold = ref<number | null>(null)
const configRrfK = ref<number | null>(null)

const chunkPreset = computed(() =>
  collectionConfig.value?.processing_params?.chunk_preset_id
  || collectionConfig.value?.processing_params?.chunk_template_id
  || 'general',
)

const defaultQueryLimit = computed(() =>
  collectionConfig.value?.query_params?.final_top_k
  ?? collectionConfig.value?.query_params?.limit
  ?? KB_DEFAULT_QUERY.final_top_k
  ?? 10,
)
const defaultRecallTopK = computed(() =>
  collectionConfig.value?.query_params?.recall_top_k ?? KB_DEFAULT_QUERY.recall_top_k ?? 20,
)
const defaultRerankTopK = computed(() =>
  collectionConfig.value?.query_params?.rerank_top_k ?? KB_DEFAULT_QUERY.rerank_top_k ?? 15,
)
const defaultUseReranker = computed(() =>
  collectionConfig.value?.query_params?.use_reranker ?? KB_DEFAULT_QUERY.use_reranker ?? true,
)
const defaultRrfK = computed(() =>
  collectionConfig.value?.query_params?.rrf_k ?? KB_DEFAULT_QUERY.rrf_k ?? 60,
)

async function copyHash(hash: string | null | undefined) {
  if (!hash) {
    message.warning('无 Hash 信息')
    return
  }
  try {
    await copyToClipboard(hash)
    message.success('Hash 已复制')
  } catch {
    message.error('复制失败')
  }
}

const docColumns: DataTableColumns<DocumentInfo> = [
  {
    title: '文件名',
    key: 'file_name',
    ellipsis: { tooltip: true },
    render: (row) => h(
      'button',
      {
        type: 'button',
        class: 'doc-link',
        onClick: () => openDrawer(row.file_name),
      },
      row.file_name,
    ),
  },
  {
    title: '类型',
    key: 'type',
    width: 88,
    render: (row) => fileTypeLabel(row.file_name),
  },
  {
    title: '分片',
    key: 'shard_count',
    width: 72,
    align: 'right',
  },
  {
    title: 'Hash',
    key: 'file_hash',
    width: 100,
    render: (row) => h(
      'button',
      {
        type: 'button',
        class: 'hash-link',
        title: row.file_hash || '',
        onClick: () => copyHash(row.file_hash),
      },
      shortHash(row.file_hash),
    ),
  },
  {
    title: '入库时间',
    key: 'uploaded_at',
    width: 148,
    render: (row) => formatKbDate(row.uploaded_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) => h(NSpace, { size: 4 }, {
      default: () => [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => useDocumentAsQuery(row.file_name) }, { default: () => '检索' }),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openDrawer(row.file_name) }, { default: () => '分片' }),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => handleDelete(row.file_name) }, { default: () => '删除' }),
      ],
    }),
  },
]

const docColumnsMobile: DataTableColumns<DocumentInfo> = [
  {
    title: '文件名',
    key: 'file_name',
    ellipsis: { tooltip: true },
    render: (row) => h(
      'button',
      {
        type: 'button',
        class: 'doc-link',
        onClick: () => openDrawer(row.file_name),
      },
      row.file_name,
    ),
  },
  {
    title: '分片',
    key: 'shard_count',
    width: 56,
    align: 'right',
  },
  {
    title: '操作',
    key: 'actions',
    width: 148,
    render: (row) => h(NSpace, { size: 2, wrap: false }, {
      default: () => [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => useDocumentAsQuery(row.file_name) }, { default: () => '检索' }),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openDrawer(row.file_name) }, { default: () => '分片' }),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => handleDelete(row.file_name) }, { default: () => '删' }),
      ],
    }),
  },
]

const activeDocColumns = computed(() => (isMobile.value ? docColumnsMobile : docColumns))

onMounted(async () => {
  await loadData()
})

watch(
  () => route.params.collectionName,
  async (name) => {
    if (name) {
      await loadData()
    }
  },
)

async function loadData() {
  const name = collectionName.value
  if (!name) {
    return
  }
  loading.value = true
  error.value = null

  try {
    const [collectionData, documentsData, configData] = await Promise.all([
      getCollection(name),
      getDocuments(name),
      getCollectionConfig(name).catch(() => null),
    ])
    collectionDetail.value = collectionData
    documents.value = documentsData
    collectionConfig.value = configData
    if (configData?.processing_params?.chunk_parser_config) {
      configChunkSize.value = configData.processing_params.chunk_parser_config.chunk_size ?? null
      configChunkOverlap.value = configData.processing_params.chunk_parser_config.chunk_overlap ?? null
    }
    if (configData?.query_params) {
      configFinalTopK.value = configData.query_params.final_top_k ?? configData.query_params.limit ?? null
      configRecallTopK.value = configData.query_params.recall_top_k ?? null
      configRerankTopK.value = configData.query_params.rerank_top_k ?? null
      configUseReranker.value = configData.query_params.use_reranker ?? null
      configRrfK.value = configData.query_params.rrf_k ?? null
      const st = configData.query_params.score_threshold
      configScoreThreshold.value = st === null || st === undefined ? null : Number(st)
      if (configData.query_params.search_mode) {
        searchMode.value = configData.query_params.search_mode
      }
    }
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function goBack() {
  router.push({ name: 'KnowledgeBase' })
}

function confirmDeleteCollection() {
  dialog.warning({
    title: '删除知识库',
    content: `确定删除「${collectionName.value}」及其中全部文档与向量？此操作不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await deleteCollection(collectionName.value)
        message.success(result.message || '已删除')
        router.push({ name: 'KnowledgeBase' })
      } catch (e: any) {
        message.error(e.message || '删除失败')
      }
    },
  })
}

function openUploadModal() {
  selectedFile.value = null
  uploadPreview.value = null
  showUploadModal.value = true
}

function openDrawer(fileName: string) {
  selectedFileName.value = fileName
  drawerShow.value = true
}

function openShardDetail(shardId: string) {
  selectedShardId.value = shardId
  shardDetailShow.value = true
}

function useDocumentAsQuery(fileName: string) {
  searchFilterFileName.value = fileName
  searchQuery.value = `关于《${fileName}》的内容`
  activeTab.value = 'search'
}

async function handleSearch() {
  if (!searchQuery.value.trim()) {
    return
  }

  searching.value = true
  searchResults.value = []
  searchTiming.value = null

  try {
    const body: SearchCollectionRequest = {
      query: searchQuery.value.trim(),
      search_mode: searchMode.value,
    }
    if (searchFinalTopKOverride.value !== null && searchFinalTopKOverride.value !== undefined) {
      body.final_top_k = searchFinalTopKOverride.value
    }
    if (searchRecallTopKOverride.value !== null && searchRecallTopKOverride.value !== undefined) {
      body.recall_top_k = searchRecallTopKOverride.value
    }
    if (searchRerankTopKOverride.value !== null && searchRerankTopKOverride.value !== undefined) {
      body.rerank_top_k = searchRerankTopKOverride.value
    }
    if (searchUseRerankerOverride.value !== null) {
      body.use_reranker = searchUseRerankerOverride.value
    }
    if (searchScoreThresholdOverride.value !== null && searchScoreThresholdOverride.value !== undefined) {
      body.score_threshold = searchScoreThresholdOverride.value
    }
    if (searchRrfKOverride.value !== null && searchRrfKOverride.value !== undefined) {
      body.rrf_k = searchRrfKOverride.value
    }
    if (searchFilterFileName.value.trim()) {
      body.filters = { file_name: searchFilterFileName.value.trim() }
    }
    const { results, timing } = await searchCollection(collectionName.value, body)
    searchResults.value = results
    searchTiming.value = timing
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
    const result = await deleteDocument(collectionName.value, deleteTargetFile.value)
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
    uploadPreview.value = null
  }
}

async function handleUpload() {
  if (!selectedFile.value) {
    return
  }

  uploading.value = true
  uploadPreview.value = null

  try {
    const result = await uploadDocument(collectionName.value, selectedFile.value)
    if (result.success) {
      const shards = typeof result.shards_created === 'number' ? `，共 ${result.shards_created} 个分片` : ''
      message.success((result.message || '上传成功') + shards)
      if (result.extracted_markdown) {
        uploadPreview.value = result.extracted_markdown
      } else {
        showUploadModal.value = false
        selectedFile.value = null
      }
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

function closeUploadModal() {
  showUploadModal.value = false
  selectedFile.value = null
  uploadPreview.value = null
}

async function saveCollectionConfig() {
  configSaving.value = true
  try {
    const patch: Parameters<typeof patchCollectionConfig>[1] = {
      query_params: {
        search_mode: searchMode.value,
        final_top_k: configFinalTopK.value ?? defaultQueryLimit.value as number,
        recall_top_k: configRecallTopK.value ?? defaultRecallTopK.value,
        rerank_top_k: configRerankTopK.value ?? defaultRerankTopK.value,
        use_reranker: configUseReranker.value ?? defaultUseReranker.value,
        score_threshold: configScoreThreshold.value,
        rrf_k: configRrfK.value ?? defaultRrfK.value,
      },
    }
    if (configChunkSize.value != null || configChunkOverlap.value != null) {
      patch.processing_params = {
        chunk_parser_config: {
          ...(configChunkSize.value != null ? { chunk_size: configChunkSize.value } : {}),
          ...(configChunkOverlap.value != null ? { chunk_overlap: configChunkOverlap.value } : {}),
        },
      }
    }
    collectionConfig.value = await patchCollectionConfig(collectionName.value, patch)
    message.success('策略配置已保存')
  } catch (e: any) {
    message.error(e.message || '保存配置失败')
  } finally {
    configSaving.value = false
  }
}
</script>

<template>
  <div class="kb-detail">
    <header class="detail-header">
      <n-button quaternary circle @click="goBack">
        <template #icon>
          <n-icon><ArrowBack /></n-icon>
        </template>
      </n-button>
      <div class="detail-header-main">
        <h1>{{ collectionName }}</h1>
        <div v-if="collectionDetail" class="detail-meta">
          <n-tag size="small" type="success" :bordered="false">
            智能解析
          </n-tag>
          <n-tag size="small" :bordered="false">
            维度 {{ collectionDetail.vector_dimension }}
          </n-tag>
          <n-tag size="small" :bordered="false">
            {{ collectionDetail.documents_count }} 文档
          </n-tag>
          <n-tag size="small" :bordered="false">
            {{ collectionDetail.points_count }} 分片
          </n-tag>
        </div>
        <p class="pipeline-hint">
          入库流程：版式解析 → {{ chunkPreset }} 分块 → 向量化 → 入库
        </p>
      </div>
      <n-space class="detail-header-actions" :size="8" :wrap="isMobile">
        <n-button quaternary :loading="loading" @click="loadData">
          <template #icon>
            <n-icon><Refresh /></n-icon>
          </template>
          刷新
        </n-button>
        <n-button type="error" ghost @click="confirmDeleteCollection">
          删除知识库
        </n-button>
        <n-button type="primary" @click="openUploadModal">
          <template #icon>
            <n-icon><CloudUpload /></n-icon>
          </template>
          上传文档
        </n-button>
      </n-space>
    </header>

    <div v-if="loading" class="state-center">
      <n-spin size="large" />
    </div>

    <div v-else-if="error" class="state-center">
      <n-alert type="error" :title="error" style="max-width: 480px" />
    </div>

    <n-tabs
      v-else
      v-model:value="activeTab"
      :type="isMobile ? 'segment' : 'line'"
      class="detail-tabs"
      :class="{ 'detail-tabs--mobile': isMobile }"
    >
      <n-tab-pane name="documents">
        <template #tab>
          <n-icon size="16" style="margin-right: 4px">
            <DocumentTextOutline />
          </n-icon>
          文档库
        </template>
        <div class="tab-panel">
          <div v-if="documents.length" class="doc-table-scroll">
            <n-data-table
              :columns="activeDocColumns"
              :data="documents"
              :bordered="false"
              :scroll-x="isMobile ? 360 : undefined"
              size="small"
              :row-key="(row: DocumentInfo) => row.file_name"
            />
          </div>
          <n-empty v-else description="暂无文档，点击右上角上传" />
        </div>
      </n-tab-pane>

      <n-tab-pane name="search">
        <template #tab>
          <n-icon size="16" style="margin-right: 4px">
            <Search />
          </n-icon>
          检索调试
        </template>
        <div class="tab-panel tab-panel--search">
          <KbSearchPanel
            v-model:query="searchQuery"
            v-model:search-mode="searchMode"
            v-model:final-top-k-override="searchFinalTopKOverride"
            v-model:recall-top-k-override="searchRecallTopKOverride"
            v-model:rerank-top-k-override="searchRerankTopKOverride"
            v-model:use-reranker-override="searchUseRerankerOverride"
            v-model:score-threshold-override="searchScoreThresholdOverride"
            v-model:rrf-k-override="searchRrfKOverride"
            v-model:filter-file-name="searchFilterFileName"
            :loading="searching"
            :default-limit="defaultQueryLimit as number"
            :default-recall-top-k="defaultRecallTopK as number"
            :default-rerank-top-k="defaultRerankTopK as number"
            :default-use-reranker="defaultUseReranker as boolean"
            :default-rrf-k="defaultRrfK as number"
            @search="handleSearch"
          />
          <div class="search-results-scroll">
            <KbSearchResults
              :results="searchResults"
              :timing="searchTiming"
              :loading="searching"
              @view-shard="openShardDetail"
            />
          </div>
        </div>
      </n-tab-pane>

      <n-tab-pane name="config">
        <template #tab>
          <n-icon size="16" style="margin-right: 4px">
            <SettingsOutline />
          </n-icon>
          策略配置
        </template>
        <div class="tab-panel config-layout">
          <section class="config-section">
            <h3>文档解析</h3>
            <p class="section-desc">
              自动识别版式、表格与标题结构；首次解析 PDF 时可能需下载模型文件。
            </p>
            <div class="readonly-tags">
              <n-tag :bordered="false">
                分块模板：{{ chunkPreset }}
              </n-tag>
            </div>
          </section>

          <section class="config-section">
            <h3>分块策略</h3>
            <p class="section-desc">
              影响新上传文档的分片粒度；已入库文档不会自动重分块。
            </p>
            <div class="config-grid">
              <n-form-item label="chunk_size">
                <n-input-number v-model:value="configChunkSize" :min="32" :max="8000" clearable style="width: 100%" />
              </n-form-item>
              <n-form-item label="chunk_overlap">
                <n-input-number v-model:value="configChunkOverlap" :min="0" :max="2000" clearable style="width: 100%" />
              </n-form-item>
            </div>
          </section>

          <section class="config-section">
            <h3>检索策略</h3>
            <p class="section-desc">
              默认检索模式与 Top-K；可在「检索调试」页面临时覆盖后一并保存。
            </p>
            <div class="config-grid">
              <n-form-item label="search_mode">
                <n-radio-group v-model:value="searchMode" size="small">
                  <n-radio-button value="vector">
                    语义
                  </n-radio-button>
                  <n-radio-button value="bm25">
                    关键词
                  </n-radio-button>
                  <n-radio-button value="hybrid">
                    混合
                  </n-radio-button>
                </n-radio-group>
              </n-form-item>
              <n-form-item label="final_top_k">
                <n-input-number v-model:value="configFinalTopK" :min="1" :max="100" clearable style="width: 100%" />
              </n-form-item>
              <n-form-item label="recall_top_k">
                <n-input-number v-model:value="configRecallTopK" :min="1" :max="200" clearable style="width: 100%" />
              </n-form-item>
              <n-form-item label="rerank_top_k">
                <n-input-number v-model:value="configRerankTopK" :min="1" :max="100" clearable style="width: 100%" />
              </n-form-item>
              <n-form-item label="rrf_k（混合检索）">
                <n-input-number v-model:value="configRrfK" :min="1" :max="500" clearable style="width: 100%" />
              </n-form-item>
              <n-form-item label="use_reranker">
                <n-switch :value="configUseReranker ?? defaultUseReranker" @update:value="(v: boolean) => configUseReranker = v" />
              </n-form-item>
              <n-form-item label="score_threshold">
                <n-input-number v-model:value="configScoreThreshold" :min="0" :max="1" :step="0.01" clearable style="width: 100%" />
              </n-form-item>
            </div>
          </section>

          <n-button type="primary" :loading="configSaving" @click="saveCollectionConfig">
            保存策略配置
          </n-button>
        </div>
      </n-tab-pane>
    </n-tabs>

    <DocumentDrawer
      v-model:show="drawerShow"
      :collection-name="collectionName"
      :file-name="selectedFileName"
      @view-shard="openShardDetail"
    />

    <ShardDetail
      v-model:show="shardDetailShow"
      :collection-name="collectionName"
      :shard-id="selectedShardId"
    />

    <n-modal v-model:show="showUploadModal" preset="card" title="上传文档" :style="{ width: uploadModalWidth }" @after-leave="closeUploadModal">
      <div class="upload-intro">
        <p>上传后将自动解析版式、表格与标题结构，再分块写入知识库。</p>
        <div class="format-grid">
          <div v-for="fmt in SUPPORTED_DOC_FORMATS" :key="fmt.ext" class="format-item">
            <strong>{{ fmt.ext }}</strong>
            <span>{{ fmt.desc }}</span>
          </div>
        </div>
      </div>
      <n-upload
        accept=".txt,.md,.markdown,.pdf,.docx,.doc,.xlsx,.xls,.csv,.pptx,.ppt"
        :max="1"
        @change="handleUploadChange"
      >
        <n-button>选择文件</n-button>
      </n-upload>
      <div v-if="uploadPreview" class="upload-preview">
        <h4>解析预览（Markdown 摘要）</h4>
        <pre>{{ uploadPreview.slice(0, 2000) }}{{ uploadPreview.length > 2000 ? '\n…' : '' }}</pre>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="closeUploadModal">
            {{ uploadPreview ? '关闭' : '取消' }}
          </n-button>
          <n-button
            v-if="!uploadPreview"
            type="primary"
            :loading="uploading"
            :disabled="!selectedFile"
            @click="handleUpload"
          >
            开始入库
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="showDeleteModal"
      preset="dialog"
      title="删除文档"
      :content="`确定删除「${deleteTargetFile}」及其全部分片？`"
      positive-text="删除"
      negative-text="取消"
      @positive-click="confirmDelete"
    />
  </div>
</template>

<style scoped>
.kb-detail {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px 20px 20px;
  box-sizing: border-box;
  overflow: hidden;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
  flex-shrink: 0;
}

.detail-header-main {
  flex: 1;
  min-width: 0;
}

.detail-header-main h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.pipeline-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--noesis-color-text-muted);
}

.state-center {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.detail-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.detail-tabs :deep(.n-tabs) {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.detail-tabs :deep(.n-tabs-pane-wrapper) {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.detail-tabs :deep(.n-tab-pane) {
  height: 100%;
}

.tab-panel {
  padding: 12px 4px 20px;
}

.tab-panel--search {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  padding-bottom: 0;
}

.search-results-scroll {
  flex: 1;
  min-height: 0;
  margin-top: 16px;
  padding: 16px 0 20px;
  overflow-x: hidden;
  overflow-y: auto;
  border-top: 1px dashed var(--noesis-color-border);
  -webkit-overflow-scrolling: touch;
}

.tab-panel :deep(.doc-link),
.tab-panel :deep(.hash-link) {
  border: none;
  background: none;
  padding: 0;
  color: var(--noesis-color-primary);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}

.tab-panel :deep(.hash-link) {
  font-family: ui-monospace, monospace;
  font-size: 12px;
}

.config-layout {
  max-width: 720px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.config-section h3 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
}

.section-desc {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--noesis-color-text-muted);
  line-height: 1.5;
}

.readonly-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.config-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 20px;
}

.upload-intro p {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--noesis-color-text-muted);
}

.format-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  margin-bottom: 16px;
}

.format-item {
  padding: 8px 10px;
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-muted);
  font-size: 12px;
}

.format-item strong {
  display: block;
  margin-bottom: 2px;
}

.format-item span {
  color: var(--noesis-color-text-muted);
}

.upload-preview {
  margin-top: 16px;
}

.upload-preview h4 {
  margin: 0 0 8px;
  font-size: 13px;
}

.upload-preview pre {
  margin: 0;
  max-height: 200px;
  overflow: auto;
  padding: 10px;
  font-size: 12px;
  line-height: 1.5;
  background: var(--noesis-color-bg-muted);
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-sm);
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 720px) {
  .config-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 1024px) {
  .kb-detail {
    padding: 12px var(--noesis-content-gutter-mobile) 16px;
  }

  .detail-header {
    flex-wrap: wrap;
    align-items: stretch;
  }

  .detail-header-main h1 {
    font-size: 18px;
    word-break: break-all;
  }

  .detail-header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .pipeline-hint {
    font-size: 11px;
    line-height: 1.45;
  }

  .tab-panel {
    padding: 10px 0 16px;
  }

  .doc-table-scroll {
    margin: 0 calc(-1 * var(--noesis-content-gutter-mobile));
    padding: 0 var(--noesis-content-gutter-mobile);
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  .detail-tabs--mobile :deep(.n-tabs-nav) {
    padding: 0 2px;
  }

  .format-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 480px) {
  .detail-header-actions :deep(.n-button .n-button__content) {
    font-size: 13px;
  }

  .detail-meta {
    gap: 4px;
  }
}
</style>
