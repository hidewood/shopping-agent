<script setup lang="ts">
import ProductCard from './ProductCard.vue'

const props = defineProps<{
  message: {
    role: 'user' | 'assistant'
    content: string
    responseType?: string
    productId?: string | null
    trace?: Record<string, any>[]
    products?: Record<string, any>[]
    alternatives?: Record<string, any>[]
    bundle?: Record<string, any> | null
    error?: { code: string; retriable: boolean } | null
    retryText?: string
    retryId?: string
  }
}>()
const emit = defineEmits<{ (e: 'retry', text: string, requestId?: string): void }>()

</script>

<template>
  <!-- 用户消息：右对齐 -->
  <div v-if="message.role === 'user'" class="flex justify-end items-start gap-3 mb-5">
    <div class="max-w-[75%] bg-brand-500 text-white rounded-[22px] rounded-br-md px-5 py-3">
      <p class="text-[14px] leading-relaxed">{{ message.content }}</p>
    </div>
  </div>

  <!-- 助手消息：左对齐，使用统一的无 IP 标记 -->
  <div v-else class="flex items-start gap-3 mb-6">
    <div class="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
    </div>
    <div class="flex-1 min-w-0 max-w-[80%]">
      <!-- 白色气泡（文字，宽度自适应） -->
      <div class="inline-block max-w-full bg-surface text-ink rounded-[22px] rounded-bl-md px-5 py-3.5 shadow-soft border border-hairline">
        <p class="text-[15px] leading-relaxed whitespace-pre-wrap">{{ message.content }}</p>
      </div>
      <button v-if="message.error?.retriable && message.retryText" @click="emit('retry', message.retryText, message.retryId)"
        class="mt-2 block text-sm font-medium text-brand-600 hover:text-brand-700 cursor-pointer">
        重试本次请求
      </button>

      <!-- 多件推荐按对象分组，避免用户把两张卡片的用途弄混。 -->
      <div v-if="message.bundle?.items?.length" class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-4">
        <section v-for="(item, i) in message.bundle.items" :key="item.product?.product_id || i" class="min-w-0">
          <p class="mb-2 text-sm font-medium text-ink-soft">{{ item.recipient || item.item_type }}{{ item.quantity > 1 ? ` · ${item.quantity} 件` : '' }}</p>
          <ProductCard v-if="item.product" :product="item.product" variant="primary" />
        </section>
      </div>

      <!-- 单件或候选商品卡片（气泡外） -->
      <div v-else-if="message.products?.length" class="mt-3">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <ProductCard
            v-for="(p, i) in message.products"
            :key="p.product_id"
            :product="p"
            :variant="i === 0 ? 'primary' : 'alternative'"
          />
        </div>
      </div>

      <!-- 无匹配的最近结果 -->
      <div v-if="message.alternatives?.length" class="mt-3">
        <p class="text-sm text-ink-muted mb-2">放宽条件后的最近结果</p>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <ProductCard
            v-for="alt in message.alternatives"
            :key="alt.products?.[0]?.product_id"
            :product="alt.products?.[0]"
            variant="alternative"
          />
        </div>
      </div>
    </div>
  </div>
</template>
