<script lang="tsx" setup>
import { useMessage } from 'naive-ui'
import * as GlobalAPI from '@/api'

type AuthMode = 'login' | 'register'

const form = ref({
  username: '',
  password: '',
  confirmPassword: '',
  mobile: '',
})
const mode = ref<AuthMode>('login')
const formRef = ref(null)
const loading = ref(false)
const message = useMessage()
const router = useRouter()
const userStore = useUserStore()

onMounted(() => {
  if (userStore.isLoggedIn) {
    router.push('/')
  }
})

async function parseResponse(res: Response) {
  const responseData = await res.json()
  return responseData as { code: number, msg?: string, data?: { token?: string } }
}

async function handleLogin() {
  if (!form.value.username || !form.value.password) {
    message.error('请填写完整信息')
    return
  }
  loading.value = true
  try {
    const res = await GlobalAPI.login(form.value.username, form.value.password)
    const responseData = await parseResponse(res)
    if (responseData.code === 200 && responseData.data?.token) {
      userStore.login({ token: responseData.data.token })
      message.success('登录成功')
      setTimeout(() => router.push('/'), 300)
    } else {
      message.error(responseData.msg ?? '登录失败，请检查用户名或密码')
    }
  } catch {
    message.error('登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function handleRegister() {
  if (!form.value.username || !form.value.password || !form.value.confirmPassword) {
    message.error('请填写完整信息')
    return
  }
  if (form.value.password !== form.value.confirmPassword) {
    message.error('两次输入的密码不一致')
    return
  }
  if (form.value.password.length < 6) {
    message.error('密码长度至少 6 位')
    return
  }
  loading.value = true
  try {
    const res = await GlobalAPI.register(
      form.value.username,
      form.value.password,
      form.value.mobile || undefined,
    )
    const responseData = await parseResponse(res)
    if (responseData.code === 200) {
      message.success('注册成功，请登录')
      mode.value = 'login'
      form.value.password = ''
      form.value.confirmPassword = ''
    } else {
      message.error(responseData.msg ?? '注册失败')
    }
  } catch {
    message.error('注册失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function handleSubmit() {
  if (mode.value === 'login') {
    handleLogin()
  } else {
    handleRegister()
  }
}

function switchMode(next: AuthMode) {
  mode.value = next
  form.value.password = ''
  form.value.confirmPassword = ''
}
</script>

<template>
  <div class="login-container">
    <transition name="fade" mode="out-in">
      <n-card
        v-if="!userStore.isLoggedIn"
        class="w-400 max-w-90vw"
        :style="{
          background: `linear-gradient(to bottom, #ffffff, #f8f9fa)`,
        }"
        :title="mode === 'login' ? '登录' : '注册'"
      >
        <n-tabs
          :value="mode"
          type="segment"
          class="mb-4"
          @update:value="switchMode"
        >
          <n-tab-pane name="login" tab="登录" />
          <n-tab-pane name="register" tab="注册" />
        </n-tabs>

        <n-form ref="formRef" @submit.prevent="handleSubmit">
          <n-form-item label="用户名" path="username">
            <n-input
              v-model:value="form.username"
              placeholder="请输入用户名（3-50 字符）"
            />
          </n-form-item>
          <n-form-item label="密码" path="password">
            <n-input
              v-model:value="form.password"
              type="password"
              show-password-on="click"
              :placeholder="mode === 'login' ? '请输入密码' : '至少 6 位'"
            />
          </n-form-item>
          <n-form-item
            v-if="mode === 'register'"
            label="确认密码"
            path="confirmPassword"
          >
            <n-input
              v-model:value="form.confirmPassword"
              type="password"
              show-password-on="click"
              placeholder="请再次输入密码"
            />
          </n-form-item>
          <n-form-item
            v-if="mode === 'register'"
            label="手机号"
            path="mobile"
          >
            <n-input
              v-model:value="form.mobile"
              placeholder="选填"
            />
          </n-form-item>
          <n-form-item>
            <n-button
              type="primary"
              :loading="loading"
              :block="true"
              @click="handleSubmit"
            >
              {{ mode === 'login' ? '登录' : '注册' }}
            </n-button>
          </n-form-item>
        </n-form>
      </n-card>
    </transition>
  </div>
</template>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background: linear-gradient(to bottom, #dee2e6, white);
}

body,
html {
  margin: 0;
  padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
    'Helvetica Neue', Arial, sans-serif;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.5s ease;
}

.fade-enter, .fade-leave-to {
  opacity: 0;
}
</style>
