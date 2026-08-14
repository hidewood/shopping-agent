<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()

onMounted(() => auth.loadUser())

const items = computed(() => [
  { label: '发现', path: '/catalog', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z' },
  { label: '对话', path: '/chat', icon: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z' },
  { label: '收藏', path: '/favorites', icon: 'M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z' },
  ...(auth.user?.role === 'admin' ? [{ label: '管理', path: '/admin', icon: 'M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6' }] : []),
])

function navigate(path: string) {
  router.push(path)
}
</script>

<template>
  <nav class="fixed inset-x-4 bottom-4 z-30 flex items-center justify-around rounded-[28px] border border-white/80 bg-white/90 px-2 py-2 shadow-[0_10px_32px_rgba(0,0,0,0.12)] backdrop-blur-xl md:hidden">
    <button
      v-for="item in items"
      :key="item.path"
      class="flex min-w-12 flex-col items-center gap-1 rounded-2xl px-3 py-1.5 text-[10px] font-medium"
      :class="route.path === item.path ? 'bg-brand-50 text-brand-600' : 'text-ink-faint'"
      @click="navigate(item.path)"
    >
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="item.icon" /></svg>
      {{ item.label }}
    </button>
    <button class="flex h-10 w-10 items-center justify-center rounded-full bg-brand-500 text-white shadow-sm" title="新会话" @click="chat.reset(); navigate('/chat')">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
    </button>
  </nav>
</template>
