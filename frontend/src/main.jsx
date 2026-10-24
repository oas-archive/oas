import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import ImmersiveApp from './ImmersiveApp.jsx'
import './immersive.css'

// 外觀版本：預設為 B2 視覺系統（2026-10-06 定案）；網址帶 ?look=classic 可暫時看舊版
document.documentElement.dataset.look = new URLSearchParams(window.location.search).get('look') === 'classic' ? '' : 'b2'

createRoot(document.getElementById('root')).render(<StrictMode><ImmersiveApp /></StrictMode>)