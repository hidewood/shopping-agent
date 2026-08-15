import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api'
import { useChatStore } from './chat'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<api.User | null>(null)
  const token = ref(localStorage.getItem('access_token') || '')

  function setSession(data: { access_token: string; user: api.User }) {
    token.value = data.access_token
    user.value = data.user
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('shopping_agent_user', JSON.stringify(data.user))
  }

  async function login(email: string, password: string) {
    const data = await api.login(email, password)
    setSession(data)
    try { await useChatStore().reset() } catch { useChatStore().clearSession() }
  }

  async function register(email: string, password: string, name?: string) {
    const data = await api.register(email, password, name)
    setSession(data)
    try { await useChatStore().reset() } catch { useChatStore().clearSession() }
  }

  async function loadUser() {
    if (!token.value) return
    try {
      user.value = await api.me()
      localStorage.setItem('shopping_agent_user', JSON.stringify(user.value))
    } catch {
      logout()
    }
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('shopping_agent_user')
    useChatStore().clearSession()
  }

  return { user, token, login, register, loadUser, logout }
})
