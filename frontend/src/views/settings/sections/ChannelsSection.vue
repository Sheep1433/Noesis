<script setup lang="ts">
import type { MessagingChannel } from '@/api/settings'
import { NButton, NInput, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { onMounted, reactive, ref, watch } from 'vue'
import {
  createChannel, deleteChannel, listChannels, testChannelConnection,
  testChannelDelivery, updateChannel,
} from '@/api/settings'
import { SettingsEmptyState, SettingsSection } from '../primitives'
import { buildChannelPayload } from './channelPayload'

const message = useMessage()
const dialog = useDialog()
const channels = ref<MessagingChannel[]>([])
const editingId = ref<string | null>(null)
const form = reactive({
  type: 'telegram' as 'telegram' | 'feishu', display_name: '我的 Telegram Bot', bot_token: '',
  pairing_chat_id: '', pairing_user_id: '', enabled: true,
})
const channelOptions = [{ label: 'Telegram', value: 'telegram' }, { label: '飞书', value: 'feishu' }]
watch(() => form.type, (type) => {
  if (!editingId.value) {
    form.display_name = type === 'feishu' ? '我的飞书机器人' : '我的 Telegram Bot'
  }
})

async function refresh() {
  try {
    channels.value = await listChannels()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载失败')
  }
}

function resetForm() {
  editingId.value = null
  Object.assign(form, { type: 'telegram', display_name: '我的 Telegram Bot', bot_token: '', pairing_chat_id: '', pairing_user_id: '', enabled: true })
}

function edit(channel: MessagingChannel) {
  editingId.value = channel.channel_id
  Object.assign(form, {
    type: channel.type as 'telegram' | 'feishu', display_name: channel.display_name, bot_token: '',
    pairing_chat_id: channel.pairing_chat_id || '', pairing_user_id: channel.pairing_user_id || '', enabled: channel.enabled,
  })
}

async function save() {
  if (form.type === 'telegram' && !editingId.value && !form.bot_token.trim()) {
    return message.warning('请填写 Bot Token')
  }
  if (form.type === 'feishu' && !form.pairing_user_id.trim()) {
    return message.warning('请填写发送者 Open ID')
  }
  const payload = buildChannelPayload(form)
  try {
    if (editingId.value) {
      await updateChannel(editingId.value, payload)
    } else {
      await createChannel(payload)
    }
    message.success('通道已保存')
    resetForm()
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  }
}

async function toggle(channel: MessagingChannel, enabled: boolean) {
  try {
    await updateChannel(channel.channel_id, {
      type: channel.type, display_name: channel.display_name, enabled,
      pairing_chat_id: channel.pairing_chat_id,
      pairing_user_id: channel.pairing_user_id,
      bot_token_action: 'keep',
    })
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '更新失败')
  }
}

async function probe(channel: MessagingChannel) {
  try {
    const result = await testChannelConnection(channel.channel_id)
    result.ok ? message.success(result.message) : message.warning(result.message)
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '连接测试失败')
  }
}

async function deliver(channel: MessagingChannel) {
  try {
    const result = await testChannelDelivery(channel.channel_id)
    result.ok ? message.success(result.message) : message.warning(result.message)
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '测试消息发送失败')
  }
}

function remove(channel: MessagingChannel) {
  dialog.warning({ title: `删除 ${channel.display_name || '通道'}？`, content: '删除后将停止接收和发送该通道的消息。', positiveText: '删除', negativeText: '取消',
    async onPositiveClick() {
      await deleteChannel(channel.channel_id)
      message.success('已删除')
      await refresh()
    } })
}

function healthTone(status?: string) {
  return status === 'healthy' ? 'success' : status === 'unavailable' ? 'error' : 'warning'
}
onMounted(() => void refresh())
</script>

