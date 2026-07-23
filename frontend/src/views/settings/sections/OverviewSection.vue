<script setup lang="ts">
import type { SettingsSection } from '../SettingsNav.vue'

const emit = defineEmits<{
  (e: 'goto', section: SettingsSection): void
}>()

const cards: { key: SettingsSection, title: string, desc: string }[] = [
  { key: 'profile', title: '画像', desc: '编辑 USER.md 用户画像' },
  { key: 'memory', title: '记忆', desc: '编辑 AGENTS.md 跨会话偏好' },
  { key: 'automation', title: '自动化', desc: '定时任务与启停' },
  { key: 'channels', title: '通讯', desc: 'Telegram 等通道配置' },
  { key: 'capabilities', title: '扩展', desc: 'Skills / MCP / 知识库' },
]
</script>

<template>
  <section class="pane">
    <h2>概览</h2>
    <p class="hint">
      管理与 Agent 相关的个人设置。Slash 命令不在此配置。
    </p>
    <div class="grid">
      <button
        v-for="card in cards"
        :key="card.key"
        type="button"
        class="card"
        @click="emit('goto', card.key)"
      >
        <strong>{{ card.title }}</strong>
        <span>{{ card.desc }}</span>
      </button>
    </div>
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
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
  padding: 14px;
  border-radius: 10px;
  border: 1px solid var(--noesis-color-border-subtle, rgba(0, 0, 0, 0.1));
  background: transparent;
  cursor: pointer;
  color: var(--noesis-color-text-secondary);
}
.card strong {
  color: var(--noesis-color-text-heading);
  font-size: 15px;
}
.card:hover {
  background: var(--noesis-color-bg-hover, rgba(0, 0, 0, 0.03));
}
</style>
