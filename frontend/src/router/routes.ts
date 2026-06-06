import childRoutes from '@/router/child-routes'

/** 登录页路径（未接 i18n 前缀路由前，拦截器与请求层统一引用此处） */
export const LOGIN_ROUTE_PATH = '/login'

const routes: Array<RouteRecordRaw> = [
  {
    path: '/',
    name: 'Root',
    redirect: {
      name: 'ChatRoot',
    },
    component: () => import('@/components/Layout/SlotCenterPanel.vue'),
    meta: { requiresAuth: true }, // 标记需要认证
    children: childRoutes,
  },
  {
    path: LOGIN_ROUTE_PATH,
    name: 'Login',
    component: () => import('@/views/Login.vue'),
  },
  {
    path: '/:pathMatch(.*)',
    name: '404',
    component: () => import('@/components/404.vue'),
  },
]

export default routes
