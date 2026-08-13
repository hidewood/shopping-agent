<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { getFavorites, removeFavorite } from '../api'
import { useRouter } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
const favorites = ref<any[]>([])
const loading = ref(false)

async function load() {
  if (!auth.token) { router.push('/login'); return }
  loading.value = true
  try {
    favorites.value = await getFavorites()
  } finally {
    loading.value = false
  }
}

async function remove(item: any) {
  await removeFavorite(item.product_id)
  load()
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto px-8 py-8">
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-ink tracking-tight mb-1">收藏</h1>
      <p class="text-sm text-ink-muted mb-6">{{ auth.user?.email }}</p>

      <!-- 空状态 -->
      <div v-if="!favorites.length && !loading" class="text-center py-24">
        <p class="text-4xl mb-4 opacity-40">🤍</p>
        <p class="text-ink-muted mb-4">还没有收藏商品</p>
        <button @click="router.push('/catalog')" class="px-5 py-2.5 rounded-full bg-accent-500 text-white text-sm font-medium hover:bg-accent-700 cursor-pointer">去逛逛</button>
      </div>

      <!-- 列表 -->
      <div v-else class="space-y-3">
        <div v-for="item in favorites" :key="item.product_id" class="bg-surface rounded-card shadow-soft p-4 flex items-center gap-4">
          <img v-if="item.product?.image" :src="`/images/${item.product.image}`" class="w-16 h-16 rounded-xl object-cover bg-gray-100" />
          <div v-else class="w-16 h-16 rounded-xl bg-gray-100 flex items-center justify-center"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg></div>
          <div class="flex-1 min-w-0">
            <div class="font-medium text-ink text-sm truncate">{{ item.product?.name }}</div>
            <div class="text-ink-muted text-xs">{{ item.product?.product_id }}</div>
            <div class="font-semibold text-ink text-sm mt-1">${{ item.product?.price?.toFixed(2) }}</div>
          </div>
          <button @click="remove(item)" class="text-ink-faint hover:text-red-500 cursor-pointer" title="取消收藏">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
