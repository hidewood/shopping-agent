<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { listConversations, renameConversation, type ConversationSummary } from '../api'
import { useRouter } from 'vue-router'

const auth = useAuthStore()
const chat = useChatStore()
const router = useRouter()
const conversations = ref<ConversationSummary[]>([])
const loading = ref(false)
const editingId = ref('')
const draftTitle = ref('')

async function load() {
  if (!auth.token) { router.push('/login'); return }
  loading.value = true
  try {
    conversations.value = await listConversations()
  } finally {
    loading.value = false
  }
}

async function open(cid: string) {
  await chat.loadConversation(cid)
  router.push('/chat')
}

function startRename(conversation: ConversationSummary) {
  editingId.value = conversation.conversation_id
  draftTitle.value = conversation.title || '未命名会话'
}

async function saveRename(conversation: ConversationSummary) {
  const title = draftTitle.value.trim()
  if (!title) return
  const response = await renameConversation(conversation.conversation_id, title)
  conversation.title = response.title
  editingId.value = ''
}

function fmt(ts: string) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto px-8 py-8">
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-ink tracking-tight mb-1">历史会话</h1>
      <p class="text-sm text-ink-muted mb-6">{{ auth.user?.email }}</p>

      <div v-if="!conversations.length && !loading" class="text-center py-24">
        <p class="text-4xl mb-4 opacity-40">💬</p>
        <p class="text-ink-muted">还没有历史会话</p>
      </div>

      <div v-else class="space-y-3">
        <div
          v-for="c in conversations"
          :key="c.conversation_id"
          class="w-full bg-surface rounded-card shadow-soft p-4 flex items-center justify-between text-left hover:shadow-soft-hover transition-all cursor-pointer"
        >
          <div class="flex min-w-0 flex-1 items-center gap-3 text-left" @click="editingId !== c.conversation_id && open(c.conversation_id)">
            <div class="w-9 h-9 rounded-full bg-accent-100 text-accent-600 flex items-center justify-center text-sm">💬</div>
            <div>
              <input
                v-if="editingId === c.conversation_id"
                v-model="draftTitle"
                class="w-full max-w-sm rounded-lg border border-accent-300 bg-white px-2 py-1 text-sm text-ink focus:outline-none"
                @click.stop
                @keydown.enter.prevent="saveRename(c)"
                @keydown.esc.prevent="editingId = ''"
              />
              <div v-else class="text-sm font-medium text-ink">{{ c.title || '未命名会话' }}</div>
              <div class="text-xs text-ink-muted">更新于 {{ fmt(c.updated_at) }}</div>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <button
              v-if="editingId === c.conversation_id"
              class="text-xs font-medium text-accent-600"
              @click.stop="saveRename(c)"
            >保存</button>
            <button
              v-else
              class="text-xs text-ink-muted hover:text-ink"
              @click.stop="startRename(c)"
            >重命名</button>
            <span class="text-ink-faint text-sm">→</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
