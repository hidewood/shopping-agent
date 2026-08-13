<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { getCart, updateCartItem, removeCartItem, clearCart, createOrder } from '../api'
import { useRouter } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
const cart = ref<any>(null)
const loading = ref(false)

async function load() {
  if (!auth.token) { router.push('/login'); return }
  loading.value = true
  try {
    cart.value = await getCart()
  } finally {
    loading.value = false
  }
}

async function changeQty(item: any, delta: number) {
  const q = item.quantity + delta
  if (q < 1) return
  cart.value = await updateCartItem(item.id, q)
}

async function remove(item: any) {
  cart.value = await removeCartItem(item.id)
}

async function checkout() {
  if (!cart.value?.items?.length) return
  await createOrder()
  router.push('/orders')
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto px-8 py-8">
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-ink tracking-tight mb-1">购物车</h1>
      <p class="text-sm text-ink-muted mb-6">{{ auth.user?.email }}</p>

      <!-- 空状态 -->
      <div v-if="!cart?.items?.length && !loading" class="text-center py-24">
        <p class="text-4xl mb-4 opacity-40">🛒</p>
        <p class="text-ink-muted mb-4">购物车是空的</p>
        <button @click="router.push('/catalog')" class="px-5 py-2.5 rounded-full bg-accent-500 text-white text-sm font-medium hover:bg-accent-700 cursor-pointer">去逛逛</button>
      </div>

      <!-- 列表 -->
      <div v-else class="space-y-3">
        <div v-for="item in cart?.items" :key="item.id" class="bg-surface rounded-card shadow-soft p-4 flex items-center gap-4">
          <img v-if="item.product?.image" :src="`/images/${item.product.image}`" class="w-16 h-16 rounded-xl object-cover bg-gray-100" />
          <div v-else class="w-16 h-16 rounded-xl bg-gray-100 flex items-center justify-center"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg></div>
          <div class="flex-1 min-w-0">
            <div class="font-medium text-ink text-sm truncate">{{ item.product?.name }}</div>
            <div class="text-ink-muted text-xs">{{ item.product?.product_id }}</div>
            <div class="font-semibold text-ink text-sm mt-1">${{ item.product?.price?.toFixed(2) }}</div>
          </div>
          <!-- 数量控制 -->
          <div class="flex items-center gap-2">
            <button @click="changeQty(item, -1)" class="w-7 h-7 rounded-full bg-gray-100 hover:bg-gray-200 text-ink-soft cursor-pointer">−</button>
            <span class="w-6 text-center text-sm font-medium">{{ item.quantity }}</span>
            <button @click="changeQty(item, 1)" class="w-7 h-7 rounded-full bg-gray-100 hover:bg-gray-200 text-ink-soft cursor-pointer">＋</button>
          </div>
          <button @click="remove(item)" class="text-ink-faint hover:text-red-500 text-sm cursor-pointer">移除</button>
        </div>

        <!-- 合计 + 结算 -->
        <div class="bg-surface rounded-card shadow-soft p-5 flex items-center justify-between">
          <div>
            <div class="text-sm text-ink-muted">合计</div>
            <div class="text-xl font-bold text-ink">${{ cart?.total_price?.toFixed(2) }}</div>
          </div>
          <button @click="checkout" class="px-6 py-3 rounded-full bg-accent-500 text-white text-sm font-semibold hover:bg-accent-700 cursor-pointer">下单（模拟）</button>
        </div>
      </div>
    </div>
  </div>
</template>
