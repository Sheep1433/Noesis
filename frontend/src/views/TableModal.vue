<script setup>
import * as GlobalAPI from '@/api'
import { qaTypeLabel } from '@/utils/qaType'

const props = defineProps({
  show: Boolean,
})

const emit = defineEmits(['update:show', 'delete'])
const { isMobile } = useBreakpoint()

const localShow = computed({
  get: () => props.show,
  set: (value) => emit('update:show', value),
})

const tableData = ref([])
const loading = ref(false)
const checkedRowKeys = ref([])

const columns = ref([
  { type: 'selection' },
  {
    title: '会话标题',
    key: 'title',
    ellipsis: { tooltip: true },
  },
  {
    title: '问答类型',
    key: 'qa_type',
    width: 120,
    ellipsis: { tooltip: true },
    render(row) {
      return qaTypeLabel(row.qa_type)
    },
  },
  {
    title: '更新时间',
    key: 'update_time',
    width: 168,
    render(row) {
      return formatTime(row.update_time)
    },
  },
  {
    title: '创建时间',
    key: 'create_time',
    width: 168,
    render(row) {
      return formatTime(row.create_time)
    },
  },
])

const pagination = ref({
  page: 1,
  pageSize: 8,
  total: 0,
  pageCount: 0,
})

const tableStyle = {
  'fontSize': '14px',
  '--n-td-color': 'var(--noesis-color-bg-elevated)',
  '--n-th-color': 'var(--noesis-color-bg-muted)',
  '--n-border-color': 'var(--noesis-color-border-subtle)',
  'fontFamily': `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`,
}

const modalTitle = computed(
  () => `管理对话记录 · 共${pagination.value.total}条`,
)

/** 后端 create_time / update_time 为 Unix 毫秒 BIGINT，接口 JSON 中为 number */
function formatTime(time) {
  if (time == null || time === '') {
    return '-'
  }
  const ms = typeof time === 'number'
    ? time
    : (typeof time === 'string' && /^\d+$/.test(time.trim())
        ? Number(time.trim())
        : Number.NaN)
  const t = Number.isFinite(ms) ? ms : Date.parse(String(time))
  if (!Number.isFinite(t)) {
    return '-'
  }
  return new Date(t).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function resetSelection() {
  checkedRowKeys.value = []
}

function resetModalState() {
  pagination.value.page = 1
  resetSelection()
}

async function fetchData() {
  loading.value = true
  try {
    const res = await GlobalAPI.query_user_qa_record(
      pagination.value.page,
      pagination.value.pageSize,
      null,
      null,
    )
    if (res.ok) {
      const data = await res.json()
      if (data?.data) {
        tableData.value = data.data.records || []
        pagination.value.total = data.data.total_count || 0
        pagination.value.pageCount = data.data.total_pages || 0
      } else {
        tableData.value = []
        pagination.value.total = 0
        pagination.value.pageCount = 0
      }
    } else {
      tableData.value = []
      pagination.value.total = 0
      pagination.value.pageCount = 0
    }
  } catch (error) {
    console.error('Error fetching data:', error)
    tableData.value = []
    pagination.value.total = 0
    pagination.value.pageCount = 0
  } finally {
    loading.value = false
  }
}

function close() {
  localShow.value = false
}

function handleAfterLeave() {
  resetModalState()
}

const rowKey = (row) => row.session_id ?? row.id ?? row.chat_id

function handleCheck(rowKeys) {
  checkedRowKeys.value = rowKeys
}

async function deleteSelectedData() {
  const ids = [...new Set(checkedRowKeys.value.filter(Boolean))]
  if (ids.length === 0) {
    return
  }
  const res = await GlobalAPI.delete_user_record(ids)
  if (res.ok) {
    resetSelection()
    fetchData()
    try {
      const data = await res.json()
      window.$ModalMessage?.success(data.msg ?? '删除成功')
    } catch {
      window.$ModalMessage?.success('删除成功')
    }
  } else {
    let msg = '删除失败'
    try {
      const data = await res.json()
      msg = data.msg ?? msg
    } catch {
      // ignore parse errors
    }
    window.$ModalMessage?.error(msg)
    resetSelection()
    fetchData()
  }
}

function handlePageChange(page) {
  pagination.value.page = page
  fetchData()
}

function handlePageSizeChange(pageSize) {
  pagination.value.pageSize = pageSize
  pagination.value.page = 1
  fetchData()
}

watch(
  () => props.show,
  (open) => {
    if (open) {
      resetModalState()
      fetchData()
    }
  },
)
</script>

<template>
  <n-modal
    v-model:show="localShow"
    to="body"
    display-directive="if"
    :mask-closable="false"
    :auto-focus="false"
    preset="card"
    :title="modalTitle"
    class="session-manage-modal"
    :style="{ width: 'min(1100px, calc(100vw - 48px))' }"
    @after-leave="handleAfterLeave"
  >
    <n-spin :show="loading" class="session-manage-modal__body">
      <n-data-table
        striped
        size="small"
        :data="tableData"
        :columns="columns"
        :row-key="rowKey"
        :checked-row-keys="checkedRowKeys"
        :max-height="420"
        :style="tableStyle"
        @update:checked-row-keys="handleCheck"
      />
    </n-spin>

    <template #footer>
      <div class="session-manage-modal__footer">
        <n-pagination
          :page="pagination.page"
          :page-size="pagination.pageSize"
          :page-count="pagination.pageCount"
          :item-count="pagination.total"
          :show-size-picker="!isMobile"
          :page-sizes="[8, 16, 24, 32]"
          @update:page="handlePageChange"
          @update:page-size="handlePageSizeChange"
        />
        <div class="session-manage-modal__actions">
          <n-button @click="close">
            取消
          </n-button>
          <n-button
            type="error"
            :disabled="checkedRowKeys.length === 0"
            @click="deleteSelectedData"
          >
            删除所选
          </n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.session-manage-modal__body {
  min-height: 200px;
}

.session-manage-modal__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.session-manage-modal__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

@media (width <= 720px) {
  .session-manage-modal__footer {
    flex-direction: column;
    align-items: stretch;
  }

  .session-manage-modal__footer :deep(.n-pagination) {
    justify-content: center;
  }

  .session-manage-modal__actions {
    justify-content: center;
  }
}
</style>

<style lang="scss">
.session-manage-modal.n-modal {
  .n-card {
    overflow: hidden;
  }

  .n-card__content {
    padding-top: 8px;
  }

  .n-card__footer {
    padding: 12px 16px;
    background: var(--noesis-color-bg-muted);
    border-top: 1px solid var(--noesis-color-border-subtle);
  }
}
</style>
