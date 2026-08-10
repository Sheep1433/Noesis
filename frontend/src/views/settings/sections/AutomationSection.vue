<script setup lang="ts">
import type { ScheduledTask, ScheduledTaskRun } from '@/api/settings'
import { NButton, NInput, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { onMounted, reactive, ref } from 'vue'
import {
  createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks,
  previewSchedule, retryScheduledTaskRun, runScheduledTask, setScheduledTaskEnabled,
  updateScheduledTask,
} from '@/api/settings'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const saving = ref(false)
const tasks = ref<ScheduledTask[]>([])
const editingId = ref<string | null>(null)
const preview = ref<{ summary: string, next_run_at: number } | null>(null)
const histories = reactive<Record<string, ScheduledTaskRun[]>>({})
const form = reactive({
  name: '', cron_expr: '0 9 * * *', timezone: 'Asia/Shanghai', enabled: true,
  qa_type: 'SUPER_AGENT_QA', prompt: '', session_binding: 'none', delivery: 'none',
})

const qaOptions = ['SUPER_AGENT_QA', 'COMMON_QA', 'FAULT_OPERATION_QA', 'TEST_CASE_QA'].map((value) => ({ label: value, value }))
const timezoneOptions = ['Asia/Shanghai', 'UTC', 'America/New_York', 'Europe/London'].map((value) => ({ label: value, value }))

async function refresh() {
  loading.value = true
  try {
    tasks.value = await listScheduledTasks()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function refreshPreview() {
  try {
    preview.value = await previewSchedule(form.cron_expr, form.timezone)
  } catch (error) {
    preview.value = null
    message.warning(error instanceof Error ? error.message : '日程无效')
  }
}

function resetForm() {
  editingId.value = null
  Object.assign(form, { name: '', cron_expr: '0 9 * * *', timezone: 'Asia/Shanghai', enabled: true, qa_type: 'SUPER_AGENT_QA', prompt: '', session_binding: 'none', delivery: 'none' })
  preview.value = null
}

function edit(task: ScheduledTask) {
  editingId.value = task.id
  Object.assign(form, task)
  void refreshPreview()
}

async function save() {
  saving.value = true
  try {
    await refreshPreview()
    if (!preview.value) {
      return
    }
    if (editingId.value) {
      await updateScheduledTask(editingId.value, form)
    } else {
      await createScheduledTask(form)
    }
    message.success(editingId.value ? '任务已更新' : '任务已创建')
    resetForm()
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function onToggle(task: ScheduledTask, enabled: boolean) {
  try {
    await setScheduledTaskEnabled(task.id, enabled)
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '更新失败')
  }
}

async function onRun(task: ScheduledTask) {
  try {
    const result = await runScheduledTask(task.id)
    message.success(`已完成：${result.run?.status || result.last_status}`)
    await Promise.all([refresh(), loadHistory(task)])
  } catch (error) {
    message.error(error instanceof Error ? error.message : '触发失败')
  }
}

function onDelete(task: ScheduledTask) {
  dialog.warning({ title: `删除 ${task.name}？`, content: '任务定义会停用并隐藏，已有运行历史仍会保留。', positiveText: '删除', negativeText: '取消',
    async onPositiveClick() {
      await deleteScheduledTask(task.id)
      message.success('已删除')
      await refresh()
    } })
}

async function loadHistory(task: ScheduledTask) {
  try {
    histories[task.id] = (await listScheduledTaskRuns(task.id)).items
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载历史失败')
  }
}

async function retry(run: ScheduledTaskRun, task: ScheduledTask) {
  try {
    await retryScheduledTaskRun(run.id)
    message.success('重试已完成')
    await Promise.all([refresh(), loadHistory(task)])
  } catch (error) {
    message.error(error instanceof Error ? error.message : '重试失败')
  }
}

onMounted(() => void refresh())
</script>

<template>
  <SettingsSection title="自动化" description="配置日程、Agent、会话策略与投递目标，并查看每次运行结果。">
    <SettingsStatus v-if="loading" title="正在加载">正在读取任务与最近状态…</SettingsStatus>
    <div class="task-form">
      <n-input v-model:value="form.name" placeholder="任务名称" />
      <div class="two-columns"><n-input v-model:value="form.cron_expr" placeholder="cron，如 0 9 * * *" @blur="refreshPreview" /><n-select v-model:value="form.timezone" :options="timezoneOptions" @update:value="refreshPreview" /></div>
      <div v-if="preview" class="preview">{{ preview.summary }} · 下次 {{ new Date(preview.next_run_at).toLocaleString() }}</div>
      <n-select v-model:value="form.qa_type" :options="qaOptions" />
      <n-input v-model:value="form.prompt" type="textarea" placeholder="任务提示词" :rows="4" />
      <div class="two-columns"><n-input v-model:value="form.session_binding" placeholder="none 或 session:{id}" /><n-input v-model:value="form.delivery" placeholder="none / web_notify / channel:{id}" /></div>
      <label class="enabled"><n-switch v-model:value="form.enabled" /> 启用任务</label>
      <div class="actions"><n-button type="primary" :loading="saving" :disabled="!form.name || !form.prompt" @click="save">{{ editingId ? '保存修改' : '创建任务' }}</n-button><n-button v-if="editingId" @click="resetForm">取消</n-button><n-button @click="refreshPreview">预览日程</n-button></div>
    </div>

    <SettingsEmptyState v-if="!loading && tasks.length === 0" title="暂无自动化任务" description="创建任务后，每次执行都会保留独立运行记录。" />
    <div v-for="task in tasks" :key="task.id" class="task-card">
      <div class="task-head"><div><strong>{{ task.name }}</strong><div class="muted">{{ task.cron_expr }} · {{ task.timezone }} · {{ task.qa_type }}</div></div><n-tag size="small" :type="task.enabled ? 'success' : 'default'">{{ task.enabled ? '启用' : '停用' }}</n-tag></div>
      <div class="muted prompt">{{ task.prompt }}</div>
      <div class="actions"><n-switch :value="task.enabled" @update:value="value => onToggle(task, value)" /><n-button size="small" @click="onRun(task)">立即运行</n-button><n-button size="small" @click="loadHistory(task)">运行历史</n-button><n-button size="small" @click="edit(task)">编辑</n-button><n-button size="small" type="error" quaternary @click="onDelete(task)">删除</n-button></div>
      <div v-if="histories[task.id]" class="history">
        <div v-for="run in histories[task.id]" :key="run.id" class="run-row">
          <div><n-tag size="small" :type="run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : 'warning'">{{ run.status }}</n-tag> <span class="muted">{{ new Date(run.created_at).toLocaleString() }} · {{ run.trigger_source }} · {{ run.duration_ms ?? '—' }} ms</span></div>
          <p v-if="run.result_summary">{{ run.result_summary }}</p><p v-if="run.error_message" class="error">{{ run.error_message }}</p>
          <div class="muted">投递：{{ run.delivery_result?.status || '—' }} <span v-if="run.session_id">· 会话 {{ run.session_id }}</span></div>
          <n-button v-if="run.status === 'failed' || run.status === 'cancelled'" size="tiny" @click="retry(run, task)">重试</n-button>
        </div>
      </div>
    </div>
  </SettingsSection>
</template>

<style scoped>
.task-form { display: grid; gap: 10px; max-width: 700px; padding-bottom: 22px; }
.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.preview { color: var(--noesis-color-success, #287a45); font-size: 12px; }
.enabled, .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.task-card { padding: 16px 0; border-top: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.task-head { display: flex; justify-content: space-between; gap: 12px; }
.muted { color: var(--noesis-color-text-secondary); font-size: 12px; }
.prompt { margin: 8px 0; white-space: pre-wrap; }
.history { margin-top: 12px; padding: 4px 12px; border-radius: 8px; background: var(--noesis-color-bg-muted, rgba(0,0,0,.03)); }
.run-row { padding: 10px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.06)); }
.run-row p { margin: 6px 0; font-size: 12px; }
.error { color: var(--noesis-color-danger, #c2413b); }
@media (max-width: 640px) { .two-columns { grid-template-columns: 1fr; } }
</style>
