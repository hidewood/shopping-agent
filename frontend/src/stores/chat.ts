import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api'

export interface Message {
  role: 'user' | 'assistant'
  content: string
  responseType?: string
  productId?: string | null
  trace?: Array<Record<string, unknown>>
  products?: Array<Record<string, unknown>>
  alternatives?: Array<Record<string, unknown>>
  guidance?: Record<string, unknown> | null
  bundle?: Record<string, unknown> | null
  error?: { code: string; retriable: boolean } | null
  retryText?: string
  retryId?: string
}

export const useChatStore = defineStore('chat', () => {
  const handleStorageKey = 'shopping_agent_conversation'
  const conversationId = ref('')
  const conversationToken = ref('')
  const messages = ref<Message[]>([])
  const loading = ref(false)
  const progressText = ref('')
  const scrollTop = ref(0)
  let initPromise: Promise<void> | null = null
  let activeController: AbortController | null = null

  function persistHandle() {
    if (!conversationId.value) {
      localStorage.removeItem(handleStorageKey)
      return
    }
    localStorage.setItem(handleStorageKey, JSON.stringify({
      conversationId: conversationId.value,
      conversationToken: conversationToken.value,
    }))
  }

  function restoreHandle() {
    try {
      const value = JSON.parse(localStorage.getItem(handleStorageKey) || '{}')
      if (typeof value.conversationId !== 'string' || !value.conversationId) return false
      conversationId.value = value.conversationId
      conversationToken.value = typeof value.conversationToken === 'string' ? value.conversationToken : ''
      return true
    } catch {
      localStorage.removeItem(handleStorageKey)
      return false
    }
  }

  async function init() {
    if (initPromise) return initPromise
    initPromise = (async () => {
      if (restoreHandle()) {
        try {
          await loadConversation(conversationId.value)
          return
        } catch {
          conversationId.value = ''
          conversationToken.value = ''
          messages.value = []
          localStorage.removeItem(handleStorageKey)
        }
      }
      const conversation = await api.createConversation()
      conversationId.value = conversation.conversation_id
      conversationToken.value = conversation.conversation_access_token || ''
      persistHandle()
    })()
    try {
      await initPromise
    } finally {
      initPromise = null
    }
  }

  function appendResult(result: api.TurnResult, text: string, requestId: string) {
    messages.value.push({
      role: 'assistant', content: result.summary, responseType: result.response_type,
      productId: result.purchased_product_id, trace: result.trace,
      products: result.products || [], alternatives: result.alternatives || [],
      guidance: result.guidance || null, bundle: result.bundle || null,
      error: result.error || null, retryText: result.error?.retriable ? text : undefined,
      retryId: result.error?.retriable ? requestId : undefined,
    })
  }

  async function send(text: string, isRetry = false, existingRequestId?: string) {
    if (!isRetry) messages.value.push({ role: 'user', content: text })
    loading.value = true
    progressText.value = '正在理解你的需求…'
    const requestId = existingRequestId || crypto.randomUUID()
    activeController = new AbortController()
    const slowTimer = window.setTimeout(() => { progressText.value = '正在检索并校验商品，请稍候…' }, 3_000)
    const longTimer = window.setTimeout(() => { progressText.value = '模型仍在处理复杂请求，最多等待 30 秒…' }, 10_000)
    try {
      const result = await api.sendMessage(conversationId.value, text, conversationToken.value, requestId, activeController.signal)
      appendResult(result, text, requestId)
    } catch (error: any) {
      const status = error?.response?.status
      if (status === 409) {
        try {
          await loadConversation(conversationId.value)
          messages.value.push({ role: 'user', content: text })
          const result = await api.sendMessage(conversationId.value, text, conversationToken.value, requestId, activeController.signal)
          appendResult(result, text, requestId)
          return
        } catch (retryError: any) {
          error = retryError
        }
      }
      const cancelled = error?.code === 'ERR_CANCELED'
      const timedOut = error?.code === 'ECONNABORTED'
      messages.value.push({
        role: 'assistant',
        content: cancelled ? '已停止等待。服务端可能仍会完成本轮；刷新或重试会复用同一请求编号，不会重复执行账户操作。' : timedOut ? '本次请求超过 32 秒。可以安全重试；系统会复用请求编号，不会重复执行已提交的操作。' : '请求暂时未能完成，请检查网络或稍后重试。',
        responseType: 'transport_error',
        error: { code: cancelled ? 'cancelled' : timedOut ? 'client_timeout' : 'network_error', retriable: true },
        retryText: text,
        retryId: requestId,
      })
    } finally {
      window.clearTimeout(slowTimer)
      window.clearTimeout(longTimer)
      activeController = null
      progressText.value = ''
      loading.value = false
    }
  }

  function cancel() { activeController?.abort() }

  async function reset() {
    messages.value = []
    scrollTop.value = 0
    const conversation = await api.createConversation()
    conversationId.value = conversation.conversation_id
    conversationToken.value = conversation.conversation_access_token || ''
    persistHandle()
  }

  function clearSession() {
    activeController?.abort()
    conversationId.value = ''
    conversationToken.value = ''
    messages.value = []
    scrollTop.value = 0
    localStorage.removeItem(handleStorageKey)
  }

  async function loadConversation(cid: string) {
    const data: any = await api.getConversation(cid, conversationToken.value)
    conversationId.value = cid
    persistHandle()
    messages.value = []
    for (const event of data.events || []) {
      if (event.event_type === 'user_message') {
        messages.value.push({ role: 'user', content: event.payload?.message || '' })
      } else if (event.event_type === 'assistant_message') {
        const r = event.payload?.result || {}
        messages.value.push({
          role: 'assistant',
          content: r.summary || '',
          responseType: r.response_type,
          productId: r.purchased_product_id,
          trace: r.trace,
          products: r.catalog_data?.products || [],
          alternatives: r.catalog_data?.alternatives || [],
          guidance: r.proactive_guidance || null,
          bundle: r.catalog_data?.bundle || null,
        })
      }
    }
  }

  function retry(text: string, requestId?: string) { return send(text, true, requestId) }

  return { conversationId, conversationToken, messages, loading, progressText, scrollTop, init, send, retry, cancel, reset, clearSession, loadConversation }
})
