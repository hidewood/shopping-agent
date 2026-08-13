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
}

export const useChatStore = defineStore('chat', () => {
  const conversationId = ref('')
  const messages = ref<Message[]>([])
  const loading = ref(false)

  async function init() {
    conversationId.value = await api.createConversation()
  }

  async function send(text: string) {
    messages.value.push({ role: 'user', content: text })
    loading.value = true
    try {
      const result = await api.sendMessage(conversationId.value, text)
      messages.value.push({
        role: 'assistant',
        content: result.summary,
        responseType: result.response_type,
        productId: result.purchased_product_id,
        trace: result.trace,
        products: (result as any).products || [],
        alternatives: (result as any).alternatives || [],
        guidance: (result as any).guidance || null,
      })
    } finally {
      loading.value = false
    }
  }

  async function reset() {
    messages.value = []
    conversationId.value = await api.createConversation()
  }

  return { conversationId, messages, loading, init, send, reset }
})
