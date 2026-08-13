<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { listOrders, cancelOrder } from '../api'
import { useRouter } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
const orders = ref<any[]>([])
const loading = ref(false)

const statusLabels: Record<string, { label: string; color: string }> = {
  confirmed: { label: '已确认', color: 'bg-emerald-50 text-emerald-600' },
  pending: { label: '待处理', color: 'bg-amber-50 text-amber-600' },
  shipped: { label: '已发货', color: 'bg-sky-50 text-sky-600' },
  delivered: { label: '已送达', color: 'bg-accent-50 text-accent-600' },
  cancelled: { label: '已取消', color: 'bg-gray-100 text-ink-muted' },
}

async function load() {
  if (!auth.token) { router.push('/login'); return }
  loading.value = true
  try {
    orders.value = await listOrders()
  } finally {
    loading.value = false
  }
}

async function cancel(id: string) {
  await cancelOrder(id)
  load()
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto px-8 py-8">
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-ink tracking-tight mb-1">我的订单</h1>
      <p class="text-sm text-ink-muted mb-6">{{ auth.user?.email }}</p>

      <div v-if="!orders.length && !loading" class="text-center py-24">
        <p class="text-4xl mb-4 opacity-40">📦</p>
        <p class="text-ink-muted">还没有订单</p>
      </div>

      <div v-else class="space-y-4">
        <div v-for="order in orders" :key="order.order_id" class="bg-surface rounded-card shadow-soft p-5">
          <div class="flex items-center justify-between mb-3">
            <div class="font-mono text-sm text-ink-soft">{{ order.order_id }}</div>
            <div class="flex items-center gap-2">
              <span :class="['text-xs px-2.5 py-1 rounded-full font-medium', statusLabels[order.status]?.color]">
                {{ statusLabels[order.status]?.label || order.status }}
              </span>
              <button v-if="order.status === 'confirmed' || order.status === 'pending'" @click="cancel(order.order_id)" class="text-xs text-ink-faint hover:text-red-500 cursor-pointer">取消</button>
            </div>
          </div>
          <div class="space-y-2">
            <div v-for="item in order.items" :key="item.product_id" class="flex items-center gap-3 text-sm">
              <img v-if="item.product?.image" :src="`/images/${item.product.image}`" class="w-10 h-10 rounded-lg object-cover bg-gray-100" />
              <span class="flex-1 text-ink-soft truncate">{{ item.product?.name }}</span>
              <span class="text-ink-muted">×{{ item.quantity }}</span>
              <span class="text-ink-soft">${{ item.unit_price?.toFixed(2) }}</span>
            </div>
          </div>
          <div class="border-t border-hairline mt-3 pt-3 text-right">
            <span class="text-sm text-ink-muted">总价 </span>
            <span class="text-lg font-bold text-ink">${{ order.total_price?.toFixed(2) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
