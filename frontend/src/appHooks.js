import { useEffect, useState } from 'react'
import { api, TOUCH, whenIdle } from './shared.jsx'

// 封面放大／縮回的動畫在 Showcase.jsx（FLIP，只動封面本身）；這裡負責找出卡片封面的位置。
// 關閉動畫的安全上限：背景分頁動畫可能不走，時間到一定卸載，不會卡住
export const CLOSE_LIMIT = 800
// 展示頁開啟後多久才把底下的頁面收起來（content-visibility）：要等封面放大結束，動畫中不動底下的版面
export const COVER_MAIN_AFTER = 640
const WARM_AFTER = 1500  // 開 App 多久後預先排一次展示頁
const WARM_LIMIT = 1500  // 最多等字型這麼久
const inViewport = element => {
  if (!element) return null
  const rect = element.getBoundingClientRect()
  return rect.bottom > 0 && rect.top < window.innerHeight && rect.width > 0 ? element : null
}
// preferHover：開啟時優先取游標下的卡片（同一作品可能同時出現在多個書架）；關閉時游標下可能已是別張卡片，所以不採用。
export const cardCover = (id, preferHover = true) => (preferHover && inViewport(document.querySelector(`article:hover [data-cover-id="${id}"]`))) || inViewport(document.querySelector(`.oas-main [data-cover-id="${id}"]`))

export function useNavVariant() {
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [shortSide, setShortSide] = useState(() => Math.min(window.innerWidth, window.innerHeight))
  const [navPreference, setNavPreference] = useState(() => { try { return localStorage.getItem('oas-nav') } catch { return null } })
  useEffect(() => {
    const onResize = () => { setViewport(window.innerWidth); setShortSide(Math.min(window.innerWidth, window.innerHeight)) }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  // 手機（含橫放：看短邊）用底部分頁列；其他寬度依使用者選擇，沒選過時窄視窗（< 1100px）預設迷你
  const navVariant = viewport < 780 || (TOUCH && shortSide < 600) ? 'phone' : (navPreference || (viewport < 1100 ? 'mini' : 'full'))
  const toggleNav = () => {
    const next = navVariant === 'mini' ? 'full' : 'mini'
    setNavPreference(next)
    try { localStorage.setItem('oas-nav', next) } catch { /* 無法保存時只影響這次 */ }
  }
  return { viewport, navVariant, toggleNav }
}

export function useWarmShowcase(selectedRef) {
  // 開 App 後趁空檔在看不見的地方排一次展示頁：載入字型、跑過程式，第一次點開作品才不會卡。使用者已經打開作品就不做
  const [warmItem, setWarmItem] = useState(null)
  useEffect(() => {
    let live = true
    let cancelIdle = () => {}
    const timer = setTimeout(() => {
      cancelIdle = whenIdle(() => {
        if (selectedRef.current) return
        api('/resources?limit=1&works=true').then(result => { if (live && !selectedRef.current && result.items[0]) setWarmItem(result.items[0]) }).catch(() => {})
      }, 4000)
    }, WARM_AFTER)
    return () => { live = false; clearTimeout(timer); cancelIdle() }
  }, [])
  useEffect(() => {
    if (!warmItem) return
    let live = true
    const done = () => { if (live) setWarmItem(null) }
    const frame = requestAnimationFrame(() => {
      Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, WARM_LIMIT))]).then(() => requestAnimationFrame(done))
    })
    return () => { live = false; cancelAnimationFrame(frame) }
  }, [warmItem])
  return warmItem
}
