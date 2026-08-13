<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const mode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const name = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(email.value, password.value)
    } else {
      await auth.register(email.value, password.value, name.value || undefined)
    }
    router.push('/')
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '操作失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="h-full flex items-center justify-center">
    <div class="w-full max-w-sm">
      <h1 class="text-2xl font-bold text-ink text-center mb-1 tracking-tight">
        {{ mode === 'login' ? '欢迎回来' : '创建账户' }}
      </h1>
      <p class="text-ink-muted text-center text-sm mb-8">
        {{ mode === 'login' ? '登录以同步你的购物车和订单' : '注册后开始你的购物之旅' }}
      </p>

      <!-- 表单 -->
      <div class="bg-surface rounded-card shadow-soft p-6 space-y-4">
        <div v-if="mode === 'register'">
          <label class="text-xs text-ink-muted mb-1 block">昵称</label>
          <input v-model="name" placeholder="可选" class="w-full px-4 py-2.5 rounded-input border border-hairline text-sm focus:outline-none focus:border-accent-400 focus:ring-4 focus:ring-accent-100" />
        </div>
        <div>
          <label class="text-xs text-ink-muted mb-1 block">邮箱</label>
          <input v-model="email" type="email" placeholder="you@example.com" class="w-full px-4 py-2.5 rounded-input border border-hairline text-sm focus:outline-none focus:border-accent-400 focus:ring-4 focus:ring-accent-100" />
        </div>
        <div>
          <label class="text-xs text-ink-muted mb-1 block">密码</label>
          <input v-model="password" type="password" placeholder="至少 6 位" class="w-full px-4 py-2.5 rounded-input border border-hairline text-sm focus:outline-none focus:border-accent-400 focus:ring-4 focus:ring-accent-100" @keydown.enter="submit" />
        </div>

        <p v-if="error" class="text-sm text-red-500">{{ error }}</p>

        <button @click="submit" :disabled="loading" class="w-full py-3 rounded-full bg-accent-500 text-white text-sm font-semibold hover:bg-accent-700 disabled:opacity-50 transition-colors cursor-pointer">
          {{ loading ? '处理中…' : mode === 'login' ? '登录' : '注册' }}
        </button>

        <p class="text-center text-sm text-ink-muted">
          {{ mode === 'login' ? '还没有账户？' : '已有账户？' }}
          <button @click="mode = mode === 'login' ? 'register' : 'login'; error = ''" class="text-accent-600 font-medium cursor-pointer hover:underline">
            {{ mode === 'login' ? '注册' : '登录' }}
          </button>
        </p>
      </div>
    </div>
  </div>
</template>
