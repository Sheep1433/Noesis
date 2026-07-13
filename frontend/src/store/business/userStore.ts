import { defineStore } from 'pinia'

export interface AuthUser {
  id: number
  username: string
  mobile?: string | null
}

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
      try {
        const res = await fetch(`${location.origin}/api/auth/session`, { credentials: 'include' })
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
        this.initialized = true
      }
    },
  },
  getters: { isLoggedIn: (state) => !!state.user },
})
