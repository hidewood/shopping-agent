import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/login' },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    { path: '/chat', name: 'chat', component: () => import('../views/ChatView.vue') },
    { path: '/catalog', name: 'catalog', component: () => import('../views/CatalogView.vue') },
    { path: '/cart', name: 'cart', component: () => import('../views/CartView.vue') },
    { path: '/favorites', name: 'favorites', component: () => import('../views/FavoritesView.vue') },
    { path: '/orders', name: 'orders', component: () => import('../views/OrdersView.vue') },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue') },
    { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: { requiresAdmin: true } },
  ],
})

// 需要登录的页面：未登录跳转登录页，登录后回跳
const authRequired = ['/cart', '/favorites', '/orders', '/history', '/admin']

router.beforeEach(async (to) => {
  const token = localStorage.getItem('access_token')
  if (authRequired.includes(to.path)) {
    if (!token) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  if (to.meta.requiresAdmin) {
    try {
      const response = await fetch('/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) throw new Error('admin session is invalid')
      const user = await response.json()
      localStorage.setItem('shopping_agent_user', JSON.stringify(user))
      if (user?.role !== 'admin') return { path: '/chat' }
    } catch {
      localStorage.removeItem('access_token')
      localStorage.removeItem('shopping_agent_user')
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
})
