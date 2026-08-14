import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
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
