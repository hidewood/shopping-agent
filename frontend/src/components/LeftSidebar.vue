<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const store = useChatStore()
const auth = useAuthStore()

const collapsed = ref(true)

onMounted(() => auth.loadUser())

const navItems = [
  { key: 'new', label: '新会话', action: () => store.reset() },
  { key: 'chat', label: '购物对话', action: () => router.push('/chat') },
  { key: 'catalog', label: '商品库', action: () => router.push('/catalog') },
  { key: 'cart', label: '购物车', action: () => router.push('/cart') },
  { key: 'favorites', label: '收藏', action: () => router.push('/favorites') },
  { key: 'orders', label: '订单', action: () => router.push('/orders') },
  { key: 'history', label: '历史会话', action: () => router.push('/history') },
  { key: 'admin', label: '管理中心', action: () => router.push('/admin') },
]

const icons: Record<string, string> = {
  new: 'M12 5v14M5 12h14',
  chat: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
  catalog: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  cart: 'M9 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM20 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6',
  orders: 'M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM14 2v6h6',
  favorites: 'M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z',
  admin: 'M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6',
  history: 'M12 8v4l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
}

function isActive(key: string) {
  const map: Record<string, string> = { chat: '/chat', catalog: '/catalog', cart: '/cart', favorites: '/favorites', orders: '/orders', history: '/history', admin: '/admin' }
  return route.path === map[key]
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <aside
    :class="['shrink-0 border-r border-white/60 bg-white/65 backdrop-blur-xl flex flex-col transition-all duration-300', collapsed ? 'w-[68px]' : 'w-60']"
  >
    <!-- Logo + 折叠按钮 -->
    <div :class="['flex items-center py-5', collapsed ? 'flex-col gap-4 px-3' : 'gap-3 px-5']">
      <div class="w-9 h-9 rounded-xl bg-accent-500 flex items-center justify-center shadow-soft shrink-0">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
      </div>
      <div v-if="!collapsed" class="leading-tight flex-1">
        <div class="font-semibold text-[15px] text-ink tracking-tight">购物 Agent</div>
        <div class="text-[11px] text-ink-faint">AI Shopping</div>
      </div>
      <!-- 折叠/展开按钮（明显） -->
      <button
        @click="collapsed = !collapsed"
        :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
        class="shrink-0 w-8 h-8 rounded-lg bg-gray-100 hover:bg-accent-100 hover:text-accent-600 text-ink-soft flex items-center justify-center transition-colors cursor-pointer"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          :style="{ transform: collapsed ? 'rotate(180deg)' : 'none', transition: 'transform 0.3s' }">
          <path d="M11 17l-5-5 5-5M18 17l-5-5 5-5"/>
        </svg>
      </button>
    </div>

    <!-- Nav -->
    <nav class="flex-1 px-3 space-y-1">
      <template v-for="item in navItems" :key="item.key">
        <button
          v-if="item.key !== 'admin' || auth.user?.role === 'admin'"
          @click="item.action"
          :title="collapsed ? item.label : ''"
          class="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-[14px] transition-colors text-left text-ink-soft hover:bg-black/[0.04] cursor-pointer"
          :class="[
            isActive(item.key) ? 'bg-accent-50 text-accent-600 font-medium' : '',
            collapsed ? 'justify-center px-0' : '',
          ]"
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
            :stroke="isActive(item.key) ? '#007AFF' : 'currentColor'"
            stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path :d="icons[item.key]" />
          </svg>
          <span v-if="!collapsed" class="flex-1">{{ item.label }}</span>
        </button>
      </template>
    </nav>

    <!-- 底部：登录/用户状态 -->
    <div class="p-3 border-t border-white/60">
      <div v-if="!auth.user" class="flex" :class="collapsed ? 'justify-center' : ''">
        <button @click="router.push('/login')" class="flex-1 py-2 rounded-xl bg-accent-500 text-white text-sm font-medium hover:bg-accent-700 transition-colors cursor-pointer flex items-center justify-center">
          <span v-if="!collapsed">登录</span>
          <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="icons.user"/></svg>
        </button>
      </div>
      <div v-else :class="['flex items-center gap-2.5', collapsed ? 'justify-center' : 'px-1']">
        <div class="w-8 h-8 rounded-full bg-accent-100 text-accent-600 flex items-center justify-center text-xs font-semibold shrink-0">
          {{ (auth.user.name || auth.user.email || '?')[0].toUpperCase() }}
        </div>
        <div v-if="!collapsed" class="flex-1 min-w-0">
          <div class="text-[13px] text-ink font-medium truncate">{{ auth.user.name || auth.user.email }}</div>
          <div class="flex items-center gap-2">
            <span v-if="auth.user.role === 'admin'" class="text-[10px] font-medium text-accent-600">管理员</span>
            <button @click="logout" class="text-[11px] text-ink-faint hover:text-red-500 cursor-pointer">退出登录</button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
