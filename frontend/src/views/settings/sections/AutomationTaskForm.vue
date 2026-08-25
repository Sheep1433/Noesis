<script setup lang="ts">
import { NButton, NInput, NInputNumber, NSelect, NSwitch } from 'naive-ui'

type RepeatMode = 'daily' | 'weekly' | 'monthly' | 'custom'

const props = defineProps<{
  editingId: string | null
  parsing: boolean
  saving: boolean
  preview: { summary: string, next_run_at: number } | null
  repeatModeOptions: Array<{ label: string, value: RepeatMode }>
  weekdayOptions: Array<{ label: string, value: number }>
}>()

const emit = defineEmits<{
  parse: []
  save: []
  cancel: []
}>()

const nlInput = defineModel<string>('nlInput', { default: '' })
const name = defineModel<string>('name', { default: '' })
const repeatMode = defineModel<RepeatMode>('repeatMode', { default: 'weekly' })
const weekdays = defineModel<number[]>('weekdays', { default: () => [1] })
const monthDay = defineModel<number>('monthDay', { default: 1 })
const customCron = defineModel<string>('customCron', { default: '0 9 * * 1' })
const repeatTime = defineModel<string>('repeatTime', { default: '09:00' })
const enabled = defineModel<boolean>('enabled', { default: true })
const prompt = defineModel<string>('prompt', { default: '' })
</script>

<template>
  <div class="automation-form-panel">
    <header class="automation-form-header">
      <div>
        <h3>{{ props.editingId ? '编辑自动化任务' : '新建自动化任务' }}</h3>
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
        <n-button type="primary" :loading="props.parsing" :disabled="!nlInput.trim()" @click="emit('parse')">
          解析为任务
        </n-button>
      </div>
    </section>

    <section class="automation-form-block">
      <h4>时间与任务内容</h4>
      <div class="task-form">
        <n-input v-model:value="name" placeholder="任务名称" />
        <div class="schedule-panel">
          <div class="schedule-row">
            <span class="schedule-label">重复</span>
            <n-select v-model:value="repeatMode" :options="props.repeatModeOptions" />
          </div>
          <div v-if="repeatMode === 'weekly'" class="schedule-row">
            <span class="schedule-label">星期</span>
            <n-select
              v-model:value="weekdays"
              multiple
              :options="props.weekdayOptions"
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
          <label class="enabled"><n-switch v-model:value="enabled" /> 启用</label>
        </div>
        <div v-if="props.preview" class="preview">
          {{ props.preview.summary }} · 下次 {{ new Date(props.preview.next_run_at).toLocaleString() }}
        </div>
        <n-input v-model:value="prompt" type="textarea" placeholder="任务提示词" :rows="4" />
        <div class="actions">
          <n-button type="primary" :loading="props.saving" :disabled="!name || !prompt" @click="emit('save')">
            {{ props.editingId ? '保存修改' : '创建任务' }}
          </n-button>
          <n-button v-if="props.editingId" @click="emit('cancel')">取消</n-button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.automation-form-panel {
  padding: 18px;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: 14px;
  background: var(--noesis-color-bg-elevated);
}

.automation-form-header {
  margin-bottom: 18px;
}

.automation-form-header h3 {
  margin: 0;
  color: var(--noesis-color-text-heading);
  font-size: 16px;
  font-weight: 650;
}

.automation-form-header p {
  margin: 5px 0 0;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.automation-form-block + .automation-form-block {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--noesis-color-border-subtle);
}

.automation-form-block h4 {
  margin: 0 0 10px;
  color: var(--noesis-color-text-heading);
  font-size: 13px;
  font-weight: 600;
}

.nl-box,
.task-form {
  display: grid;
  gap: 10px;
}

.schedule-panel {
  display: grid;
  gap: 0;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: 14px;
  overflow: hidden;
}

.schedule-row,
.custom-cron-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 52px;
  padding: 0 14px;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
}

.schedule-row :deep(.n-select),
.custom-cron-row :deep(.n-input) {
  min-width: 0;
}

.schedule-label,
.schedule-suffix {
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}

.time-input {
  width: 120px;
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid var(--noesis-color-border);
  border-radius: 6px;
  color: var(--noesis-color-text-primary);
  background: var(--noesis-color-bg-elevated);
  font: inherit;
}

.enabled {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 52px;
  padding: 0 14px;
  color: var(--noesis-color-text-primary);
}

.preview {
  color: var(--noesis-color-success, #287a45);
  font-size: 12px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
