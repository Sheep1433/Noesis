<script setup lang="ts">
import type { ScheduledTask, ScheduledTaskDraft, ScheduledTaskRun } from '@/api/settings'
import { NButton, NInput, NInputNumber, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks,
  parseScheduledTask, previewSchedule, retryScheduledTaskRun, runScheduledTask,
  setScheduledTaskEnabled, updateScheduledTask,
} from '@/api/settings'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const saving = ref(false)
const parsing = ref(false)
const tasks = ref<ScheduledTask[]>([])
const editingId = ref<string | null>(null)
const editingDelivery = ref('none')
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

type RepeatMode = 'daily' | 'weekly' | 'monthly' | 'custom'
const repeatMode = ref<RepeatMode>('weekly')
const repeatTime = ref('09:00')
const weekdays = ref<number[]>([1]) // cron: 1=周一 … 7=周日
const monthDay = ref(1)
const customCron = ref('0 9 * * 1')

function buildCron(): string {
  if (repeatMode.value === 'custom') {
    return customCron.value.trim()
  }
  const [hour, minute] = repeatTime.value.split(':').map(Number)
  const hh = String(Number.isFinite(hour) ? hour : 9)
  const mm = String(Number.isFinite(minute) ? minute : 0)
  switch (repeatMode.value) {
    case 'daily': return `${mm} ${hh} * * *`
    case 'weekly': return `${mm} ${hh} * * ${[...weekdays.value].sort((a, b) => a - b).join(',') || '1'}`
    case 'monthly': return `${mm} ${hh} ${monthDay.value} * *`
    default: return customCron.value.trim()
  }
}

watch([repeatMode, repeatTime, weekdays, monthDay, customCron], () => {
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
  if (/^\d+$/.test(m) && /^\d+$/.test(h)) {
    repeatTime.value = `${String(Number(h)).padStart(2, '0')}:${String(Number(m)).padStart(2, '0')}`
  }
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && dom === '*' && parts[3] === '*' && dow === '*') {
    repeatMode.value = 'daily'
    return
  }
  const weeklyDays = dow.split(',')
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && dom === '*' && parts[3] === '*' && weeklyDays.length > 0 && weeklyDays.every((day) => /^[0-7]$/.test(day))) {
    repeatMode.value = 'weekly'
    weekdays.value = dow.split(',').map(Number).map((day) => day === 0 ? 7 : day)
    return
  }
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && /^\d+$/.test(dom) && parts[3] === '*' && dow === '*') {
    repeatMode.value = 'monthly'
    monthDay.value = Number(dom)
    return
  }
  repeatMode.value = 'custom'
  customCron.value = cron
}

function resetForm() {
  editingId.value = null
  form.name = ''
  form.cron_expr = '0 9 * * 1'
  form.enabled = true
  form.prompt = ''
  repeatMode.value = 'weekly'
  repeatTime.value = '09:00'
  weekdays.value = [1]
  monthDay.value = 1
  customCron.value = '0 9 * * 1'
  editingDelivery.value = 'none'
  preview.value = null
  nlInput.value = ''
}

function edit(task: ScheduledTask) {
  editingId.value = task.id
  form.name = task.name
  form.cron_expr = task.cron_expr
  form.enabled = task.enabled
  form.prompt = task.prompt
  editingDelivery.value = task.delivery
  syncSelectorFromCron(task.cron_expr)
  void refreshPreview()
}

const submitPayload = computed(() => ({
  name: form.name,
  cron_expr: buildCron(),
  timezone: TIMEZONE,
  enabled: form.enabled,
  qa_type: 'SUPER_AGENT_QA',
  prompt: form.prompt,
  session_binding: 'single',
  delivery: editingId.value ? editingDelivery.value : 'none',
}))

