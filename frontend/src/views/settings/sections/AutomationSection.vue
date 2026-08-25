<script setup lang="ts">
import type { ScheduledTask, ScheduledTaskDraft, ScheduledTaskRun } from '@/api/settings'
import { NButton, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks,
  parseScheduledTask, previewSchedule, retryScheduledTaskRun, runScheduledTask,
  setScheduledTaskEnabled, updateScheduledTask,
} from '@/api/settings'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { SettingsEmptyState, SettingsSection, SettingsStatus } from '../primitives'
import AutomationTaskForm from './AutomationTaskForm.vue'

const message = useMessage()
const dialog = useDialog()
const { isMobile } = useBreakpoint()
const loading = ref(false)
const saving = ref(false)
const parsing = ref(false)
const tasks = ref<ScheduledTask[]>([])
const editingId = ref<string | null>(null)
const editingDelivery = ref('none')
const preview = ref<{ summary: string, next_run_at: number } | null>(null)
const histories = reactive<Record<string, ScheduledTaskRun[]>>({})
const nlInput = ref('')
const formDrawerOpen = ref(false)

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
  formDrawerOpen.value = false
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
  if (isMobile.value) {
    formDrawerOpen.value = true
  }
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
        <AutomationTaskForm
          v-if="!isMobile"
          v-model:nl-input="nlInput"
          v-model:name="form.name"
          v-model:repeat-mode="repeatMode"
          v-model:weekdays="weekdays"
          v-model:month-day="monthDay"
          v-model:custom-cron="customCron"
          v-model:repeat-time="repeatTime"
          v-model:enabled="form.enabled"
          v-model:prompt="form.prompt"
          :editing-id="editingId"
          :parsing="parsing"
          :saving="saving"
          :preview="preview"
          :repeat-mode-options="repeatModeOptions"
          :weekday-options="weekdayOptions"
          @parse="onParse"
          @save="save"
          @cancel="resetForm"
        />
        <button v-else type="button" class="automation-create-button" @click="formDrawerOpen = true">
          <span class="automation-create-button__plus">＋</span>
          <span>
            <strong>{{ editingId ? '继续编辑任务' : '新建自动化任务' }}</strong>
            <small>配置运行时间和任务提示词</small>
          </span>
          <span class="automation-create-button__arrow">›</span>
        </button>
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

    <n-drawer
      v-if="isMobile"
      v-model:show="formDrawerOpen"
      placement="bottom"
      width="100%"
      height="min(92vh, 760px)"
      :block-scroll="true"
    >
      <n-drawer-content
        :title="editingId ? '编辑自动化任务' : '新建自动化任务'"
        closable
        body-content-style="padding: 0 16px max(16px, env(safe-area-inset-bottom)); overflow-y: auto;"
      >
        <AutomationTaskForm
          v-model:nl-input="nlInput"
          v-model:name="form.name"
          v-model:repeat-mode="repeatMode"
          v-model:weekdays="weekdays"
          v-model:month-day="monthDay"
          v-model:custom-cron="customCron"
          v-model:repeat-time="repeatTime"
          v-model:enabled="form.enabled"
          v-model:prompt="form.prompt"
          :editing-id="editingId"
          :parsing="parsing"
          :saving="saving"
          :preview="preview"
          :repeat-mode-options="repeatModeOptions"
          :weekday-options="weekdayOptions"
          @parse="onParse"
          @save="save"
          @cancel="resetForm"
        />
      </n-drawer-content>
    </n-drawer>
  </SettingsSection>
</template>

<style scoped>
/* 在实际可用宽度足够时并列展示，避免被侧栏或窗口宽度误判。 */

.automation-section {
  width: 100%;
  max-width: none;
}

.automation-layout {
  display: grid;
  gap: 24px 40px;
  align-items: start;
}

.automation-form,
.automation-list {
  min-width: 0;
}

@container settings-content (min-width: 960px) {

  .automation-layout {
    grid-template-columns: minmax(0, 1.15fr) minmax(380px, 0.85fr);
    grid-template-areas: "list form";
  }

  .automation-list {
    grid-area: list;
  }

  .automation-form {
    grid-area: form;
  }

  .automation-form :deep(.automation-form-panel) {
    position: sticky;
    top: 16px;
  }
}

.automation-list-header h3 {
  margin: 0;
  color: var(--noesis-color-text-heading);
  font-size: 16px;
  font-weight: 650;
}

.automation-list-header p {
  margin: 5px 0 0;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.automation-list-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.automation-list-count {
  flex-shrink: 0;
  color: var(--noesis-color-text-muted);
  font-size: 12px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.automation-create-button {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 16px;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-lg);
  color: var(--noesis-color-text-heading);
  background: var(--noesis-color-bg-elevated);
  text-align: left;
  cursor: pointer;
}

.automation-create-button:hover {
  border-color: var(--noesis-color-primary);
}

.automation-create-button__plus {
  color: var(--noesis-color-primary);
  font-size: 22px;
  line-height: 1;
}

.automation-create-button > span:nth-child(2) {
  display: grid;
  flex: 1;
  gap: 3px;
}

.automation-create-button small {
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.automation-create-button__arrow {
  color: var(--noesis-color-text-muted);
  font-size: 22px;
  line-height: 1;
}

.task-card {
  padding: 16px;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-lg);
  background: var(--noesis-color-bg-elevated);
}

.task-card + .task-card {
  margin-top: 10px;
}

.task-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.muted {
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.prompt {
  margin: 8px 0;
  white-space: pre-wrap;
}

.history {
  margin-top: 12px;
  padding: 4px 12px;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-muted);
}

.run-row {
  padding: 10px 0;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
}

.run-row p {
  margin: 6px 0;
  font-size: 12px;
}

.error {
  color: var(--noesis-color-danger);
}
</style>
