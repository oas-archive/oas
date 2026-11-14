import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 開發模式（npm run dev）時，將 /api 轉發至本機 API（啟動器或手動啟動的 8080 埠）。
// changeOrigin: false 保留原本的 Host（localhost:5173）：API 只接受 Origin 與 Host 相同的寫入，改成 8080 就會被當成跨站。
// cors: false：Vite 預設允許任何 localhost 埠跨來源讀取，同機另一個開發中專案的網頁就能經代理讀走典藏；網頁與 /api 同源，不需要 CORS。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, cors: false, proxy: { '/api': { target: process.env.OAS_API_URL || 'http://localhost:8080', changeOrigin: false } } },
  preview: { cors: false },
})