async function save() {
  saving.value = true
  try {
    form.cron_expr = buildCron()
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

const repeatModeOptions = [
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
  { label: '自定义', value: 'custom' },
]
const weekdayOptions = [
  { label: '周一', value: 1 }, { label: '周二', value: 2 }, { label: '周三', value: 3 },
  { label: '周四', value: 4 }, { label: '周五', value: 5 }, { label: '周六', value: 6 }, { label: '周日', value: 7 },
]
onMounted(() => void refresh())
</script>

<template>
  <SettingsSection class="automation-section" title="自动化" description="用一句话描述任务，或选择时间手动配置。每次运行结果会落到一个固定会话，可像普通对话一样继续交互。">
    <SettingsStatus v-if="loading" title="正在加载">正在读取任务与最近状态…</SettingsStatus>

    <div class="automation-layout">
      <!-- 右列（桌面）/ 首段（移动）：任务设置表单 -->
      <div class="automation-form">
        <div class="automation-form-panel">
          <header class="automation-form-header">
            <div>
              <h3>{{ editingId ? '编辑自动化任务' : '新建自动化任务' }}</h3>
              <p>先用自然语言生成，也可以直接配置重复方式和执行时间。</p>
            </div>
          </header>
          <section class="automation-form-block automation-form-block--natural">
            <h4>自然语言创建</h4>
            <div class="nl-box">
              <n-input
                v-model:value="nlInput"
                type="textarea"
                placeholder="例如：每周一早上 9 点，整理 AI Agent 的最新进展"
                :rows="2"
              />
              <n-button type="primary" :loading="parsing" :disabled="!nlInput.trim()" @click="onParse">解析为任务</n-button>
            </div>
          </section>

          <section class="automation-form-block">
            <h4>时间与任务内容</h4>
            <div class="task-form">
              <n-input v-model:value="form.name" placeholder="任务名称" />
              <div class="schedule-panel">
                <div class="schedule-row">
                  <span class="schedule-label">重复</span>
                  <n-select v-model:value="repeatMode" :options="repeatModeOptions" />
                </div>
                <div v-if="repeatMode === 'weekly'" class="schedule-row">
                  <span class="schedule-label">星期</span>
                  <n-select
                    v-model:value="weekdays"
                    multiple
                    :options="weekdayOptions"
                    max-tag-count="responsive"
                    placeholder="选择星期"
                  />
                </div>
                <div v-if="repeatMode === 'monthly'" class="schedule-row">
                  <span class="schedule-label">日期</span>
                  <n-input-number v-model:value="monthDay" :min="1" :max="28" />
                  <span class="schedule-suffix">日</span>
                </div>
                <div v-if="repeatMode === 'custom'" class="custom-cron-row">
                  <span class="schedule-label">Cron</span>
                  <n-input v-model:value="customCron" placeholder="分 时 日 月 周，例如 0 9 * * 1,3,5" />
                </div>
                <div class="schedule-row">
                  <span class="schedule-label">时间</span>
                  <input v-model="repeatTime" class="time-input" type="time">
                </div>
                <label class="enabled"><n-switch v-model:value="form.enabled" /> 启用</label>
              </div>
              <div v-if="preview" class="preview">{{ preview.summary }} · 下次 {{ new Date(preview.next_run_at).toLocaleString() }}</div>
              <n-input v-model:value="form.prompt" type="textarea" placeholder="任务提示词" :rows="4" />
              <div class="actions"><n-button type="primary" :loading="saving" :disabled="!form.name || !form.prompt" @click="save">{{ editingId ? '保存修改' : '创建任务' }}</n-button><n-button v-if="editingId" @click="resetForm">取消</n-button></div>
            </div>
          </section>
        </div>
      </div>

      <!-- 左列（桌面）/ 次段（移动）：任务列表 -->
      <div class="automation-list">
        <header class="automation-list-header">
          <div>
            <h3>已配置任务</h3>
            <p>管理已创建的任务，并查看最近运行情况。</p>
          </div>
          <span v-if="tasks.length" class="automation-list-count">{{ tasks.length }} 个</span>
        </header>
        <SettingsEmptyState v-if="!loading && tasks.length === 0" title="暂无自动化任务" description="用一句话描述或选择时间配置后，每次执行都会保留运行记录并落到对应会话。" />
        <div v-for="task in tasks" :key="task.id" class="task-card">
          <div class="task-head"><div><strong>{{ task.name }}</strong><div class="muted">{{ task.summary || task.cron_expr }} · {{ task.timezone }}</div></div><n-tag size="small" :type="task.enabled ? 'success' : 'default'">{{ task.enabled ? '启用' : '停用' }}</n-tag></div>
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
      </div>
    </div>
  </SettingsSection>
</template>

<style scoped>
/* 两列布局：桌面左侧任务列表、右侧设置表单；窄屏单列展示。 */
.automation-section { max-width: 1200px; }
.automation-layout { display: grid; gap: 24px 40px; align-items: start; }
.automation-form { min-width: 0; }
.automation-list { min-width: 0; }
@media (min-width: $bp-lg) {
  .automation-layout {
    grid-template-columns: minmax(0, 1.1fr) minmax(360px, 0.9fr);
    grid-template-areas: "list form";
  }
  .automation-list { grid-area: list; }
  .automation-form { grid-area: form; }
}

.automation-form-panel { padding: 18px; border: 1px solid var(--noesis-color-border-subtle); border-radius: 14px; background: var(--noesis-color-bg-elevated); }
.automation-form-header { margin-bottom: 18px; }
.automation-form-header h3, .automation-list-header h3 { margin: 0; color: var(--noesis-color-text-heading); font-size: 16px; font-weight: 650; }
.automation-form-header p, .automation-list-header p { margin: 5px 0 0; color: var(--noesis-color-text-secondary); font-size: 12px; line-height: 1.5; }
.automation-form-block + .automation-form-block { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--noesis-color-border-subtle); }
.automation-form-block h4 { margin: 0 0 10px; color: var(--noesis-color-text-heading); font-size: 13px; font-weight: 600; }
.automation-list-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.automation-list-count { flex-shrink: 0; color: var(--noesis-color-text-muted); font-size: 12px; }
.nl-box { display: grid; gap: 10px; padding-bottom: 0; }
.task-form { display: grid; gap: 10px; padding-bottom: 0; }
.schedule-panel { display: grid; gap: 0; max-width: 700px; border: 1px solid var(--noesis-color-border-subtle); border-radius: 14px; overflow: hidden; }
.schedule-row, .custom-cron-row { display: grid; grid-template-columns: 72px minmax(0, 1fr); align-items: center; gap: 12px; min-height: 52px; padding: 0 14px; border-bottom: 1px solid var(--noesis-color-border-subtle); }
.schedule-row :deep(.n-select), .custom-cron-row :deep(.n-input) { min-width: 0; }
.schedule-label { color: var(--noesis-color-text-secondary); font-size: 13px; }
.schedule-suffix { color: var(--noesis-color-text-secondary); font-size: 13px; }
.time-input { width: 120px; box-sizing: border-box; padding: 7px 10px; border: 1px solid var(--noesis-color-border); border-radius: 6px; color: var(--noesis-color-text-primary); background: var(--noesis-color-bg-elevated); font: inherit; }
.enabled { display: flex; align-items: center; gap: 8px; min-height: 52px; padding: 0 14px; color: var(--noesis-color-text-primary); }
.preview { color: var(--noesis-color-success, #287a45); font-size: 12px; }
.actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.task-card { padding: 16px; border: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); border-radius: 12px; background: var(--noesis-color-bg-elevated); }
.task-card + .task-card { margin-top: 10px; }
.task-head { display: flex; justify-content: space-between; gap: 12px; }
.muted { color: var(--noesis-color-text-secondary); font-size: 12px; }
.prompt { margin: 8px 0; white-space: pre-wrap; }
.history { margin-top: 12px; padding: 4px 12px; border-radius: 8px; background: var(--noesis-color-bg-muted, rgba(0,0,0,.03)); }
.run-row { padding: 10px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.06)); }
.run-row p { margin: 6px 0; font-size: 12px; }
.error { color: var(--noesis-color-danger, #c2413b); }
</style>
