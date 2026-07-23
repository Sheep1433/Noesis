<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/store/business/userStore'

const router = useRouter()
const userStore = useUserStore()

async function onLogout() {
  await userStore.logout()
  await router.push({ name: 'Login' })
}
</script>

<template>
  <section class="pane">
    <h2>账户</h2>
    <p class="hint">
      当前用户：{{ userStore.user?.username || '已登录' }}
    </p>
    <button type="button" class="logout" @click="onLogout">
      退出登录
    </button>
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
.logout {
  border: 1px solid var(--noesis-color-border-subtle, rgba(0, 0, 0, 0.12));
  background: transparent;
  padding: 8px 14px;
  border-radius: 8px;
  cursor: pointer;
}
</style>
