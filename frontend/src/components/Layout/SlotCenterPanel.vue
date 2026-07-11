<script lang="ts" setup>
interface Props {
  loading?: boolean
}
withDefaults(defineProps<Props>(), {
  loading: false,
})

const appStore = useAppStore()
const { isMobile } = useBreakpoint()

appStore.areaLoading = true
setTimeout(() => {
  appStore.areaLoading = false
}, 400)
</script>

<template>
  <LayoutSlotFrame :class="['bg-no-repeat bg-cover bg-center app-shell']">
    <template #center>
      <div
        flex="~"
        size-full
        overflow-hidden
        class="panel-shadow"
      >
        <n-spin
          w-full
          h-full
          content-class="w-full h-full flex"
          :show="appStore.areaLoading"
          :rotate="false"
          class="bg-surface"
          :style="{
            '--n-opacity-spinning': '0',
          }"
        >
          <!-- 桌面端：左侧全局导航 -->
          <section
            v-if="!isMobile"
            flex="~ col"
            min-w-0
            w-70
            h-full
            overflow-hidden
            relative
          >
            <NavigationSideBar />
          </section>

          <!-- 主内容区 -->
          <section
            flex="1 ~"
            min-w-0
            h-full
            overflow-hidden
            class="app-shell__main"
            :class="{ 'app-shell__main--mobile': isMobile }"
            :style="{ background: 'var(--noesis-layout-shell-bg)' }"
          >
            <div
              size-full
              overflow-hidden
              class="app-shell__content"
              :class="{ 'app-shell__content--mobile': isMobile }"
            >
              <LayoutDefault />
            </div>
          </section>
        </n-spin>
      </div>
    </template>
    <template #bottom>
      <NavigationNavFooter />
      <NavigationMobileBottomNav v-if="isMobile" />
    </template>
  </LayoutSlotFrame>
</template>

<style lang="scss" scoped>
.app-shell {
  height: var(--noesis-app-height);
}

.panel-shadow {
  --shadow: 50px 50px 100px 10px rgb(0 0 0 / 10%);
  --at-apply: 'shadow-[--shadow]';
}

.app-shell__main {
  padding: var(--noesis-shell-padding-desktop) var(--noesis-shell-padding-desktop) var(--noesis-shell-padding-desktop) 0;
}

.app-shell__main--mobile {
  padding:
    calc(var(--noesis-safe-area-top) + var(--noesis-shell-padding-mobile))
    var(--noesis-shell-padding-mobile)
    calc(var(--noesis-mobile-nav-height) + var(--noesis-safe-area-bottom) + var(--noesis-shell-padding-mobile))
    var(--noesis-shell-padding-mobile);
}

.app-shell__content {
  border-radius: var(--noesis-shell-radius-desktop);
}

.app-shell__content--mobile {
  border-radius: var(--noesis-shell-radius-mobile);
}

@media (max-width: 1024px) {
  .panel-shadow {
    --shadow: none;
  }
}

@media (min-width: 769px) and (max-width: 1024px) {
  .app-shell__main {
    padding: 16px 16px 16px 0;
  }

  .app-shell__content {
    border-radius: 24px;
  }
}
</style>
