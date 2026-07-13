import App from '@/App.vue'

import InstallGlobalComponents from '@/components'
import { initThemePreset } from '@/hooks/useThemePreset'
import { setupRouter } from '@/router'

import { setupStore } from '@/store'

import 'virtual:uno.css'

initThemePreset()

const app = createApp(App)

function setupPlugins() {
  app.use(InstallGlobalComponents)
}

async function setupApp() {
  setupStore(app)
  const userStore = useUserStore()
  await userStore.restoreSession()
  await setupRouter(app)
  app.mount('#app')
}

setupPlugins()
setupApp()

export default app
