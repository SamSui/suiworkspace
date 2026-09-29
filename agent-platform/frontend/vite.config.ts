import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发服务器：5173，代理 /v1、/healthz、/metrics 到后端网关（默认 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: process.env.VITE_API_PROXY || 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: process.env.VITE_API_PROXY || 'http://127.0.0.1:8000', changeOrigin: true },
      '/metrics': { target: process.env.VITE_API_PROXY || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
