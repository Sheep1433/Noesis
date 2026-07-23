<script setup lang="ts">
import type { MessagingChannel } from '@/api/settings'
import { NButton, NInput, NSwitch, useMessage } from 'naive-ui'
import { onMounted, ref } from 'vue'
import {
  createChannel,
  deleteChannel,
  listChannels,
  updateChannel,
} from '@/api/settings'

const message = useMessage()
const channels = ref<MessagingChannel[]>([])
const displayName = ref('我的 Telegram Bot')
const botToken = ref('')
const pairingChatId = ref('')
const pairingDraft = ref<Record<string, string>>({})

async function refresh() {
  try {
    channels.value = await listChannels()
    const draft: Record<string, string> = {}
    for (const ch of channels.value) {
      draft[ch.channel_id] = ch.pairing_chat_id || ''
    }
    pairingDraft.value = draft
  } catch (e) {
    message.error(e instanceof Error ? e.message : '加载失败')
  }
}

async function onCreate() {
  if (!botToken.value.trim()) {
    message.warning('请填写 Bot Token')
    return
  }
  try {
    await createChannel({
      type: 'telegram',
      display_name: displayName.value,
      bot_token: botToken.value.trim(),
      pairing_chat_id: pairingChatId.value.trim() || undefined,
      enabled: true,
      default_qa_type: 'SUPER_AGENT_QA',
    })
    botToken.value = ''
    pairingChatId.value = ''
    message.success('已保存')
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '保存失败')
  }
}

async function onToggle(ch: MessagingChannel, enabled: boolean) {
  try {
    await updateChannel(ch.channel_id, {
      type: ch.type,
      display_name: ch.display_name,
      enabled,
      pairing_chat_id: pairingDraft.value[ch.channel_id] || ch.pairing_chat_id,
      default_qa_type: ch.default_qa_type,
    })
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function onSavePairing(ch: MessagingChannel) {
  const chatId = (pairingDraft.value[ch.channel_id] || '').trim()
  if (!chatId) {
    message.warning('请填写 Chat ID')
    return
  }
  try {
    await updateChannel(ch.channel_id, {
      type: ch.type,
      display_name: ch.display_name,
      enabled: ch.enabled,
      pairing_chat_id: chatId,
      default_qa_type: ch.default_qa_type,
    })
    message.success('配对已更新')
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function onDelete(ch: MessagingChannel) {
  try {
    await deleteChannel(ch.channel_id)
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
    <h2>通讯通道</h2>
    <ol class="steps">
      <li>
        在 Telegram 打开
        <strong>@BotFather</strong>
        ，发送 <code>/newbot</code>，按提示创建机器人并复制 Token。
      </li>
      <li>将 Token 粘贴到下方并添加通道；配对 Chat ID 可稍后填写。</li>
      <li>
        打开你的机器人，发送
        <code>/start</code>
        。若尚未配对，机器人会回复你的 Chat ID。
      </li>
      <li>将 Chat ID 填入对应通道的配对栏并保存，即可开始对话。</li>
    </ol>

    <div class="form">
      <n-input v-model:value="displayName" placeholder="显示名称" />
      <n-input
        v-model:value="botToken"
        type="password"
        show-password-on="click"
        placeholder="Bot Token"
      />
      <n-input
        v-model:value="pairingChatId"
        placeholder="配对 Chat ID（可选）"
      />
      <n-button type="primary" @click="onCreate">
        添加 Telegram 通道
      </n-button>
    </div>

    <ul class="list">
      <li v-for="ch in channels" :key="ch.channel_id" class="row">
        <div class="meta">
          <strong>{{ ch.display_name || ch.type }}</strong>
          <span class="muted">{{ ch.type }} · {{ ch.bot_token_masked || '未配置 Token' }}</span>
          <div class="pair-row">
            <n-input
              v-model:value="pairingDraft[ch.channel_id]"
              size="small"
              placeholder="配对 Chat ID"
            />
            <n-button size="small" @click="onSavePairing(ch)">
              保存配对
            </n-button>
          </div>
          <span v-if="ch.runtime_note" class="muted">{{ ch.runtime_note }}</span>
        </div>
        <div class="actions">
          <n-switch :value="ch.enabled" @update:value="(v) => onToggle(ch, v)" />
          <n-button size="small" quaternary type="error" @click="onDelete(ch)">
            删除
          </n-button>
        </div>
      </li>
      <li v-if="channels.length === 0" class="empty">
        尚未配置通道
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
.steps {
  margin: 0 0 16px;
  padding-left: 1.25rem;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
  line-height: 1.55;
  max-width: 640px;
}
.steps code {
  font-size: 12px;
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
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 240px;
}
.pair-row {
  display: flex;
  gap: 8px;
  align-items: center;
  max-width: 420px;
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
