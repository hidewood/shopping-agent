<script setup lang="ts">
import { ref, provide, onMounted } from 'vue'
import { useChatStore } from './stores/chat'
import LeftSidebar from './components/LeftSidebar.vue'
import ProductDetailPanel from './components/ProductDetailPanel.vue'

const store = useChatStore()

// 右侧详情栏状态：默认折叠，点击商品卡片后展开
const selectedProduct = ref<Record<string, any> | null>(null)
const selectedReason = ref<string[]>([])

function openProduct(product: Record<string, any>, reason: string[] = []) {
  selectedProduct.value = product
  selectedReason.value = reason
}

provide('openProduct', openProduct)

onMounted(async () => {
  if (!store.conversationId) await store.init()
})
</script>

<template>
  <div class="h-screen flex bg-canvas overflow-hidden">
    <!-- 左导航 -->
    <LeftSidebar />

    <!-- 中间工作区 -->
    <main class="flex-1 flex flex-col min-w-0 overflow-hidden">
      <router-view />
    </main>

    <!-- 右详情栏：点击商品后展开 -->
    <Transition name="panel">
      <ProductDetailPanel
        v-if="selectedProduct"
        :product="selectedProduct"
        :reason="selectedReason"
        @close="selectedProduct = null"
      />
    </Transition>
  </div>
</template>

<style scoped>
.panel-enter-active,
.panel-leave-active {
  transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
}
.panel-enter-from,
.panel-leave-to {
  width: 0;
  opacity: 0;
}
</style>
