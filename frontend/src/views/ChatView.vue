<script setup lang="ts">
import { ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useChatStore } from '../stores/chat'
import ChatMessage from '../components/ChatMessage.vue'

const store = useChatStore()
const input = ref('')
const chatRef = ref<HTMLElement>()

onMounted(async () => {
  if (!store.conversationId) await store.init()
  await nextTick()
  chatRef.value?.scrollTo({ top: store.scrollTop })
})

onBeforeUnmount(() => {
  store.scrollTop = chatRef.value?.scrollTop || 0
})

async function handleSend() {
  const text = input.value.trim()
  if (!text || store.loading) return
  input.value = ''
  await store.send(text)
  await nextTick()
  chatRef.value?.scrollTo({ top: chatRef.value.scrollHeight, behavior: 'smooth' })
}

</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 对话区 -->
    <div ref="chatRef" class="flex-1 overflow-y-auto px-6 py-6">
      <!-- 空状态：纯净的 Apple 风输入入口 -->
      <div v-if="!store.messages.length" class="h-full flex flex-col items-center justify-center text-center px-6">
        <div class="mb-7 flex h-14 w-14 items-center justify-center rounded-[22px] bg-brand-50 text-brand-600 shadow-soft">
          <svg width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
        </div>
        <h1 class="text-4xl font-bold text-ink tracking-tight mb-3">今天想找什么？</h1>
        <p class="text-ink-muted mb-10 text-[15px]">告诉我商品、预算或喜欢的风格。</p>

        <!-- 大输入框 -->
        <div class="w-full max-w-xl mb-6">
          <div class="flex items-center gap-2 bg-surface rounded-[28px] border border-hairline shadow-soft px-5 py-4 focus-within:border-brand-400 focus-within:ring-4 focus-within:ring-brand-50 transition-all">
            <input
              v-model="input"
              @keydown.enter="handleSend"
              placeholder="描述你想要的商品……"
              class="flex-1 text-[16px] bg-transparent focus:outline-none placeholder:text-ink-faint"
            />
            <button
              @click="handleSend"
              :disabled="!input.trim()"
              class="shrink-0 w-10 h-10 rounded-full bg-brand-500 text-white flex items-center justify-center disabled:opacity-30 disabled:cursor-not-allowed hover:bg-brand-700 transition-colors cursor-pointer"
              title="发送"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1v14M1 8l7-7 7 7" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
        </div>

      </div>

      <!-- 消息流 -->
      <div v-else class="max-w-3xl mx-auto">
        <TransitionGroup name="msg">
          <ChatMessage v-for="(msg, i) in store.messages" :key="i" :message="msg" @retry="store.retry" />
        </TransitionGroup>

        <!-- Loading -->
        <div v-if="store.loading" class="flex items-center gap-2.5 text-ink-muted">
          <div class="flex gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-accent-400 animate-bounce" style="animation-delay: 0ms"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-accent-400 animate-bounce" style="animation-delay: 150ms"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-accent-400 animate-bounce" style="animation-delay: 300ms"></span>
          </div>
          <span class="text-sm">{{ store.progressText || '正在处理…' }}</span>
          <button @click="store.cancel" class="ml-2 text-xs text-ink-faint hover:text-ink cursor-pointer">取消</button>
        </div>
      </div>
    </div>

    <!-- 底部输入框（有消息时） -->
    <div v-if="store.messages.length" class="border-t border-white/60 bg-white/70 backdrop-blur-xl px-6 py-4">
      <div class="max-w-3xl mx-auto flex items-center gap-3">
        <input
          v-model="input"
          @keydown.enter="handleSend"
          :disabled="store.loading"
          placeholder="继续描述需求……"
          class="flex-1 text-[14px] bg-surface rounded-input border border-hairline px-4 py-2.5 focus:outline-none focus:border-accent-400 focus:ring-4 focus:ring-accent-100 disabled:opacity-50 transition-all"
        />
        <button
          @click="handleSend"
          :disabled="!input.trim() || store.loading"
          class="shrink-0 px-5 py-2.5 rounded-full bg-brand-500 text-white text-sm font-medium hover:bg-brand-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
        >发送</button>
      </div>
    </div>
  </div>
</template>
