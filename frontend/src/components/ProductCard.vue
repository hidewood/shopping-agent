<script setup lang="ts">
import { computed, inject } from 'vue'

const props = defineProps<{
  product: Record<string, any>
  variant?: 'primary' | 'alternative'
  reason?: string[]
}>()

const openProduct = inject<(p: Record<string, any>, r: string[]) => void>('openProduct')

const imageUrl = computed(() => {
  const img = props.product.image || props.product.productImg || props.product.product_img
  return img ? `/images/${img}` : ''
})

function handleClick() {
  openProduct?.(props.product, props.reason || [])
}
</script>

<template>
  <div
    @click="handleClick"
    class="rounded-card bg-surface overflow-hidden shadow-soft card-hover cursor-pointer"
  >
    <!-- Image (visual-first) -->
    <div class="aspect-[4/5] bg-gray-100 relative">
      <img v-if="imageUrl" :src="imageUrl" :alt="product.name" class="w-full h-full object-cover" loading="lazy" />
      <div v-else class="w-full h-full flex items-center justify-center bg-gray-100">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
      </div>
      <span v-if="variant === 'primary'" class="absolute top-3 left-3 text-[11px] bg-brand-500 text-white px-2.5 py-1 rounded-full font-medium">推荐</span>
    </div>

    <!-- Body -->
    <div class="p-4">
      <h3 class="font-semibold text-[14px] text-ink leading-snug line-clamp-1">{{ product.name }}</h3>
      <div class="text-[16px] font-bold text-ink mt-1">${{ product.price?.toFixed(2) }}</div>

      <!-- Tags (克制配色) -->
      <div class="flex flex-wrap gap-1.5 mt-2.5">
        <span
          v-for="tag in (product.tags || []).slice(0, 3)"
          :key="tag"
          class="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-ink-muted font-medium"
        >
          {{ tag }}
        </span>
      </div>
    </div>
  </div>
</template>