<template>
  <SettingsSection title="通讯通道" description="连接 Telegram 或飞书，在常用通讯工具中使用 Agent。">
    <ol v-if="form.type === 'telegram'" class="steps">
      <li>在 Telegram 向 <strong>@BotFather</strong> 发送 <code>/newbot</code>，创建机器人并复制 Token。</li>
      <li>添加通道后向机器人发送 <code>/start</code>，再将返回的 Chat ID 填入配对栏。</li>
    </ol>
    <ol v-else class="steps">
      <li>向 Noesis 飞书机器人发送任意消息，机器人会返回你的 Open ID。</li>
      <li>填写 Open ID 完成关联；如需发送测试消息，再填写 Chat ID。</li>
    </ol>
    <div class="form">
      <n-select v-model:value="form.type" :options="channelOptions" :disabled="Boolean(editingId)" />
      <n-input v-model:value="form.display_name" placeholder="显示名称" />
      <template v-if="form.type === 'telegram'">
        <n-input v-model:value="form.bot_token" type="password" show-password-on="click" :placeholder="editingId ? '留空则保留现有 Token' : 'Bot Token'" />
        <n-input v-model:value="form.pairing_chat_id" placeholder="配对 Chat ID（可稍后填写）" />
      </template>
      <template v-else>
        <n-input v-model:value="form.pairing_user_id" placeholder="发送者 Open ID（用于配对）" />
        <n-input v-model:value="form.pairing_chat_id" placeholder="接收测试消息的 Chat ID（可稍后填写）" />
      </template>
      <label class="enabled"><n-switch v-model:value="form.enabled" /> 启用通道</label>
      <div class="actions"><n-button type="primary" @click="save">{{ editingId ? '保存修改' : `添加${form.type === 'feishu' ? '飞书' : ' Telegram'}通道` }}</n-button><n-button v-if="editingId" @click="resetForm">取消</n-button></div>
    </div>

    <SettingsEmptyState v-if="channels.length === 0" title="尚未配置通道" description="添加通讯通道后可测试连接与消息投递。" />
    <div v-for="channel in channels" :key="channel.channel_id" class="channel-card">
      <div class="channel-head"><div><strong>{{ channel.display_name || channel.type }}</strong><div class="muted">{{ channel.type }} · {{ channel.type === 'feishu' ? '系统机器人' : (channel.bot_token_masked || '未配置 Token') }} · {{ channel.type === 'feishu' ? (channel.pairing_user_id ? '已关联' : '未关联') : (channel.pairing_chat_id ? '已配对' : '未配对') }}</div></div><n-tag size="small" :type="healthTone(channel.health?.status)">{{ channel.health?.status || 'unknown' }}</n-tag></div>
      <div class="health"><span>{{ channel.health?.message || '尚未检查' }}</span><span v-if="channel.health?.checked_at">检查于 {{ new Date(channel.health.checked_at).toLocaleString() }}</span><span v-if="channel.health?.last_inbound_at">最近接收：{{ channel.health.last_inbound_status }} · {{ new Date(channel.health.last_inbound_at).toLocaleString() }}</span><span v-if="channel.health?.last_outbound_at">最近发送：{{ channel.health.last_outbound_status }} · {{ new Date(channel.health.last_outbound_at).toLocaleString() }}</span><span v-if="channel.health?.correlation_id">关联 ID：{{ channel.health.correlation_id }}</span></div>
      <div class="actions"><n-switch :value="channel.enabled" @update:value="value => toggle(channel, value)" /><n-button size="small" @click="probe(channel)">测试连接</n-button><n-button size="small" :disabled="!channel.pairing_chat_id" @click="deliver(channel)">发送测试消息</n-button><n-button size="small" @click="edit(channel)">编辑</n-button><n-button size="small" type="error" quaternary @click="remove(channel)">删除</n-button></div>
    </div>
  </SettingsSection>
</template>

<style scoped>
.steps { margin: 0 0 16px; padding-left: 1.25rem; color: var(--noesis-color-text-secondary); font-size: 13px; line-height: 1.6; }
.form { display: grid; gap: 10px; max-width: 640px; margin-bottom: 24px; }
.enabled, .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.channel-card { padding: 16px 0; border-top: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); }
.channel-head { display: flex; justify-content: space-between; gap: 12px; }
.muted, .health { color: var(--noesis-color-text-secondary); font-size: 12px; }
.health { display: flex; flex-direction: column; gap: 3px; margin: 8px 0 12px; }
</style>
