<script setup>
import * as GlobalAPI from '@/api'
import { qaTypeLabel } from '@/utils/qaType'

const props = defineProps({
  show: Boolean,
})

const emit = defineEmits(['update:show', 'delete'])

const localShow = ref(props.show)
const tableData = ref([])

function qaTypeLabelLocal(qt) {
  return qaTypeLabel(qt)
}

const columns = ref([
  {
    type: 'selection',
  },
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
      return qaTypeLabelLocal(row.qa_type)
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
const loading = ref(false)
const checkedRowKeys = ref([])

// 分页配置
const pagination = ref({
  page: 1,
  pageSize: 8,
  total: 0, // 总记录数
  pageCount: 0, // 总页数
  onChange: (page) => handlePageChange(page),
  onUpdatePageSize: (pageSize) => handlePageSizeChange(pageSize),
})

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
      if (data && data.data) {
        tableData.value = data.data.records || []
        pagination.value.total = data.data.total_count || 0
        pagination.value.pageCount = data.data.total_pages || 0
      } else {
        console.error('Unexpected data format:', data)
        tableData.value = []
        pagination.value.total = 0
        pagination.value.pageCount = 0
      }
    } else {
      console.error('API request failed with status:', res.status)
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
  emit('update:show', false)
  pagination.value.page = 1
  resetSelection()
}

function resetSelection() {
  checkedRowKeys.value = []
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

function handlePageSizeChange(newPageSize) {
  pagination.value.pageSize = newPageSize
  pagination.value.page = 1 // 重置到第一页
  fetchData()
}

const modalTitle = computed(
  () => `管理对话记录 · 共${pagination.value.total}条`,
)

watch(
  () => props.show,
  (newVal) => {
    if (newVal !== localShow.value) {
      localShow.value = newVal
      if (newVal) {
        resetSelection()
        fetchData()
      }
    }
  },
)

const tableRef = useTemplateRef('tableRef')
</script>

<template>
  <n-modal
    v-model:show="localShow"
    :mask-closable="false"
    :on-after-leave="close"
    preset="card"
    :title="modalTitle"
    class="w-1100 h-600 flex flex-col"
  >
    <div
      class="modal-content"
      style="flex: 1; display: flex; flex-direction: column"
    >
      <n-spin :show="loading" style="flex: 1; overflow: auto">
        <n-data-table
          ref="tableRef"
          :data="tableData"
          :columns="columns"
          :row-key="rowKey"
          :checked-row-keys="checkedRowKeys"
          style="height: 100%; width: 100%"
          :style="{
            'font-size': `15px`,
            '--n-td-color': `#ffffff`,
            'font-family': `-apple-system, BlinkMacSystemFont,'Segoe UI', Roboto, 'Helvetica Neue', Arial,sans-serif`,
          }"
          @update:checked-row-keys="handleCheck"
        />
      </n-spin>
      <div
        class="footer"
        style="
display: flex;
justify-content: space-between;
align-items: center;
padding: 10px;
background-color: var(--n-modal-footer-bg);
border-top: 1px solid var(--n-modal-border-color);
          "
      >
        <n-pagination
          v-model:page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :page-count="pagination.pageCount"
          :page-size="pagination.pageSize"
          @update:page="handlePageChange"
          @update:page-size="handlePageSizeChange"
        />
        <div>
          <n-button style="margin-right: 10px" @click="close">
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
    </div>
  </n-modal>
</template>

<style scoped>
.modal-content {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.footer {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  padding: 10px;
  background-color: var(--n-modal-footer-bg);
  border-top: 1px solid var(--n-modal-border-color);
  justify-content: flex-end;
}

/* 确保分页组件在新的一行 */

.n-pagination {
  margin-top: 10px;
}
</style>
