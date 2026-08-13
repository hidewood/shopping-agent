<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { adminOrders, adminShipOrder, adminDeliverOrder, adminUsers } from '../api'
import { useRouter } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
const orders = ref<any[]>([])
const users = ref<any[]>([])
const loading = ref(false)
const tab = ref<'orders' | 'users'>('orders')

const statusLabels: Record<string, { label: string; color: string }> = {
  confirmed: { label: '已确认', color: 'bg-emerald-50 text-emerald-600' },
  pending: { label: '待处理', color: 'bg-amber-50 text-amber-600' },
  shipped: { label: '已发货', color: 'bg-sky-50 text-sky-600' },
  delivered: { label: '已送达', color: 'bg-accent-50 text-accent-600' },
  cancelled: { label: '已取消', color: 'bg-gray-100 text-ink-muted' },
}

async function load() {
  loading.value = true
  try {
    orders.value = await adminOrders()
    users.value = await adminUsers()
  } finally {
    loading.value = false
  }
}

async function ship(id: string) {
  await adminShipOrder(id)
  load()
}

async function deliver(id: string) {
  await adminDeliverOrder(id)
  load()
}

onMounted(() => {
  if (auth.user?.role !== 'admin') { router.push('/chat'); return }
  load()
})
</script>

<template>
  <div class="h-full overflow-y-auto px-8 py-8">
    <div class="max-w-4xl mx-auto">
      <h1 class="text-2xl font-bold text-ink tracking-tight mb-1">管理后台</h1>
      <p class="text-sm text-ink-muted mb-6">管理员：{{ auth.user?.email }}</p>

      <!-- Tab 切换 -->
      <div class="flex gap-2 mb-6">
        <button @click="tab = 'orders'" :class="['px-4 py-2 rounded-full text-sm font-medium transition-colors cursor-pointer', tab === 'orders' ? 'bg-accent-500 text-white' : 'bg-surface text-ink-muted hover:bg-black/5']">订单管理</button>
        <button @click="tab = 'users'" :class="['px-4 py-2 rounded-full text-sm font-medium transition-colors cursor-pointer', tab === 'users' ? 'bg-accent-500 text-white' : 'bg-surface text-ink-muted hover:bg-black/5']">用户管理</button>
      </div>

      <!-- 订单管理 -->
      <div v-if="tab === 'orders'" class="space-y-4">
        <div v-for="order in orders" :key="order.order_id" class="bg-surface rounded-card shadow-soft p-5">
          <div class="flex items-center justify-between mb-3">
            <div class="font-mono text-sm text-ink-soft">{{ order.order_id }}</div>
            <div class="flex items-center gap-2">
              <span :class="['text-xs px-2.5 py-1 rounded-full font-medium', statusLabels[order.status]?.color]">
                {{ statusLabels[order.status]?.label || order.status }}
              </span>
              <button v-if="order.status === 'confirmed'" @click="ship(order.order_id)" class="text-xs px-3 py-1 rounded-full bg-sky-500 text-white hover:bg-sky-600 cursor-pointer">发货</button>
              <button v-if="order.status === 'shipped'" @click="deliver(order.order_id)" class="text-xs px-3 py-1 rounded-full bg-accent-500 text-white hover:bg-accent-700 cursor-pointer">送达</button>
            </div>
          </div>
          <div class="text-xs text-ink-faint mb-2">用户：{{ order.user_id?.slice(0, 8) }}…</div>
          <div class="space-y-1.5">
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
        <div v-if="!orders.length && !loading" class="text-center py-16 text-ink-muted">暂无订单</div>
      </div>

      <!-- 用户管理 -->
      <div v-else class="space-y-3">
        <div v-for="u in users" :key="u.id" class="bg-surface rounded-card shadow-soft p-4 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="w-9 h-9 rounded-full bg-accent-100 text-accent-600 flex items-center justify-center text-sm font-semibold">
              {{ (u.name || u.email || '?')[0].toUpperCase() }}
            </div>
            <div>
              <div class="text-sm font-medium text-ink">{{ u.name || '未设置昵称' }}</div>
              <div class="text-xs text-ink-muted">{{ u.email }}</div>
            </div>
          </div>
          <span :class="['text-xs px-2.5 py-1 rounded-full font-medium', u.role === 'admin' ? 'bg-accent-100 text-accent-600' : 'bg-gray-100 text-ink-muted']">
            {{ u.role === 'admin' ? '管理员' : '用户' }}
          </span>
        </div>
        <div v-if="!users.length && !loading" class="text-center py-16 text-ink-muted">暂无用户</div>
      </div>
    </div>
  </div>
</template>
