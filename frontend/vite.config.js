import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 開發模式（npm run dev）時，將 /api 轉發至本機 API（啟動器或手動啟動的 8080 埠）。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': process.env.OAS_API_URL || 'http://localhost:8080' } },
})
