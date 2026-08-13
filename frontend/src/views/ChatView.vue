<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import ChatMessage from '../components/ChatMessage.vue'

const store = useChatStore()
const input = ref('')
const chatRef = ref<HTMLElement>()

onMounted(async () => {
  if (!store.conversationId) await store.init()
})

async function handleSend() {
  const text = input.value.trim()
  if (!text || store.loading) return
  input.value = ''
  await store.send(text)
  await nextTick()
  chatRef.value?.scrollTo({ top: chatRef.value.scrollHeight, behavior: 'smooth' })
}

function handleGuidance(text: string) {
  input.value = text
  handleSend()
}

const examples = [
  '海洋主题的马克杯，预算 20 以内',
  '推荐一件 T 恤，不限预算和风格',
  '给朋友挑一个生日礼物',
]
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 对话区 -->
    <div ref="chatRef" class="flex-1 overflow-y-auto px-6 py-6">
      <!-- 空状态：Apple 风首页 -->
      <div v-if="!store.messages.length" class="h-full flex flex-col items-center justify-center text-center">
        <h1 class="text-4xl font-bold text-ink tracking-tight mb-3">今天想买什么？</h1>
        <p class="text-ink-muted mb-12 text-[15px]">告诉我你的需求，我来帮你找到最合适的商品。</p>

        <!-- 大输入框 -->
        <div class="w-full max-w-xl mb-6">
          <div class="flex items-center gap-2 bg-surface rounded-input border border-hairline shadow-soft px-5 py-4 focus-within:border-accent-400 focus-within:ring-4 focus-within:ring-accent-100 transition-all">
            <input
              v-model="input"
              @keydown.enter="handleSend"
              placeholder="描述你想要的商品……"
              class="flex-1 text-[16px] bg-transparent focus:outline-none placeholder:text-ink-faint"
            />
            <button
              @click="handleSend"
              :disabled="!input.trim()"
              class="shrink-0 w-9 h-9 rounded-full bg-accent-500 text-white flex items-center justify-center disabled:opacity-30 disabled:cursor-not-allowed hover:bg-accent-700 transition-colors cursor-pointer"
              title="发送"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1v14M1 8l7-7 7 7" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
        </div>

        <!-- 示例 chips -->
        <div class="flex flex-wrap gap-2 justify-center max-w-xl">
          <button
            v-for="ex in examples"
            :key="ex"
            @click="input = ex; handleSend()"
            class="text-[13px] px-4 py-2 rounded-full border border-hairline bg-surface text-ink-muted hover:border-accent-300 hover:text-accent-600 transition-all cursor-pointer"
          >
            {{ ex }}
          </button>
        </div>
      </div>

      <!-- 消息流 -->
      <div v-else class="max-w-3xl mx-auto">
        <TransitionGroup name="msg">
          <ChatMessage v-for="(msg, i) in store.messages" :key="i" :message="msg" @send="handleGuidance" />
        </TransitionGroup>

        <!-- Loading -->
        <div v-if="store.loading" class="flex items-center gap-2.5 text-ink-muted">
          <div class="flex gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-accent-400 animate-bounce" style="animation-delay: 0ms"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-accent-400 animate-bounce" style="animation-delay: 150ms"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-accent-400 animate-bounce" style="animation-delay: 300ms"></span>
          </div>
          <span class="text-sm">正在为你挑选…</span>
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
          class="shrink-0 px-5 py-2.5 rounded-full bg-accent-500 text-white text-sm font-medium hover:bg-accent-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
        >发送</button>
      </div>
    </div>
  </div>
</template>
