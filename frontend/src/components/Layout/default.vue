<script lang="ts" setup>
defineOptions({
  name: 'LayoutDefault',
})
</script>

<template>
  <router-view v-slot="{ Component }">
    <component :is="Component" class="layout-page" />
  </router-view>
</template>

<style scoped>
.layout-page {
  height: 100%;
  min-height: 0;
}
</style>
