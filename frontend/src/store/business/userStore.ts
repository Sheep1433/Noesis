// src/store/userStore.ts
import { defineStore } from 'pinia'

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null as null | { token: string },
  }),
  actions: {
    login(user: { token: string }) {
      this.user = user
      sessionStorage.setItem('user', JSON.stringify(user))
    },
    setToken(token: string) {
      this.login({ token })
    },
    logoutLocal() {
      this.user = null
      sessionStorage.removeItem('user')
    },
    async logout() {
      try {
        await fetch(`${location.origin}/api/user/logout`, {
          method: 'POST',
          credentials: 'include',
        })
      } catch {
        // 网络失败时仍清除本地态
      }
      this.logoutLocal()
    },
    init() {
      const storedUser = sessionStorage.getItem('user')
      if (storedUser) {
        this.user = JSON.parse(storedUser)
      }
    },
    getUserToken() {
      const storedUser = sessionStorage.getItem('user')
      if (storedUser) {
        this.user = JSON.parse(storedUser)
      }
      return this.user?.token
    },
  },
  getters: {
    isLoggedIn: (state) => !!state.user,
  },
})
