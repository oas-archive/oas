// 字型打包進 App（開源、依 unicode-range 分檔，只下載畫面用到的字）：電腦和手機一致，離線也一樣
import '@fontsource-variable/inter/wght.css'
import '@fontsource-variable/noto-sans-tc/wght.css'
import '@fontsource-variable/noto-serif-tc/wght.css'
import { Component, StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import FirstRun from './FirstRun.jsx'
import ImmersiveApp from './ImmersiveApp.jsx'
import { loadPreferences, preferences } from './preferences.js'
import './immersive.css'

// 按鈕上的光：滑鼠跟著游標走，觸控在手指按下的位置亮起（樣式見 immersive.css 的 --lit-x、--lit-y）
const lightUp = event => {
  const button = event.target.closest?.('.save,.primary,.ghost')
  if (!button) return
  const rect = button.getBoundingClientRect()
  button.style.setProperty('--lit-x', `${event.clientX - rect.left}px`)
  button.style.setProperty('--lit-y', `${event.clientY - rect.top}px`)
}
document.addEventListener(window.matchMedia('(hover:hover) and (pointer:fine)').matches ? 'pointermove' : 'pointerdown', lightUp, { passive: true })

// 外觀版本：預設為 B2 視覺系統（2026-10-06 定案）；網址帶 ?look=classic 可暫時看舊版
document.documentElement.dataset.look = new URLSearchParams(window.location.search).get('look') === 'classic' ? '' : 'b2'

// 保護畫面：畫面程式出錯時顯示說明與重新載入，不會整頁變空白。資料存在後端，不受影響
class CrashGuard extends Component {
  state = { failed: null }
  static getDerivedStateFromError(failed) { return { failed } }
  render() {
    if (!this.state.failed) return this.props.children
    return <main className="crash-screen" role="alert"><h1>畫面出了問題</h1><p>你的典藏資料沒有受影響，重新載入就能回來。</p>
      <button type="button" className="save" onClick={() => window.location.reload()}>重新載入</button>
      <small>{String(this.state.failed?.message || this.state.failed)}</small></main>
  }
}

// 先問設定再開畫面：公開版第一次啟動要先做選擇，選之前主畫面不能載入封面或讀網頁
function Start() {
  const [prefs, setPrefs] = useState(null)
  useEffect(() => { loadPreferences().then(setPrefs, () => setPrefs(preferences())) }, [])
  if (!prefs) return null
  return prefs.setup_done ? <ImmersiveApp /> : <FirstRun onDone={setPrefs} />
}

createRoot(document.getElementById('root')).render(<StrictMode><CrashGuard><Start /></CrashGuard></StrictMode>)
