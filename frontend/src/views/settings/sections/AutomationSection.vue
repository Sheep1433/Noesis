<script setup lang="ts">
import type { ScheduledTask, ScheduledTaskDraft, ScheduledTaskRun } from '@/api/settings'
import { NButton, NInput, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks,
  parseScheduledTask, previewSchedule, retryScheduledTaskRun, runScheduledTask, setScheduledTaskEnabled,
  updateScheduledTask,
} from '@/api/settings'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const saving = ref(false)
const parsing = ref(false)
const tasks = ref<ScheduledTask[]>([])
const editingId = ref<string | null>(null)
const preview = ref<{ summary: string, next_run_at: number } | null>(null)
const histories = reactive<Record<string, ScheduledTaskRun[]>>({})
const nlInput = ref('')

// 字段固定默认值：qa_type 恒为 SuperAgent、时区 Asia/Shanghai、单任务单会话、不投递。
const TIMEZONE = 'Asia/Shanghai'
const form = reactive({
  name: '',
  cron_expr: '0 9 * * 1',
  enabled: true,
  prompt: '',
})

// 直观时间选择器：频率 + 时间 + 星期几，watch 同步生成 cron_expr。
type Freq = 'daily' | 'weekly' | 'monthly'
const freq = ref<Freq>('weekly')
const atHour = ref(9)
const atMinute = ref(0)
const weekday = ref(1) // 1=周一 … 7=周日
const monthDay = ref(1)

function buildCron(): string {
  const hh = String(atHour.value).padStart(2, '0')
  const mm = String(atMinute.value).padStart(2, '0')
  switch (freq.value) {
    case 'daily': return `${mm} ${hh} * * *`
    case 'weekly': return `${mm} ${hh} * * ${weekday.value}`
    case 'monthly': return `${mm} ${hh} ${monthDay.value} * *`
  }
}

watch([freq, atHour, atMinute, weekday, monthDay], () => {
  form.cron_expr = buildCron()
}, { deep: true })

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
    preview.value = await previewSchedule(form.cron_expr, TIMEZONE)
  } catch (error) {
    preview.value = null
    message.warning(error instanceof Error ? error.message : '日程无效')
  }
}

async function onParse() {
  const text = nlInput.value.trim()
  if (!text) {
    return
  }
  parsing.value = true
  try {
    const draft: ScheduledTaskDraft = await parseScheduledTask(text)
    form.name = draft.name
    form.cron_expr = draft.cron_expr
    form.prompt = draft.prompt
    syncSelectorFromCron(draft.cron_expr)
    await refreshPreview()
    message.success('已解析，请确认后创建')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '解析失败')
  } finally {
    parsing.value = false
  }
}

// 把后端返回的 cron_expr 反解析到选择器（识别不了保持当前选择器值，cron_expr 仍以原文为准）。
function syncSelectorFromCron(cron: string) {
  const parts = cron.split(/\s+/)
  if (parts.length !== 5) {
    return
  }
  const [m, h, dom, , dow] = parts
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && dom === '*' && parts[3] === '*' && dow === '*') {
    freq.value = 'daily'
    atMinute.value = Number(m)
    atHour.value = Number(h)
    return
  }
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && dom === '*' && parts[3] === '*' && /^\d+$/.test(dow)) {
    freq.value = 'weekly'
    atMinute.value = Number(m)
    atHour.value = Number(h)
    weekday.value = Number(dow)
    return
  }
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && /^\d+$/.test(dom) && parts[3] === '*' && dow === '*') {
    freq.value = 'monthly'
    atMinute.value = Number(m)
    atHour.value = Number(h)
    monthDay.value = Number(dom)
  }
}

function resetForm() {
  editingId.value = null
  form.name = ''
  form.cron_expr = '0 9 * * 1'
  form.enabled = true
  form.prompt = ''
  freq.value = 'weekly'
  atHour.value = 9
  atMinute.value = 0
  weekday.value = 1
  preview.value = null
  nlInput.value = ''
}

function edit(task: ScheduledTask) {
  editingId.value = task.id
  form.name = task.name
  form.cron_expr = task.cron_expr
  form.enabled = task.enabled
  form.prompt = task.prompt
  syncSelectorFromCron(task.cron_expr)
  void refreshPreview()
}

const submitPayload = computed(() => ({
  name: form.name,
  cron_expr: form.cron_expr,
  timezone: TIMEZONE,
  enabled: form.enabled,
  qa_type: 'SUPER_AGENT_QA',
  prompt: form.prompt,
  session_binding: 'single',
  delivery: 'none',
}))

