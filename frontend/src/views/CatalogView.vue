<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { listProducts, getFacets } from '../api'
import ProductCard from '../components/ProductCard.vue'

interface Product { product_id: string; name: string; price: number; tags: string[]; manufacturer: string; item_type: string; description: string; [key: string]: any }

const products = ref<Product[]>([])
const total = ref(0)
const page = ref(1)
const query = ref('')
const itemType = ref('')
const itemTypes = ref<string[]>([])
const loading = ref(false)
const pageSize = 24

onMounted(async () => {
  const facets = await getFacets()
  itemTypes.value = facets.item_types || []
  await load()
})

async function load() {
  loading.value = true
  try {
    const data = await listProducts({ q: query.value, item_type: itemType.value, page: page.value, page_size: pageSize })
    products.value = data.products
    total.value = data.total
  } finally {
    loading.value = false
  }
}

watch([query, itemType], () => { page.value = 1; load() })
watch(page, () => load())

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
</script>

<template>
  <div class="h-full overflow-y-auto px-8 py-8">
    <div class="max-w-5xl mx-auto">
      <!-- Header -->
      <h1 class="text-2xl font-bold text-ink tracking-tight mb-1">商品库</h1>
      <p class="text-sm text-ink-muted mb-6">浏览全部 {{ total }} 件商品</p>

      <!-- Filters -->
      <div class="flex flex-col sm:flex-row gap-3 mb-7">
        <div class="flex-1 relative">
          <span class="absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          </span>
          <input
            v-model="query"
            placeholder="搜索名称、厂商、标签或描述"
            class="w-full pl-11 pr-4 py-2.5 rounded-input border border-hairline bg-surface text-sm focus:outline-none focus:border-accent-400 focus:ring-4 focus:ring-accent-100"
          />
        </div>
        <select
          v-model="itemType"
          class="px-4 py-2.5 rounded-input border border-hairline bg-surface text-sm focus:outline-none focus:border-accent-400 min-w-[140px]"
        >
          <option value="">全部类型</option>
          <option v-for="t in itemTypes" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>

      <!-- Grid -->
      <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-5">
        <ProductCard v-for="p in products" :key="p.product_id" :product="p" />
      </div>

      <!-- Empty -->
      <div v-if="!loading && !products.length" class="text-center py-24">
        <p class="text-ink-muted">没有找到匹配的商品</p>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="text-center py-12 text-ink-faint">加载中…</div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-4 mt-10">
        <button
          :disabled="page <= 1"
          @click="page--"
          class="px-5 py-2 text-sm rounded-full border border-hairline bg-surface disabled:opacity-30 hover:bg-black/[0.03] transition-colors cursor-pointer disabled:cursor-not-allowed"
        >上一页</button>
        <span class="text-sm text-ink-muted">{{ page }} / {{ totalPages }}</span>
        <button
          :disabled="page >= totalPages"
          @click="page++"
          class="px-5 py-2 text-sm rounded-full border border-hairline bg-surface disabled:opacity-30 hover:bg-black/[0.03] transition-colors cursor-pointer disabled:cursor-not-allowed"
        >下一页</button>
      </div>
    </div>
  </div>
</template>
