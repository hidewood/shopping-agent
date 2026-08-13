/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts,js}'],
  theme: {
    extend: {
      colors: {
        // Soft Minimal AI Commerce — Apple 式克制
        canvas: '#ECECEE',
        surface: '#FFFFFF',
        ink: {
          DEFAULT: '#111111',
          soft: '#333333',
          muted: '#6E6E73',
          faint: '#8E8E93',
        },
        hairline: '#E5E5EA',
        // 用户气泡专用紫色
        brand: {
          50: '#F5F3FF',
          100: '#EDE9FE',
          200: '#DDD6FE',
          400: '#A78BFA',
          500: '#8B5CF6',
          600: '#7C3AED',
          700: '#6D28D9',
        },
        // Apple 系统蓝 — 用于图标选中态、CTA、发送按钮等
        accent: {
          50: '#F0F7FF',
          100: '#E0EFFF',
          200: '#BFE0FF',
          400: '#3399FF',
          500: '#007AFF',
          600: '#0066D6',
          700: '#0055B8',
        },
      },
      borderRadius: {
        card: '24px',
        input: '20px',
      },
      boxShadow: {
        soft: '0 4px 20px rgba(0,0,0,0.04)',
        'soft-hover': '0 8px 30px rgba(0,0,0,0.07)',
      },
      fontFamily: {
        sans: ['-apple-system', 'SF Pro Display', 'Inter', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
