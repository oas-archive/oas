import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, CATEGORY_NAMES, colourFor, CoverFace, coverRatio, coverSrc, groupTags, hideBroken, isFaceCover, loadTagCategories, noteText, STATUS_COLOURS, titleFor } from './shared.jsx'

// ---------- 搜尋：即時建議與最近搜尋 ----------

const RECENT_KEY = 'oas-recent-search'
const readRecent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') } catch { return [] } }
const writeRecent = list => { try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8))) } catch { /* 只影響這次 */ } }

// 打字時列出符合的作品與標籤；輸入框空白時列出最近搜過的字。方向鍵選擇、Enter 開啟、Esc 收起。
export function SearchBox({ value, onChange, onOpenItem, onTag }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [recent, setRecent] = useState(readRecent)
  const [active, setActive] = useState(-1)
  const root = useRef(null)
  const term = value.trim()

  useEffect(() => {
    if (!term) { setData(null); return }
    let live = true
    const timer = setTimeout(() => api(`/search/suggest?q=${encodeURIComponent(term)}`).then(result => live && setData(result)).catch(() => {}), 150)
    return () => { live = false; clearTimeout(timer) }
  }, [term])
  useEffect(() => { setActive(-1) }, [term, open])
  useEffect(() => {
    if (!open) return
    const close = event => { if (!root.current?.contains(event.target)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])

  const remember = text => {
    const word = text.trim()
    if (word.length < 2) return
    const next = [word, ...recent.filter(entry => entry !== word)]
    setRecent(next); writeRecent(next)
  }
  const forget = word => { const next = recent.filter(entry => entry !== word); setRecent(next); writeRecent(next) }
  const options = term
    ? [...(data?.items || []).map(item => ({ kind: 'item', key: `i${item.id}`, item })), ...(data?.tags || []).map(tag => ({ kind: 'tag', key: `t${tag.name}`, tag }))]
    : recent.map(word => ({ kind: 'recent', key: `r${word}`, word }))
  const choose = option => {
    setOpen(false)
    if (option.kind === 'item') { remember(term); onOpenItem(option.item) }
    else if (option.kind === 'tag') { remember(term); onChange(''); onTag(option.tag.name) }
    else onChange(option.word)
  }
  const onKeyDown = event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!options.length) return
      event.preventDefault(); setOpen(true)
      // -1 代表回到輸入框本身；上下循環
      setActive(index => event.key === 'ArrowDown' ? (index + 1 >= options.length ? -1 : index + 1) : (index - 1 < -1 ? options.length - 1 : index - 1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (open && options[active]) choose(options[active])
      else { remember(term); setOpen(false); event.currentTarget.blur() }
    } else if (event.key === 'Escape') {
      if (open) { event.preventDefault(); event.stopPropagation(); setOpen(false) } else event.currentTarget.blur()
    }
  }
  const items = options.filter(option => option.kind === 'item'), tags = options.filter(option => option.kind === 'tag')
  const row = (option, content) => {
    const index = options.indexOf(option)
    return <li key={option.key} role="option" aria-selected={index === active} className={index === active ? 'is-active' : ''} onMouseEnter={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(option)}>{content}</li>
  }
  const showPanel = open && (term ? data && options.length > 0 : recent.length > 0)
  return <div className="search-box" ref={root}>
    <label className="search"><span>⌕</span>
      <input id="oas-search" type="search" role="combobox" aria-expanded={showPanel} aria-controls="oas-search-panel" aria-autocomplete="list" aria-label="搜尋典藏" autoComplete="off" spellCheck={false}
        value={value} onChange={event => { onChange(event.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onBlur={() => remember(term)} onKeyDown={onKeyDown} placeholder="搜尋標題、標籤或代碼" />
      <kbd>/</kbd></label>
    {showPanel && <div className="search-panel" id="oas-search-panel">
      {term ? <>
        {items.length > 0 && <><p className="search-group">作品</p><ul role="listbox">{items.map(option => row(option, <>
          <i className="search-thumb" style={{ '--cover-colour': colourFor(option.item) }}>{!isFaceCover(option.item) && <img src={coverSrc(option.item, 240)} alt="" onError={hideBroken} />}</i>
          <span className="search-title">{titleFor(option.item)}</span><small>{option.item.source_name}</small>
        </>))}</ul></>}
        {tags.length > 0 && <><p className="search-group">標籤</p><ul role="listbox">{tags.map(option => row(option, <>
          <span className="search-title">{option.tag.category === 'tag' ? '#' : ''}{option.tag.name}</span>
          {option.tag.category !== 'tag' && <small className="search-category">{CATEGORY_NAMES[option.tag.category]}</small>}<small>{option.tag.count} 件</small>
        </>))}</ul></>}
        <p className="search-foot">Enter 篩選典藏館；選作品直接開啟，選作者等標籤開資料頁</p>
      </> : <><p className="search-group">最近搜尋</p><ul role="listbox">{options.map(option => row(option, <>
        <span className="search-title">{option.word}</span>
        <button type="button" className="search-forget" onMouseDown={event => event.preventDefault()} onClick={event => { event.stopPropagation(); forget(option.word) }} aria-label={`從最近搜尋移除 ${option.word}`}>×</button>
      </>))}</ul></>}
    </div>}
  </div>
}

// ---------- 懸停預覽 ----------

// 卡片透過 previewBus 通知；預覽元件只有一個，由 App 放在畫面上。
export const previewBus = { show: () => {}, hide: () => {} }
const PREVIEW_DELAY = 650
let previewTimer = 0
export const schedulePreview = (item, element) => {
  clearTimeout(previewTimer)
  if (!item?.id || window.matchMedia?.('(hover: none)').matches) return
  previewTimer = setTimeout(() => previewBus.show(item, element), PREVIEW_DELAY)
}
export const cancelPreview = () => { clearTimeout(previewTimer); previewBus.hide() }

export function HoverPreview({ disabled }) {
  const [state, setState] = useState(null)
  const [categories, setCategories] = useState({})
  const [position, setPosition] = useState(null)
  const panel = useRef(null)
  useEffect(() => {
    previewBus.show = (item, element) => { if (!disabled && element?.isConnected) setState({ item, rect: element.getBoundingClientRect() }) }
    previewBus.hide = () => setState(null)
    const hide = () => { clearTimeout(previewTimer); setState(null) }
    // capture：書架等區塊自己的左右捲動也要收起預覽
    window.addEventListener('scroll', hide, { passive: true, capture: true })
    window.addEventListener('pointerdown', hide, true)
    return () => { previewBus.show = () => {}; previewBus.hide = () => {}; window.removeEventListener('scroll', hide, { capture: true }); window.removeEventListener('pointerdown', hide, true) }
  }, [disabled])
  useEffect(() => { if (disabled) setState(null) }, [disabled])  // 開啟其他視窗時收掉，關閉後不會在舊位置冒出來
  useEffect(() => { if (state) loadTagCategories().then(setCategories) }, [state])
  useLayoutEffect(() => {
    if (!state || !panel.current) { setPosition(null); return }
    const { rect } = state
    const width = panel.current.offsetWidth, height = panel.current.offsetHeight
    const right = rect.right + 14 + width <= window.innerWidth - 12
    const left = right ? rect.right + 14 : Math.max(12, rect.left - 14 - width)
    const top = Math.min(Math.max(12, rect.top), window.innerHeight - height - 12)
    setPosition({ left, top })
  }, [state])
  if (!state || disabled) return null
  const { item } = state
  const groups = groupTags(item.tags, categories)
  const note = noteText(item)
  return createPortal(<aside ref={panel} className={`hover-preview${position ? ' is-placed' : ''}`} style={{ ...(position || { left: -9999, top: 0 }), '--cover-colour': colourFor(item) }} aria-hidden="true">
    <div className="hover-cover" style={{ height: Math.min(300, Math.round(300 * (isFaceCover(item) ? 1.15 : coverRatio(item)))) }}>{isFaceCover(item) ? <CoverFace item={item} width={360} /> : <img src={coverSrc(item, 360)} alt="" onError={hideBroken} />}</div>
    <div className="hover-body">
      <h4>{titleFor(item)}</h4>
      <p className="hover-meta"><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}{item.progress ? ` ${item.progress}` : ''}</span>{item.rating > 0 && <span className="is-rated">★ {Number(item.rating).toFixed(1)}</span>}{item.kind && <span>{item.kind}</span>}<span>{item.source_name}</span></p>
      {groups.map(group => <p key={group.key} className="hover-tags"><b>{group.label}</b>{group.names.slice(0, 14).map(name => <span key={name}>{name}</span>)}{group.names.length > 14 && <span>…</span>}</p>)}
      {note && <p className="hover-note">{note.slice(0, 120)}{note.length > 120 ? '…' : ''}</p>}
    </div>
  </aside>, document.body)
}

// ---------- 斷線橫幅 ----------

// 資料服務連不上時出現，每 4 秒試一次；恢復後顯示「已重新連上」並重新載入資料。
export function OfflineBanner({ onBack }) {
  const [state, setState] = useState('ok')  // ok／offline／back
  useEffect(() => {
    const onOffline = () => setState(current => current === 'offline' ? current : 'offline')
    window.addEventListener('oas-offline', onOffline)
    return () => window.removeEventListener('oas-offline', onOffline)
  }, [])
  useEffect(() => {
    if (state === 'back') { const timer = setTimeout(() => setState('ok'), 3000); return () => clearTimeout(timer) }
    if (state !== 'offline') return
    const timer = setInterval(() => fetch('/api/health').then(response => { if (response.ok) { setState('back'); onBack() } }).catch(() => {}), 4000)
    return () => clearInterval(timer)
  }, [state])
  if (state === 'ok') return null
  return <div className={`offline-banner${state === 'back' ? ' is-back' : ''}`} role="status">
    {state === 'back' ? <b>已重新連上典藏館，資料已重新載入。</b> : <>
      <b>典藏館的資料服務沒有回應，每 4 秒自動重試。</b>
      <span>通常是 Docker 還在啟動或剛被關閉，等一下就好。超過一分鐘還沒恢復，請關掉典藏館視窗，再雙擊「OAS 典藏館」捷徑重新開啟。</span>
    </>}
  </div>
}

// ---------- 一次收錄多個網址 ----------

// 網址：遇到空白、引號、中文標點就結束；尾端的英文標點與沒有成對的右括號也去掉（「看這本：https://…/123/。」）
const URL_PATTERN = /https?:\/\/[^\s"'<>，、。；：！？「」『』【】（）《》]+/g
// 整行只有一串數字（可帶 #）：當成 N 網的作品編號（俗稱「神的語言」）。只認整行，避免把文字裡的日期等數字誤當成作品。
const CODE_LINE = /^\s*#?\s*(\d{1,7})\s*$/
const cleanUrl = url => {
  let clean = url.replace(/[.,;:!?'"]+$/, '')
  while (clean.endsWith(')') && (clean.match(/\(/g) || []).length < (clean.match(/\)/g) || []).length) clean = clean.slice(0, -1).replace(/[.,;:!?]+$/, '')
  return clean
}
// 解析貼上的文字：每一項是 { url, code }，code 有值代表由整行數字（N 網作品編號）轉換而來；同一網址只算一次
export const extractItems = text => {
  const items = new Map()
  for (const line of text.split(/\r?\n/)) {
    const code = line.match(CODE_LINE)?.[1]
    const found = code ? [{ url: `https://nhentai.net/g/${code}/`, code }] : (line.match(URL_PATTERN) || []).map(url => ({ url: cleanUrl(url), code: null }))
    for (const item of found) if (!items.has(item.url)) items.set(item.url, item)
  }
  return [...items.values()]
}
export const extractUrls = text => extractItems(text).map(item => item.url)

// 與後端 ImportInput 的上限一致
const IMPORT_LIMIT = 300

export function ImportDialog({ close, notify, onDone, onOpenId }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState(null)
  const [choice, setChoice] = useState({})  // 使用者手動勾選或取消的項目：網址 → true／false
  const items = extractItems(text)
  // 3 位數以下的數字常是頁數、序號，預設不收錄，確認是作品編號再勾選
  const isShort = item => item.code !== null && item.code.length <= 3
  const picked = item => choice[item.url] ?? !isShort(item)
  const urls = items.filter(picked).map(item => item.url)
  const tooMany = urls.length > IMPORT_LIMIT
  useEffect(() => {
    const listener = event => { if (event.key === 'Escape') { event.stopPropagation(); close() } }
    window.addEventListener('keydown', listener, true)
    return () => window.removeEventListener('keydown', listener, true)
  }, [close])
  const submit = async () => {
    setBusy(true)
    try {
      const result = await api('/import/urls', { method: 'POST', body: JSON.stringify({ urls }) })
      setReport(result); onDone()
      if (result.created.length) notify(`已收錄 ${result.created.length} 件，正在背景補上封面與標籤`, result.undo)
    } catch (error) { notify(error.message) } finally { setBusy(false) }
  }
  return <div className="oas-modal" role="dialog" aria-modal="true" aria-label="一次收錄多個網址" onClick={event => event.target === event.currentTarget && close()}>
    <section className="oas-modal-card import-card">
      <button className="drawer-close" onClick={close} aria-label="關閉">×</button>
      <h2>一次收錄多個網址</h2>
      {!report ? <>
        <p className="drawer-subtitle">貼上作品頁網址，一行一個，或直接貼一段含網址的文字；整行只有一串數字的，會當成 N 網的作品編號。收錄後會在背景慢慢補上標題、封面與標籤（nhentai 等網站每 6 秒一件），進度在側欄底部。已在典藏中的不會重複收錄。</p>
        <textarea className="import-text" autoFocus value={text} onChange={event => setText(event.target.value)} placeholder={'https://nhentai.net/g/123456/\nhttps://www.wnacg.com/photos-index-aid-12345.html\n654321'} />
        {items.length > 0 && <ul className="import-preview" aria-label="將收錄的項目">{items.map(item => <li key={item.url} className={picked(item) ? undefined : 'is-off'}>
          <label><input type="checkbox" checked={picked(item)} onChange={event => setChoice(current => ({ ...current, [item.url]: event.target.checked }))} />
            {item.code ? <><b>N 網 #{item.code}</b><small className={isShort(item) ? 'is-warn' : undefined}>{isShort(item) ? '很短的數字：確認不是頁數或序號再勾選' : '由數字轉換'}</small></> : <span title={item.url}>{item.url}</span>}</label>
        </li>)}</ul>}
        <div className="import-actions"><span className={tooMany ? 'is-warn' : undefined}>找到 {items.length} 項{items.length !== urls.length ? `，將收錄 ${urls.length} 件` : ''}{tooMany ? `，一次最多 ${IMPORT_LIMIT} 件，請分批貼上` : ''}</span><button type="button" className="save" disabled={busy || !urls.length || tooMany} onClick={submit}>{busy ? '收錄中…' : `收錄 ${urls.length} 件`}</button></div>
        <p className="import-hint">禁漫、MissAV 有防護，伺服器讀不到資料；這些網站請在作品頁用「收錄到 OAS」書籤。</p>
      </> : <>
        <p className="drawer-subtitle">新增 {report.created.length} 件{report.duplicates.length ? `，${report.duplicates.length} 件已在典藏中` : ''}{report.invalid.length ? `，${report.invalid.length} 個無法辨識` : ''}。{report.created.length ? '封面與標籤會在背景陸續補上；不想要這批可以按提示訊息的「復原」。' : ''}</p>
        {report.duplicates.length > 0 && <div className="backup-block"><h3>已在典藏中</h3><ul className="import-list">{report.duplicates.map(row => <li key={row.url}><span title={row.url}>{row.title}</span><button type="button" className="ghost" onClick={() => { close(); onOpenId(row.id) }}>開啟</button></li>)}</ul></div>}
        {report.invalid.length > 0 && <div className="backup-block"><h3>無法辨識</h3><ul className="import-list">{report.invalid.map(url => <li key={url}><span>{url}</span></li>)}</ul></div>}
        <div className="import-actions"><button type="button" className="ghost" onClick={() => { setReport(null); setText(''); setChoice({}) }}>再貼一批</button><button type="button" className="save" onClick={close}>完成</button></div>
      </>}
    </section>
  </div>
}

// ---------- 載入中的卡片輪廓 ----------

const SKELETON_RATIOS = [1.42, 1.3, 1.5, 1.38, 1.45, 1.33, 1.4, 1.48]
export function SkeletonGrid({ columns = 4, count = 12 }) {
  const lists = Array.from({ length: columns }, () => [])
  for (let index = 0; index < count; index++) lists[index % columns].push(index)
  return <section className="oas-gallery skeleton-grid" aria-label="載入中" aria-busy="true" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: 14 }}>
    {lists.map((list, column) => <div key={column} className="masonry-column" style={{ gap: 14 }}>{list.map(index => <div key={index} className="skeleton-card">
      <i style={{ aspectRatio: `1 / ${SKELETON_RATIOS[index % SKELETON_RATIOS.length]}` }} /><b /><b className="is-short" />
    </div>)}</div>)}
  </section>
}
