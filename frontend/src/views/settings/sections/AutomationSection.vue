<script setup lang="ts">
import type { ScheduledTask } from '@/api/settings'
import { NButton, NInput, NSwitch, NTag, useMessage } from 'naive-ui'
import { onMounted, ref } from 'vue'
import {
  createScheduledTask,
  deleteScheduledTask,
  listScheduledTasks,
  runScheduledTask,

  setScheduledTaskEnabled,
} from '@/api/settings'

const message = useMessage()
const loading = ref(false)
const tasks = ref<ScheduledTask[]>([])
const name = ref('')
const cronExpr = ref('0 9 * * *')
const prompt = ref('')

async function refresh() {
  loading.value = true
  try {
    tasks.value = await listScheduledTasks()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  try {
    await createScheduledTask({
      name: name.value || '定时任务',
      cron_expr: cronExpr.value,
      prompt: prompt.value,
      enabled: true,
      qa_type: 'SUPER_AGENT_QA',
      timezone: 'Asia/Shanghai',
      session_binding: 'none',
      delivery: 'none',
    })
    name.value = ''
    prompt.value = ''
    message.success('已创建')
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '创建失败')
  }
}

async function onToggle(task: ScheduledTask, enabled: boolean) {
  try {
    await setScheduledTaskEnabled(task.id, enabled)
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function onRun(task: ScheduledTask) {
  try {
    await runScheduledTask(task.id)
    message.success('已触发')
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '触发失败')
  }
}

async function onDelete(task: ScheduledTask) {
  try {
    await deleteScheduledTask(task.id)
    message.success('已删除')
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '删除失败')
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <section class="pane">
    <h2>自动化</h2>
    <p class="hint">
      用 cron 定时跑 Agent（默认 isolated，不写入主聊天时间线）。
    </p>

    <div class="form">
      <n-input v-model:value="name" placeholder="任务名称" />
      <n-input v-model:value="cronExpr" placeholder="cron，如 0 9 * * *" />
      <n-input
        v-model:value="prompt"
        type="textarea"
        placeholder="提示词"
        :rows="3"
      />
      <n-button type="primary" @click="onCreate">
        创建任务
      </n-button>
    </div>

    <ul class="list">
      <li v-for="task in tasks" :key="task.id" class="row">
        <div class="meta">
          <strong>{{ task.name }}</strong>
          <span class="mono">{{ task.cron_expr }}</span>
          <n-tag size="small" :type="task.enabled ? 'success' : 'default'">
            {{ task.enabled ? '启用' : '停用' }}
          </n-tag>
          <span v-if="task.last_status" class="muted">上次：{{ task.last_status }}</span>
        </div>
        <div class="actions">
          <n-switch
            :value="task.enabled"
            @update:value="(v) => onToggle(task, v)"
          />
          <n-button size="small" @click="onRun(task)">
            立即运行
          </n-button>
          <n-button size="small" quaternary type="error" @click="onDelete(task)">
            删除
          </n-button>
        </div>
      </li>
      <li v-if="!loading && tasks.length === 0" class="empty">
        暂无定时任务
      </li>
    </ul>
  </section>
</template>

<style scoped>
.pane h2 {
  margin: 0 0 8px;
  font-size: 18px;
  color: var(--noesis-color-text-heading);
}
.hint {
  margin: 0 0 16px;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 520px;
  margin-bottom: 20px;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 0;
  border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0, 0, 0, 0.08));
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.muted {
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}
.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.empty {
  color: var(--noesis-color-text-secondary);
}
</style>
