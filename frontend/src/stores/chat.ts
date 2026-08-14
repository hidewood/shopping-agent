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
  const conversationToken = ref('')
  const messages = ref<Message[]>([])
  const loading = ref(false)

  async function init() {
    const conversation = await api.createConversation()
    conversationId.value = conversation.conversation_id
    conversationToken.value = conversation.conversation_access_token || ''
  }

  async function send(text: string) {
    messages.value.push({ role: 'user', content: text })
    loading.value = true
    try {
      const result = await api.sendMessage(conversationId.value, text, conversationToken.value)
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
    const conversation = await api.createConversation()
    conversationId.value = conversation.conversation_id
    conversationToken.value = conversation.conversation_access_token || ''
  }

  async function loadConversation(cid: string) {
    const data: any = await api.getConversation(cid, conversationToken.value)
    conversationId.value = cid
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
        })
      }
    }
  }

  return { conversationId, conversationToken, messages, loading, init, send, reset, loadConversation }
})
