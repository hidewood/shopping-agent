import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    allowedHosts: true, // 允许通过 Cloudflare Tunnel 域名访问（否则 Host 校验会拦 comgender-blog.top）
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/images': 'http://localhost:8000',
      '/avatars': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/cart': 'http://localhost:8000',
      '/orders': 'http://localhost:8000',
      '/favorites': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
    },
  },
})
