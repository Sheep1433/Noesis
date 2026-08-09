<script setup lang="ts">
import type { McpServerStatusItem } from '@/api/mcp'

const props = defineProps<{
  server: McpServerStatusItem
  probing: boolean
  expanded: boolean
  tools?: Array<{ name: string, description: string }>
  toolLoading: boolean
  toolError?: string
  compact?: boolean
}>()

const emit = defineEmits<{
  toggle: [server: McpServerStatusItem]
}>()

function statusText(server: McpServerStatusItem) {
  if (!server.enabled) {
    return '已停用'
  }
  if (props.probing) {
    return 'Loading tools'
  }
  if (server.status === 'ok') {
    return server.tool_count > 0 ? `${server.tool_count} tools enabled` : 'Connected'
  }
  if (server.status === 'error') {
    return 'Error · 查看输出'
  }
  return '等待检测'
}
</script>

<template>
  <div class="server-card-wrap">
    <button
      type="button"
      class="server-card"
      :class="{ 'server-card--compact': compact }"
      :aria-expanded="expanded"
      @click="emit('toggle', server)"
    >
      <span
        class="server-card__dot"
        :class="{
          'server-card__dot--ok': server.status === 'ok',
          'server-card__dot--err': server.status === 'error',
          'server-card__dot--pending': server.status === 'unknown',
        }"
      ></span>
      <div class="server-card__body">
        <div class="server-card__name">
          {{ server.display_name || server.id }}
        </div>
        <div class="server-card__status">
          {{ statusText(server) }}
        </div>
        <div v-if="server.status === 'error' && server.message" class="server-card__err">
          {{ server.message }}
        </div>
      </div>
      <span class="server-card__chevron">{{ expanded ? '⌃' : '⌄' }}</span>
    </button>

    <div v-if="expanded" class="server-tools">
      <span v-if="toolLoading" class="server-tools__state">Loading tools…</span>
      <span v-else-if="toolError" class="server-card__err">{{ toolError }}</span>
      <span v-else-if="tools?.length === 0" class="server-tools__state">No tools found</span>
      <ul v-else class="tool-list">
        <li v-for="tool in tools" :key="tool.name">
          <strong>{{ tool.name }}</strong>
          <span>{{ tool.description }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.server-card-wrap {
  width: 100%;
}

.server-card {
  display: flex;
  width: 100%;
  gap: 10px;
  align-items: flex-start;
  text-align: left;
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 10px 8px;
  cursor: pointer;
  color: inherit;
}

.server-card:hover {
  background: var(--noesis-color-primary-bg-subtle);
}

.server-card--compact {
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-light);
  margin-bottom: 8px;
}

.server-card__dot {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 999px;
  flex-shrink: 0;
  background: var(--noesis-color-text-muted);
}

.server-card__dot--ok {
  background: var(--noesis-color-success);
  box-shadow: 0 0 0 3px rgb(81 207 102 / 18%);
}

.server-card__dot--err {
  background: var(--noesis-color-danger);
}

.server-card__dot--pending {
  animation: pulse-dot 1.2s ease-in-out infinite;
}

@keyframes pulse-dot {

  50% {
    opacity: 0.35;
  }
}

.server-card__body {
  min-width: 0;
  flex: 1;
}

.server-card__chevron {
  flex-shrink: 0;
  color: var(--noesis-color-text-muted);
  font-size: 16px;
  line-height: 1;
}

.server-card__name {
  font-size: 14px;
  font-weight: 560;
  color: var(--noesis-color-text-body);
}

.server-card__status {
  margin-top: 2px;
  font-size: 12px;
  color: var(--noesis-color-text-muted);
}

.server-card__err {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-danger);
  overflow-wrap: anywhere;
}

.server-tools {
  margin: -2px 8px 8px 26px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--noesis-color-bg-muted);
}

.server-tools__state {
  font-size: 12px;
  color: var(--noesis-color-text-muted);
}

.tool-list {
  margin: 4px 0;
  padding-left: 16px;
  display: grid;
  gap: 6px;
}

.tool-list li {
  display: flex;
  flex-direction: column;
  font-size: 12px;
  color: var(--noesis-color-text-muted);
}

.tool-list li strong {
  color: var(--noesis-color-text-body);
}
</style>
