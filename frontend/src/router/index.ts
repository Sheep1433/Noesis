import { createWebHashHistory, createWebHistory } from 'vue-router'
import { createRouterGuards } from '@/router/permission'
import routes, { LOGIN_ROUTE_PATH } from './routes'

/** GitHub Pages 等静态托管使用 `VITE_ROUTER_MODE=hash`（见 `pnpm build:gh-pages`） */
const history = import.meta.env.VITE_ROUTER_MODE === 'hash'
  ? createWebHashHistory()
  : createWebHistory()

const router = createRouter({
  history,
  routes,
})

// 全局前置守卫
router.beforeEach((to, from, next) => {
  const userStore = useUserStore()
  if (to.meta.requiresAuth && !userStore.isLoggedIn) {
    // 如果目标路由需要认证且用户未登录，则重定向到登录页面
    next(LOGIN_ROUTE_PATH)
  } else {
    next()
  }
})

export async function setupRouter(app: App) {
  createRouterGuards(router)
  app.use(router)

  await router.isReady()
}

export default router
