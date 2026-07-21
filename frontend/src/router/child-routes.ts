const childrenRoutes: Array<RouteRecordRaw> = [
  {
    path: 'chat',
    meta: { requiresAuth: true },
    name: 'ChatRoot',
    redirect: {
      name: 'ChatIndex',
    },
    children: [
      {
        path: '',
        name: 'ChatIndex',
        component: () => import('@/views/chat.vue'),
      },
    ],
  },
  {
    path: 'mcp',
    name: 'McpManagement',
    component: () => import('@/views/mcp/MCPClient.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: 'mcpChat',
    redirect: { name: 'McpManagement' },
  },
  {
    path: 'testcase/generate',
    name: 'TestCaseGenerate',
    component: () => import('@/views/TestAssistant.vue'),
    meta: { requiresAuth: true },
  },
  // 知识库路由
  {
    path: 'knowledgeBase',
    name: 'KnowledgeBase',
    component: () => import('@/views/knowledge-base/KnowledgeBase.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: 'knowledgeBase/collection/:collectionName',
    name: 'KnowledgeBaseDetail',
    component: () => import('@/views/knowledge-base/CollectionDetail.vue'),
    meta: { requiresAuth: true },
  },
  // Skills 管理路由
  {
    path: 'skills',
    name: 'SkillsManagement',
    component: () => import('@/views/skills/SkillsManagement.vue'),
    meta: { requiresAuth: true },
  },
]

export default childrenRoutes
