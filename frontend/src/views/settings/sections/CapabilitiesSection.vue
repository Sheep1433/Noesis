<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

const links = [
  { label: 'Skills', to: { name: 'Extensions', query: {} } },
  { label: 'MCP', to: { name: 'Extensions', query: { tab: 'mcp' } } },
  { label: '知识库', to: { name: 'KnowledgeBase' } },
]
</script>

<template>
  <section class="pane">
    <h2>扩展</h2>
    <p class="hint">
      Skills、MCP 与知识库的管理入口。
    </p>
    <ul class="links">
      <li v-for="item in links" :key="item.label">
        <button type="button" class="link" @click="router.push(item.to)">
          {{ item.label }}
        </button>
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
.links {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.link {
  border: none;
  background: transparent;
  color: var(--noesis-color-text-heading);
  text-align: left;
  padding: 10px 0;
  cursor: pointer;
  font-size: 15px;
  text-decoration: underline;
}
</style>
