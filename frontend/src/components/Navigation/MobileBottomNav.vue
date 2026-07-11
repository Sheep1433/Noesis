<script lang="ts" setup>
import { mainNavItems } from '@/config/navigation'

const router = useRouter()
const route = useRoute()

function isActive(item: (typeof mainNavItems)[number]) {
  if (!item.routeName) {
    return route.path === '/'
  }
  return route.name === item.routeName
}

function navigate(item: (typeof mainNavItems)[number]) {
  if (!item.routeName) {
    router.push('/')
    return
  }
  router.push({ name: item.routeName })
}
</script>

<template>
  <nav
    class="mobile-bottom-nav"
    aria-label="主导航"
  >
    <button
      v-for="item in mainNavItems"
      :key="item.key"
      type="button"
      class="mobile-bottom-nav__item"
      :class="{
        'mobile-bottom-nav__item--active': isActive(item),
        'mobile-bottom-nav__item--brand': item.fill,
      }"
      :aria-current="isActive(item) ? 'page' : undefined"
      @click="navigate(item)"
    >
      <span
        class="mobile-bottom-nav__icon"
        :class="item.iconClass"
        aria-hidden="true"
      ></span>
      <span class="mobile-bottom-nav__label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<style lang="scss" scoped>
.mobile-bottom-nav {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 100;
  display: flex;
  align-items: stretch;
  justify-content: space-around;
  height: calc(var(--noesis-mobile-nav-height) + var(--noesis-safe-area-bottom));
  padding-bottom: var(--noesis-safe-area-bottom);
  border-top: 1px solid var(--noesis-color-border-subtle);
  background: var(--noesis-sidebar-bg);
  backdrop-filter: blur(8px);
}

.mobile-bottom-nav__item {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  justify-content: center;
  min-width: 0;
  margin: 0;
  padding: 4px 2px;
  border: none;
  background: transparent;
  color: var(--noesis-color-text-secondary);
  cursor: pointer;
  transition: color 0.15s ease;
}

.mobile-bottom-nav__item--active {
  color: var(--noesis-color-text);
  font-weight: 600;
}

.mobile-bottom-nav__item--brand .mobile-bottom-nav__icon {
  width: 22px;
  height: 22px;
}

.mobile-bottom-nav__icon {
  display: inline-block;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
}

.mobile-bottom-nav__label {
  overflow: hidden;
  font-size: 10px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
