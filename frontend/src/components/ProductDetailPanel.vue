<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  product: Record<string, any> | null
  reason?: string[]
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const imageUrl = computed(() => {
  if (!props.product) return ''
  const img = props.product.image || props.product.productImg || props.product.product_img
  return img ? `/images/${img}` : ''
})
</script>

<template>
  <aside class="w-[340px] shrink-0 border-l border-white/60 bg-white/65 backdrop-blur-xl overflow-y-auto">
    <div v-if="product" class="p-6">
      <!-- Close -->
      <div class="flex justify-between items-center mb-5">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">商品详情</span>
        <button @click="emit('close')" class="w-7 h-7 rounded-full hover:bg-black/5 flex items-center justify-center text-ink-faint cursor-pointer">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- 大图（视觉优先） -->
      <div v-if="imageUrl" class="rounded-card overflow-hidden bg-gray-100 mb-5 aspect-square">
        <img :src="imageUrl" :alt="product.name" class="w-full h-full object-cover" />
      </div>
      <div v-else class="rounded-card bg-gray-100 aspect-square mb-5 flex items-center justify-center">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
      </div>

      <!-- 名称 + 价格 -->
      <h2 class="text-xl font-bold text-ink leading-snug mb-2">{{ product.name }}</h2>
      <div class="text-2xl font-bold text-ink mb-3">${{ product.price?.toFixed(2) }}</div>
      <div class="text-[13px] text-ink-muted mb-5">{{ product.product_id }} · {{ product.item_type }} · {{ product.manufacturer }}</div>

      <!-- 标签 -->
      <div class="flex flex-wrap gap-1.5 mb-5">
        <span v-for="tag in (product.tags || [])" :key="tag"
          class="text-[12px] px-2.5 py-1 rounded-full bg-gray-100 text-ink-muted font-medium">
          {{ tag }}
        </span>
      </div>

      <!-- 扩展字段（书籍） -->
      <div v-if="product.author || product.genre || product.pages || product.isbn" class="border-t border-hairline pt-4 mb-5 space-y-2 text-sm">
        <div v-if="product.author" class="flex gap-3"><span class="text-ink-faint w-14 shrink-0">作者</span><span class="text-ink-soft">{{ product.author }}</span></div>
        <div v-if="product.genre" class="flex gap-3"><span class="text-ink-faint w-14 shrink-0">类型</span><span class="text-ink-soft">{{ product.genre }}</span></div>
        <div v-if="product.pages" class="flex gap-3"><span class="text-ink-faint w-14 shrink-0">页数</span><span class="text-ink-soft">{{ product.pages }}</span></div>
        <div v-if="product.isbn" class="flex gap-3"><span class="text-ink-faint w-14 shrink-0">ISBN</span><span class="text-ink-soft font-mono text-xs">{{ product.isbn }}</span></div>
      </div>

      <!-- 推荐理由 -->
      <div v-if="reason?.length" class="border-t border-hairline pt-4 mb-5">
        <div class="text-[12px] font-semibold text-accent-600 mb-2.5">为什么适合你</div>
        <ul class="space-y-2">
          <li v-for="r in reason" :key="r" class="flex items-start gap-2 text-sm text-ink-soft">
            <span class="text-accent-500 mt-0.5 shrink-0">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
            </span>
            <span>{{ r }}</span>
          </li>
        </ul>
      </div>

      <!-- 描述 -->
      <p v-if="product.description" class="text-sm text-ink-muted leading-relaxed border-t border-hairline pt-4 mb-5">
        {{ product.description }}
      </p>

      <!-- CTA -->
      <button class="w-full py-3.5 rounded-full bg-accent-500 text-white text-[15px] font-semibold hover:bg-accent-700 transition-colors cursor-pointer">
        加入购物车
      </button>
    </div>

    <div v-else class="h-full flex flex-col items-center justify-center text-center px-6">
      <div class="w-14 h-14 rounded-2xl bg-gray-100 mb-4 flex items-center justify-center">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
      </div>
      <p class="text-sm text-ink-faint">点击商品卡片<br/>查看详情</p>
    </div>
  </aside>
</template>
