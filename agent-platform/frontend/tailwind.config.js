/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 设计令牌（ADR §6.1）：主色蓝紫、辅助紫/橙，语义失败红/成功绿
        primary: {
          DEFAULT: '#2F54EB',
          hover: '#2549d9',
          light: '#eef1fe',
        },
        accent: {
          purple: '#722ED1',
          orange: '#FAAD14',
        },
        danger: '#F5222D',
        success: '#52C41A',
      },
      borderRadius: {
        card: '6px',
        ctrl: '4px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,0.06)',
      },
      // 骨架屏抖动动画
      keyframes: {
        shimmer: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
