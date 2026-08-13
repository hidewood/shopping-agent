import axios from 'axios'

const api = axios.create({ baseURL: '' })

export interface TurnResult {
  conversation_id: string
  response_type: string
  summary: string
  purchased_product_id: string | null
  trace: Array<Record<string, unknown>>
}

export async function createConversation(): Promise<string> {
  const r = await api.post('/api/conversations')
  return r.data.conversation_id
}

export async function sendMessage(cid: string, message: string): Promise<TurnResult> {
  const r = await api.post(`/api/conversations/${cid}/messages`, { message })
  return r.data
}

export async function getConversation(cid: string): Promise<Record<string, unknown>> {
  const r = await api.get(`/api/conversations/${cid}`)
  return r.data
}

export async function listProducts(params: Record<string, string | number> = {}) {
  const r = await api.get('/api/products', { params })
  return r.data
}

export async function getProduct(pid: string) {
  const r = await api.get(`/api/products/${pid}`)
  return r.data
}

export async function getFacets() {
  const r = await api.get('/api/catalog/facets')
  return r.data
}

export async function health() {
  const r = await api.get('/health')
  return r.data
}
