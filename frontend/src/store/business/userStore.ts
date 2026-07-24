import { defineStore } from 'pinia'

export interface AuthUser {
  id: number
  username: string
  mobile?: string | null
}

/** 移动弱网下避免无限卡住首屏；超时后按未登录继续，后台不再阻塞 mount */
const RESTORE_SESSION_TIMEOUT_MS = 4000

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null as AuthUser | null,
    csrfToken: '',
    initialized: false,
    unavailable: false,
  }),
  actions: {
    login(user: AuthUser, csrfToken: string) {
      this.user = user
      this.csrfToken = csrfToken
      this.unavailable = false
    },
    logoutLocal() {
      this.user = null
      this.csrfToken = ''
    },
    async logout() {
      try {
        await fetch(`${location.origin}/api/auth/logout`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'X-CSRF-Token': this.csrfToken },
        })
      } finally {
        this.logoutLocal()
      }
    },
    async restoreSession() {
      if (this.initialized) {
        return
      }
      const controller = new AbortController()
      const timer = window.setTimeout(() => controller.abort(), RESTORE_SESSION_TIMEOUT_MS)
      try {
        const res = await fetch(`${location.origin}/api/auth/session`, {
          credentials: 'include',
          signal: controller.signal,
        })
        if (res.status === 401) {
          this.logoutLocal()
          return
        }
        if (!res.ok) {
          this.unavailable = true
          return
        }
        const json = await res.json() as { code: number, data?: { user: AuthUser, csrf_token: string } }
        if (json.code === 200 && json.data) {
          this.login(json.data.user, json.data.csrf_token)
        }
      } catch {
        this.unavailable = true
      } finally {
        window.clearTimeout(timer)
        this.initialized = true
      }
    },
  },
  getters: { isLoggedIn: (state) => !!state.user },
})
