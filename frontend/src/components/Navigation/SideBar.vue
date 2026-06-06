<script lang="tsx" setup>
const router = useRouter()
const route = useRoute()
const userStore = useUserStore()


// 侧边栏图标组件
const SideBarItem = defineComponent({
  props: {
    label: {
      type: String,
      default: '',
    },
    fill: {
      type: Boolean,
      default: false,
    },
    active: {
      type: Boolean,
      default: false,
    },
    disabled: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['click'],
  setup(props, { emit }) {
    const computedWrapperClassName = computed(() => {
      if (props.fill) {
        return 'c-slate-700'
      }

      if (props.disabled) {
        return [
          'opacity-50',
        ]
      }

      return [
        'c-slate-500 hover:c-slate-700',
        props.active && 'c-slate-700',
      ]
    })

    const computedInnerClassName = computed(() => {
      if (props.fill) {
        return
      }

      return [
        'p-10 rouned-50%',
        props.active && 'bg-slate-200',
      ]
    })

    const handleClick = () => {
      if (props.disabled) {
        return
      }
      emit('click')
    }

    return {
      computedWrapperClassName,
      computedInnerClassName,
      handleClick,
    }
  },
  render() {
    return (
      <div
        flex="~ col gap-10 items-center"
        class={[
          'select-none transition-all-260',
          this.disabled
            ? 'cursor-not-allowed'
            : 'cursor-pointer',
          this.computedWrapperClassName,
        ]}
        onClick={this.handleClick}
      >
        <div
          flex="~ justify-center items-center"
          class={[
            'transition-all-260',
            'size-40 rounded-50%',
            '[&>*]:size-full [&>*]:bg-no-repeat [&>*]:bg-center [&>*]:bg-cover',
            this.computedInnerClassName,
          ]}
        >
          {this.$slots.default?.()}
        </div>
        <div class="font-bold">{this.label}</div>
      </div>
    )
  },
})

const sidebarItems = ref([
  {
    label: '智枢',
    key: 'SystemLogo',
    renderIcon() {
      return (
        <div class="brand-mark i-my-svg:system-logo"></div>
      )
    },
    onClick() {
      router.push('/')
    },
    props: {
      fill: true,
    },
  },
  {
    label: '对话',
    key: 'ChatIndex',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="i-my-svg:chat-index"></div>
      )
    },
  },
  {
    label: '测试用例',
    key: 'TestCaseGenerate',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="i-mdi:clipboard-text-outline"></div>
      )
    },
  },
  {
    label: '知识库',
    key: 'KnowledgeBase',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="i-my-svg:chat-knowledge"></div>
      )
    },
  },
  {
    label: 'Skills',
    key: 'SkillsManagement',
    onClick() {
      router.push({
        name: this.key,
      })
    },
    renderIcon() {
      return (
        <div class="i-my-svg:chat-skill"></div>
      )
    },
  },
])


const handleLogout = () => {
  userStore.logout()
  setTimeout(() => {
    router.replace('/login')
  }, 500)
}
</script>

<template>
  <section
    flex="~ col justify-between"
    w-70
    h-full
    overflow-hidden
    relative
    :style="{
      background: `linear-gradient(
        to bottom,
        #f8f9fa,
        #e9ecef
      )`,
    }"
  >
    <!-- 最侧边图标设置 -->
    <div
      flex="1 ~ col gap-28"
      pt-24
    >
      <SideBarItem
        v-for="(sidebarItem) in sidebarItems"
        :key="sidebarItem.key"
        :label="sidebarItem.label"
        :active="sidebarItem.key === route.name"
        v-bind="sidebarItem.props"
        @click="sidebarItem.onClick.call(sidebarItem)"
      >
        <component :is="sidebarItem.renderIcon" />
      </SideBarItem>
    </div>

    <n-popover
      trigger="hover"
      placement="right-start"
    >
      <template #trigger>
        <SideBarItem
          fill
        >
          <div class="size-35 i-my-svg:avatar"></div>
        </SideBarItem>
      </template>
      <n-button
        quaternary
        strong
        @click="handleLogout"
      >
        退出登录
      </n-button>
    </n-popover>
  </section>
</template>

<style lang="scss" scoped>
.brand-mark {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  overflow: hidden;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
}
</style>
