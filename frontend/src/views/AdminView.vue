<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { adminOrders, adminShipOrder, adminDeliverOrder, adminUsers, listProducts, adminCreateProduct, adminUpdateProduct, adminDeleteProduct } from '../api'
import { useRouter } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
const orders = ref<any[]>([])
const users = ref<any[]>([])
const products = ref<any[]>([])
const totalProducts = ref(0)
const loading = ref(false)
const tab = ref<'orders' | 'users' | 'products'>('orders')

// 商品管理表单
const showForm = ref(false)
const editingId = ref<string | null>(null)
const form = ref({ name: '', item_type: 'mug', manufacturer: '', price: 0, tags: '', description: '' })
const formError = ref('')

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
    const prodData = await listProducts({ page: 1, page_size: 100 })
    products.value = prodData.products
    totalProducts.value = prodData.total
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

function openCreate() {
  editingId.value = null
  form.value = { name: '', item_type: 'mug', manufacturer: '', price: 0, tags: '', description: '' }
  formError.value = ''
  showForm.value = true
}

function openEdit(p: any) {
  editingId.value = p.product_id
  form.value = { name: p.name, item_type: p.item_type, manufacturer: p.manufacturer, price: p.price, tags: (p.tags || []).join(', '), description: p.description || '' }
  formError.value = ''
  showForm.value = true
}

async function submitForm() {
  formError.value = ''
  const data = {
    name: form.value.name,
    item_type: form.value.item_type,
    manufacturer: form.value.manufacturer,
    price: Number(form.value.price),
    tags: form.value.tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
    description: form.value.description,
  }
  try {
    if (editingId.value) {
      await adminUpdateProduct(editingId.value, data)
    } else {
      await adminCreateProduct(data)
    }
    showForm.value = false
    load()
  } catch (e: any) {
    formError.value = e?.response?.data?.detail || '操作失败'
  }
}

async function remove(p: any) {
  if (!confirm(`确定删除商品 ${p.name}（${p.product_id}）？`)) return
  await adminDeleteProduct(p.product_id)
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
        <button @click="tab = 'products'" :class="['px-4 py-2 rounded-full text-sm font-medium transition-colors cursor-pointer', tab === 'products' ? 'bg-accent-500 text-white' : 'bg-surface text-ink-muted hover:bg-black/5']">商品管理</button>
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

      <!-- 商品管理 -->
      <div v-else-if="tab === 'products'">
        <div class="flex items-center justify-between mb-4">
          <p class="text-sm text-ink-muted">共 {{ totalProducts }} 件商品（显示前 100 件）</p>
          <button @click="openCreate" class="px-4 py-2 rounded-full bg-accent-500 text-white text-sm font-medium hover:bg-accent-700 cursor-pointer">＋ 新增商品</button>
        </div>

        <!-- 新增/编辑表单 -->
        <div v-if="showForm" class="bg-surface rounded-card shadow-soft p-5 mb-4 space-y-3">
          <h3 class="text-sm font-semibold text-ink">{{ editingId ? '编辑商品' : '新增商品' }}</h3>
          <div class="grid grid-cols-2 gap-3">
            <input v-model="form.name" placeholder="商品名称" class="px-3 py-2 rounded-lg border border-hairline text-sm focus:outline-none focus:border-accent-400" />
            <select v-model="form.item_type" class="px-3 py-2 rounded-lg border border-hairline text-sm focus:outline-none">
              <option value="mug">mug</option>
              <option value="shirt">shirt</option>
            </select>
            <input v-model="form.manufacturer" placeholder="厂商" class="px-3 py-2 rounded-lg border border-hairline text-sm focus:outline-none focus:border-accent-400" />
            <input v-model.number="form.price" type="number" step="0.01" placeholder="价格" class="px-3 py-2 rounded-lg border border-hairline text-sm focus:outline-none focus:border-accent-400" />
          </div>
          <input v-model="form.tags" placeholder="标签（逗号分隔）" class="w-full px-3 py-2 rounded-lg border border-hairline text-sm focus:outline-none focus:border-accent-400" />
          <input v-model="form.description" placeholder="描述（可选）" class="w-full px-3 py-2 rounded-lg border border-hairline text-sm focus:outline-none focus:border-accent-400" />
          <p v-if="formError" class="text-sm text-red-500">{{ formError }}</p>
          <div class="flex gap-2">
            <button @click="submitForm" class="px-4 py-2 rounded-full bg-accent-500 text-white text-sm font-medium hover:bg-accent-700 cursor-pointer">保存</button>
            <button @click="showForm = false" class="px-4 py-2 rounded-full border border-hairline text-ink-soft text-sm cursor-pointer">取消</button>
          </div>
        </div>

        <!-- 商品列表 -->
        <div class="space-y-2.5">
          <div v-for="p in products" :key="p.product_id" class="bg-surface rounded-card shadow-soft p-4 flex items-center gap-4">
            <img v-if="p.image" :src="`/images/${p.image}`" class="w-12 h-12 rounded-lg object-cover bg-gray-100" />
            <div v-else class="w-12 h-12 rounded-lg bg-gray-100 flex items-center justify-center"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg></div>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-ink truncate">{{ p.name }}</div>
              <div class="text-xs text-ink-muted">{{ p.product_id }} · {{ p.item_type }} · {{ p.manufacturer }}</div>
            </div>
            <div class="text-sm font-semibold text-ink">${{ p.price?.toFixed(2) }}</div>
            <div class="flex gap-2">
              <button @click="openEdit(p)" class="text-xs px-3 py-1 rounded-full border border-hairline text-ink-soft hover:bg-black/5 cursor-pointer">编辑</button>
              <button @click="remove(p)" class="text-xs px-3 py-1 rounded-full border border-red-200 text-red-500 hover:bg-red-50 cursor-pointer">删除</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
