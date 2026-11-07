// 第三方授權聲明：清單列名稱、版本與授權，可搜尋，點一下才看全文（資料由 tools/dev/gen_third_party.py 產生，手機 App 提供）
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useFocusTrap } from './shared.jsx'

const GROUPS = [['App 本體', '手機 App 與執行環境'], ['Python', '後端'], ['網頁', '畫面']]
// 授權原文的換行是照 80 欄排的，手機上會斷得很難讀：同一段接回一行（只動空白，不動文字）；條列項目與分隔線各自一行
const LIST_ITEM = /^\s*(?:[*•-]\s|\(?[a-z0-9]{1,3}[.)]\s|[=*_-]{3,}\s*$)/i
export const paragraphs = text => text.split(/\n\s*\n/).map(block => {
  const lines = []
  for (const line of block.split('\n')) {
    if (lines.length && !LIST_ITEM.test(line) && !LIST_ITEM.test(lines[lines.length - 1])) lines[lines.length - 1] += ` ${line.trim()}`
    else if (lines.length && !LIST_ITEM.test(line) && /^\s*(?:[*•-]\s|\(?[a-z0-9]{1,3}[.)]\s)/i.test(lines[lines.length - 1])) lines[lines.length - 1] += ` ${line.trim()}`
    else lines.push(line.trim())
  }
  return lines.join('\n')
}).filter(Boolean)

export default function ThirdPartyNotices({ close }) {
  const dialog = useFocusTrap()
  const [packages, setPackages] = useState(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [detail, setDetail] = useState(null)
  const card = useRef(null)
  useEffect(() => {
    let live = true
    fetch('/third-party-notices.json').then(response => {
      if (!response.ok) throw new Error('not found')
      return response.json()
    }).then(data => { if (live) setPackages(data.packages) }).catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [])
  // Android 返回鍵走瀏覽紀錄：全文 → 清單 → 關閉
  useEffect(() => {
    window.history.pushState({ ...window.history.state, oasNotices: 'list' }, '')
    const onPop = event => {
      if (event.state?.oasNotices === 'list') setDetail(null)
      else if (!event.state?.oasNotices) close()
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  useEffect(() => { if (card.current) card.current.scrollTop = 0 }, [detail])
  const leave = () => window.history.go(window.history.state?.oasNotices === 'detail' ? -2 : -1)
  useEffect(() => {
    const onKey = event => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      if (detail) window.history.back(); else leave()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [detail])
  const open = item => { window.history.pushState({ ...window.history.state, oasNotices: 'detail' }, ''); setDetail(item) }
  const shown = useMemo(() => {
    const text = query.trim().toLowerCase()
    return (packages || []).filter(item => !text || `${item.name} ${item.license}`.toLowerCase().includes(text))
  }, [packages, query])
  // 從維護頁打開，但要蓋過底部分頁列：掛到頁面最外層
  return createPortal(<div ref={dialog} className="oas-modal notices-modal" role="dialog" aria-modal="true" aria-label="第三方授權聲明" onClick={event => event.target === event.currentTarget && leave()}>
    <section ref={card} className="oas-modal-card notices-card">
      <button className="drawer-close" onClick={leave} aria-label="關閉第三方授權聲明">×</button>
      {detail ? <>
        <button type="button" className="notices-back" onClick={() => window.history.back()}>‹ 全部套件</button>
        <h2>{detail.name}</h2>
        <p className="notices-meta">{detail.version && <span>{detail.version}</span>}<em className="notices-chip">{detail.license}</em></p>
        {detail.texts.length
          ? detail.texts.map((text, index) => <div key={index} className="notices-text">{paragraphs(text).map((block, part) => <p key={part}>{block}</p>)}</div>)
          : <p className="drawer-subtitle">這個套件沒有附授權全文，授權條款見上方的名稱。</p>}
      </> : <>
        <h2>第三方授權聲明</h2>
        <p className="drawer-subtitle">OAS 本身採 MIT 授權。這個 App 另外包含下列開源軟體，各自依其授權條款提供。</p>
        {failed ? <p className="notices-empty" role="alert">讀不到授權資料。</p> : !packages ? <p className="notices-empty">讀取中…</p> : <>
          <label className="notices-search"><span>搜尋 {packages.length} 個套件</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="套件或授權名稱" autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>
          {GROUPS.map(([kind, title]) => {
            const rows = shown.filter(item => item.kind === kind)
            return rows.length > 0 && <div key={kind} className="notices-group"><h3>{title}</h3><ul>{rows.map(item => <li key={`${item.name} ${item.version}`}>
              <button type="button" onClick={() => open(item)}><span><b>{item.name}</b>{item.version && <small>{item.version}</small>}</span><em className="notices-chip">{item.license}</em><i aria-hidden="true">›</i></button>
            </li>)}</ul></div>
          })}
          {shown.length === 0 && <p className="notices-empty">沒有符合的套件。</p>}
        </>}
      </>}
    </section>
  </div>, document.body)
}