async function save() {
  saving.value = true
  try {
    await refreshPreview()
    if (!preview.value) {
      return
    }
    if (editingId.value) {
      await updateScheduledTask(editingId.value, submitPayload.value)
    } else {
      await createScheduledTask(submitPayload.value)
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
    // 立即运行异步派发，返回的 run 此时为 queued/running，后台执行完毕后更新 last_status。
    message.success(`已触发，状态：${result.run?.status || 'queued'}`)
    await refresh()
    await loadHistory(task)
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

const freqOptions = [
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
]
const weekdayOptions = [
  { label: '周一', value: 1 }, { label: '周二', value: 2 }, { label: '周三', value: 3 },
  { label: '周四', value: 4 }, { label: '周五', value: 5 }, { label: '周六', value: 6 }, { label: '周日', value: 7 },
]
const hourOptions = Array.from({ length: 24 }, (_, i) => ({ label: String(i).padStart(2, '0'), value: i }))
const minuteOptions = [0, 15, 30, 45].map((v) => ({ label: String(v).padStart(2, '0'), value: v }))

onMounted(() => void refresh())
</script>

<template>
  <SettingsSection title="自动化" description="用一句话描述任务，或选择时间手动配置。每次运行结果会落到一个固定会话，可像普通对话一样继续交互。">
    <SettingsStatus v-if="loading" title="正在加载">正在读取任务与最近状态…</SettingsStatus>

    <div class="nl-box">
      <n-input
        v-model:value="nlInput"
        type="textarea"
        placeholder="用一句话描述定时任务，如「每周一早上9点，收集网上资料整理 AI Agent 的最新进展」"
        :rows="2"
      />
      <n-button type="primary" :loading="parsing" :disabled="!nlInput.trim()" @click="onParse">解析为任务</n-button>
    </div>

    <div class="task-form">
      <n-input v-model:value="form.name" placeholder="任务名称" />
      <div class="schedule-row">
        <n-select v-model:value="freq" :options="freqOptions" style="width: 100px" />
        <n-select v-if="freq === 'weekly'" v-model:value="weekday" :options="weekdayOptions" style="width: 100px" />
        <n-select v-if="freq === 'monthly'" v-model:value="monthDay" :options="Array.from({ length: 28 }, (_, i) => ({ label: String(i + 1), value: i + 1 }))" style="width: 80px" />
        <n-select v-model:value="atHour" :options="hourOptions" style="width: 80px" />
        <span class="colon">:</span>
        <n-select v-model:value="atMinute" :options="minuteOptions" style="width: 80px" />
      </div>
      <div v-if="preview" class="preview">{{ preview.summary }} · 下次 {{ new Date(preview.next_run_at).toLocaleString() }}</div>
      <n-input v-model:value="form.prompt" type="textarea" placeholder="任务提示词" :rows="4" />
      <label class="enabled"><n-switch v-model:value="form.enabled" /> 启用任务</label>
      <div class="actions"><n-button type="primary" :loading="saving" :disabled="!form.name || !form.prompt" @click="save">{{ editingId ? '保存修改' : '创建任务' }}</n-button><n-button v-if="editingId" @click="resetForm">取消</n-button></div>
    </div>

    <SettingsEmptyState v-if="!loading && tasks.length === 0" title="暂无自动化任务" description="用一句话描述或选择时间配置后，每次执行都会保留运行记录并落到对应会话。" />
    <div v-for="task in tasks" :key="task.id" class="task-card">
      <div class="task-head"><div><strong>{{ task.name }}</strong><div class="muted">{{ task.cron_expr }} · {{ task.timezone }}</div></div><n-tag size="small" :type="task.enabled ? 'success' : 'default'">{{ task.enabled ? '启用' : '停用' }}</n-tag></div>
      <div class="muted prompt">{{ task.prompt }}</div>
      <div class="actions"><n-switch :value="task.enabled" @update:value="value => onToggle(task, value)" /><n-button size="small" @click="onRun(task)">立即运行</n-button><n-button size="small" @click="loadHistory(task)">运行历史</n-button><n-button size="small" @click="edit(task)">编辑</n-button><n-button size="small" type="error" quaternary @click="onDelete(task)">删除</n-button></div>
      <div v-if="histories[task.id]" class="history">
        <div v-for="run in histories[task.id]" :key="run.id" class="run-row">
          <div><n-tag size="small" :type="run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : 'warning'">{{ run.status }}</n-tag> <span class="muted">{{ new Date(run.created_at).toLocaleString() }} · {{ run.trigger_source }} · {{ run.duration_ms ?? '—' }} ms</span></div>
          <p v-if="run.result_summary">{{ run.result_summary }}</p><p v-if="run.error_message" class="error">{{ run.error_message }}</p>
          <n-button v-if="run.status === 'failed' || run.status === 'cancelled'" size="tiny" @click="retry(run, task)">重试</n-button>
        </div>
      </div>
    </div>
  </SettingsSection>
</template>

<style scoped>
.nl-box { display: grid; gap: 10px; max-width: 700px; padding-bottom: 18px; }
.task-form { display: grid; gap: 10px; max-width: 700px; padding-bottom: 22px; }
.schedule-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.colon { font-weight: 600; }
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
</style>
