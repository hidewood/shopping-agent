<script setup lang="ts">
import { computed } from 'vue'
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
  }
}>()

// 简洁化：推荐类只显示结论首句（中文句号），其余类型显示完整自然语言
const headline = computed(() => {
  const content = props.message.content
  if (props.message.responseType === 'recommendation' || props.message.responseType === 'bundle_recommendation') {
    const idx = content.indexOf('。')
    return idx > 0 ? content.slice(0, idx + 1) : content
  }
  return content
})

const userAvatar = '/avatars/snoopy.png'
const botAvatar = '/avatars/哆啦A梦.png'
</script>

<template>
  <!-- 用户消息：右对齐，snoopy 头像在右 -->
  <div v-if="message.role === 'user'" class="flex justify-end items-start gap-3 mb-5">
    <div class="max-w-[75%] bg-brand-500 text-white rounded-[20px] rounded-br-md px-5 py-3">
      <p class="text-[14px] leading-relaxed">{{ message.content }}</p>
    </div>
    <img :src="userAvatar" alt="用户" class="w-12 h-12 rounded-full object-cover shrink-0" />
  </div>

  <!-- 助手消息：左对齐，哆啦A梦 头像在左 -->
  <div v-else class="flex items-start gap-3 mb-6">
    <img :src="botAvatar" alt="客服" class="w-12 h-12 rounded-full object-cover shrink-0" />
    <div class="flex-1 min-w-0 max-w-[80%]">
      <!-- 白色气泡（文字，宽度自适应） -->
      <div class="inline-block max-w-full bg-surface text-ink rounded-[20px] rounded-bl-md px-5 py-3.5 shadow-soft border border-hairline">
        <p class="text-[15px] leading-relaxed whitespace-pre-wrap">{{ headline }}</p>
      </div>

      <!-- 商品卡片（气泡外） -->
      <div v-if="message.products?.length" class="mt-3">
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
