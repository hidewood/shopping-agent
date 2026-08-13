import axios from 'axios'

const api = axios.create({ baseURL: '' })

// 附加 JWT token 到请求头
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export interface TurnResult {
  conversation_id: string
  response_type: string
  summary: string
  purchased_product_id: string | null
  trace: Array<Record<string, unknown>>
}

export interface User {
  id: string
  email: string
  name: string | null
}

// ── auth ──────────────────────────────────────────────────────────────

export async function register(email: string, password: string, name?: string) {
  const r = await api.post('/auth/register', { email, password, name })
  return r.data
}

export async function login(email: string, password: string) {
  const r = await api.post('/auth/login', { email, password })
  return r.data
}

export async function me(): Promise<User> {
  const r = await api.get('/auth/me')
  return r.data
}

// ── cart ──────────────────────────────────────────────────────────────

export async function getCart() {
  const r = await api.get('/cart')
  return r.data
}

export async function addCartItem(productId: string, quantity = 1) {
  const r = await api.post('/cart/items', { product_id: productId, quantity })
  return r.data
}

export async function updateCartItem(itemId: string, quantity: number) {
  const r = await api.patch(`/cart/items/${itemId}`, { quantity })
  return r.data
}

export async function removeCartItem(itemId: string) {
  const r = await api.delete(`/cart/items/${itemId}`)
  return r.data
}

export async function clearCart() {
  const r = await api.delete('/cart')
  return r.data
}

// ── favorites ─────────────────────────────────────────────────────────

export async function addFavorite(productId: string) {
  const r = await api.post('/favorites', { product_id: productId })
  return r.data.favorites
}

export async function getFavorites() {
  const r = await api.get('/favorites')
  return r.data.favorites
}

export async function removeFavorite(productId: string) {
  const r = await api.delete(`/favorites/${productId}`)
  return r.data.favorites
}

// ── orders ────────────────────────────────────────────────────────────

export async function createOrder() {
  const r = await api.post('/orders', {})
  return r.data
}

export async function listOrders() {
  const r = await api.get('/orders')
  return r.data
}

export async function cancelOrder(orderId: string) {
  const r = await api.post(`/orders/${orderId}/cancel`)
  return r.data
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
