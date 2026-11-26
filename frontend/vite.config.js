import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 開發模式（npm run dev）時，將 /api 轉發至本機 API（啟動器或手動啟動的 8080 埠）。
// changeOrigin: false 保留原本的 Host（localhost:5173）：API 只接受 Origin 與 Host 相同的寫入，改成 8080 就會被當成跨站。
// cors: false：Vite 預設允許任何 localhost 埠跨來源讀取，同機另一個開發中專案的網頁就能經代理讀走典藏；網頁與 /api 同源，不需要 CORS。
//
// 兩個入口：index.html（主畫面）與 pair.html（區網模式下其他裝置的配對頁）。
// 配對頁的程式放在 dist/pair/：還沒配對的裝置只拿得到 pair.html 與 pair/ 底下的檔案（後端 frontend.py），主程式的 /assets 要配對後才給。
// 所以兩個入口不能共用任何程式塊：配對頁只 import pairLogic.js，主程式不 import 它；modulePreload 的 polyfill 也關掉（兩邊都會注入，會被拆成共用程式塊放進 assets/）。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, cors: false, proxy: { '/api': { target: process.env.OAS_API_URL || 'http://localhost:8080', changeOrigin: false } } },
  preview: { cors: false },
  build: {
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: { index: 'index.html', pair: 'pair.html' },
      output: { entryFileNames: chunk => chunk.name === 'pair' ? 'pair/[name]-[hash].js' : 'assets/[name]-[hash].js' },
    },
  },
})
