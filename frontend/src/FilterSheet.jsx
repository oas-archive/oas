import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, CATEGORY_NAMES, KINDS, useFocusTrap } from './shared.jsx'

// 分類分頁的順序：最常用的一般標籤在前，作者、社團數量多放後面
const TAB_ORDER = ['tag', 'parody', 'character', 'artist', 'group', 'category', 'language']
const PAGE = 120  // 一次畫出的列數：標籤上千個，全畫出來手機會卡

// 單選的條件（類型、來源）：一排膠囊，再按一次取消
function ChoiceGroup({ label, value, options, onChange }) {
  return <section className="fs-group"><h3>{label}</h3><div className="fs-choices" role="group" aria-label={label}>
    {options.map(option => <button key={option} type="button" aria-pressed={value === option} className={value === option ? 'is-active' : ''} onClick={() => onChange(value === option ? '' : option)}>{option}</button>)}
  </div></section>
}

// 篩選面板：類型、來源、標籤都在這裡。手機從底部滑出，電腦從右側滑出。
// 每按一下就套用，底部按鈕的件數是真實結果，按了只是關閉面板。
export default function FilterSheet({ included, excluded, mode, kind, source, sources, total, loading, filterParams, drafting, canSave, onIncluded, onExcluded, onMode, onKind, onSource, onClearAll, onSave, onClose }) {
  const [term, setTerm] = useState('')
  const [searched, setSearched] = useState('')
  const [tab, setTab] = useState('')
  const [rows, setRows] = useState(null)
  const [shown, setShown] = useState(PAGE)
  const body = useRef(null)

  // 關閉：先滑出（比進場快），動畫結束才真的卸載
  const card = useRef(null)
  useFocusTrap(true, card)
  const [leaving, setLeaving] = useState(false)
  const closing = useRef(false)
  const dismiss = () => {
    setLeaving(true)
    if (card.current) card.current.style.transform = ''
    setTimeout(onClose, 200)
  }
  // Android 返回鍵走瀏覽紀錄：開啟時多推一筆，返回就只關面板
  useEffect(() => {
    window.history.pushState({ ...window.history.state, oasFilter: true }, '')
    const onPop = event => { if (!event.state?.oasFilter) dismiss() }
    const onKey = event => { if (event.key === 'Escape') { event.stopPropagation(); close() } }
    window.addEventListener('popstate', onPop)
    window.addEventListener('keydown', onKey, true)
    return () => { window.removeEventListener('popstate', onPop); window.removeEventListener('keydown', onKey, true) }
  }, [])
  const close = () => {
    if (closing.current) return
    closing.current = true
    if (window.history.state?.oasFilter) window.history.back(); else dismiss()
  }
  // 手機抓上緣往下拉關閉：跟手、往上拉有阻力，放開時拉得夠遠或甩得夠快就關（和作品頁下拉同一組數值）
  const drag = useRef(null)
  const dragStart = event => {
    if (drag.current || event.target.closest('button') || !window.matchMedia('(max-width:780px)').matches) return
    drag.current = { id: event.pointerId, y: event.clientY, at: Date.now(), distance: 0 }
    event.currentTarget.setPointerCapture(event.pointerId)
    card.current.style.transition = 'none'
  }
  const dragMove = event => {
    if (drag.current?.id !== event.pointerId) return
    const distance = event.clientY - drag.current.y
    drag.current.distance = distance
    card.current.style.transform = `translate3d(0,${distance > 0 ? distance : distance / 6}px,0)`
  }
  const dragEnd = event => {
    if (drag.current?.id !== event.pointerId) return
    const { distance, at } = drag.current
    drag.current = null
    card.current.style.transition = ''
    if (distance > 96 || (distance > 24 && distance / (Date.now() - at) > 0.45)) close()
    else card.current.style.transform = ''
  }

  useEffect(() => { const timer = setTimeout(() => setSearched(term.trim()), 180); return () => clearTimeout(timer) }, [term])
  // 件數只算目前結果裡的作品，看得出再選下去還剩多少；搜尋交給後端，原文、簡體也找得到翻好的標籤
  useEffect(() => {
    let live = true
    api(`/tags/stats?limit=5000&${filterParams}${searched ? `&term=${encodeURIComponent(searched)}` : ''}`).then(result => live && setRows(result)).catch(() => live && setRows([]))
    return () => { live = false }
  }, [filterParams, searched])
  useEffect(() => { setShown(PAGE) }, [tab, searched])

  const tabs = useMemo(() => {
    const present = new Set((rows || []).map(row => row.category))
    return TAB_ORDER.filter(key => present.has(key))
  }, [rows])
  const picked = new Set([...included, ...excluded])
  const list = useMemo(() => {
    if (!rows) return []
    if (searched) return rows
    return rows.filter(row => !picked.has(row.name) && (!tab || row.category === tab))
  }, [rows, searched, tab, included, excluded])
  const counts = useMemo(() => Object.fromEntries((rows || []).map(row => [row.name, row.count])), [rows])

  const include = name => {
    if (excluded.includes(name)) onExcluded(excluded.filter(value => value !== name))
    onIncluded(included.includes(name) ? included.filter(value => value !== name) : [...included, name])
  }
  const exclude = name => {
    if (included.includes(name)) onIncluded(included.filter(value => value !== name))
    onExcluded(excluded.includes(name) ? excluded.filter(value => value !== name) : [...excluded, name])
  }
  const row = ({ name, count, category, via }) => {
    const on = included.includes(name)
    const off = excluded.includes(name)
    return <li key={name} className={on ? 'is-on' : off ? 'is-off' : undefined}>
      <button type="button" className="fs-row-main" aria-pressed={on} onClick={() => include(name)}>
        <i aria-hidden="true" /><span>{name}{via && <small>原文 {via}</small>}</span>
        {!tab && category && category !== 'tag' && <em>{CATEGORY_NAMES[category]}</em>}
        {off ? <small>已排除</small> : count != null && <small>{count}</small>}
      </button>
      <button type="button" className="fs-row-exclude" aria-pressed={off} onClick={() => exclude(name)}>{off ? '取消排除' : '排除'}</button>
    </li>
  }
  const hasAny = Boolean(kind || source || included.length || excluded.length)
  const pinned = [...included, ...excluded].map(name => ({ name, count: counts[name] ?? null }))

  return createPortal(<div className={`filter-sheet${leaving ? ' is-leaving' : ''}`} onClick={event => event.target === event.currentTarget && close()}>
    <section ref={card} className="fs-card" role="dialog" aria-modal="true" aria-label="篩選">
      <header className="fs-head" onPointerDown={dragStart} onPointerMove={dragMove} onPointerUp={dragEnd} onPointerCancel={dragEnd}><i className="fs-grab" aria-hidden="true" /><h2>{drafting ? '新增智慧書單' : '篩選'}</h2><button type="button" className="fs-close" onClick={close} aria-label="關閉">×</button></header>
      <div className="fs-body" ref={body}>
        {drafting && <p className="fs-note">選好條件後按下面的按鈕取名。符合條件的作品會自動出現在書單裡，之後收錄的也一樣。</p>}
        <ChoiceGroup label="類型" value={kind} options={KINDS} onChange={onKind} />
        <ChoiceGroup label="來源" value={source} options={sources} onChange={onSource} />
        <section className="fs-group">
          <h3>標籤</h3>
          {/* 清單很長，搜尋與分類固定在面板上方；點搜尋框時捲上來，鍵盤才不會蓋住結果 */}
          <div className="fs-tag-head">
            <label className="fs-search"><input value={term} onFocus={event => event.target.closest('.fs-tag-head').scrollIntoView({ block: 'start' })} onChange={event => setTerm(event.target.value)} placeholder="搜尋標籤，原文或簡體也可以" aria-label="搜尋標籤" /></label>
            {!searched && tabs.length > 1 && <div className="fs-tabs" role="tablist" aria-label="標籤分類">
              {['', ...tabs].map(key => <button key={key || 'all'} type="button" role="tab" aria-selected={tab === key} className={tab === key ? 'is-active' : ''} onClick={() => setTab(key)}>{key ? CATEGORY_NAMES[key] : '全部'}</button>)}
            </div>}
          </div>
          {included.length > 1 && <div className="fs-mode" role="radiogroup" aria-label="多個標籤的比對方式">
            {[['all', '同時有這些標籤'], ['any', '有其中一個就好']].map(([value, label]) => <button key={value} type="button" role="radio" aria-checked={mode === value} className={mode === value ? 'is-active' : ''} onClick={() => onMode(value)}>{label}</button>)}
          </div>}
          {!searched && pinned.length > 0 && <div className="fs-picked"><p>已選 {pinned.length} 個</p><ul className="fs-list" aria-label="已選的標籤">{pinned.map(row)}</ul></div>}
          {rows === null ? <p className="fs-empty">讀取中…</p> : list.length === 0 ? <p className="fs-empty">{searched ? '目前的結果裡沒有符合的標籤' : '沒有其他標籤可以選了'}</p> : <>
            <ul className="fs-list">{list.slice(0, shown).map(row)}</ul>
            {list.length > shown && <button type="button" className="fs-more" onClick={() => setShown(value => value + PAGE)}>再顯示 {Math.min(PAGE, list.length - shown)} 個（共 {list.length} 個）</button>}
          </>}
        </section>
      </div>
      <footer className="fs-foot">
        <button type="button" className="ghost" disabled={!hasAny} onClick={onClearAll}>清除</button>
        {drafting ? <button type="button" className="save" disabled={!canSave} onClick={() => { close(); onSave() }}>{canSave ? `下一步：取名（${total} 件）` : '先選一個條件'}</button>
          : <button type="button" className="save" onClick={close}>{loading ? '看結果' : total ? `看 ${total} 件` : '沒有符合的作品'}</button>}
      </footer>
    </section>
  </div>, document.body)
}

// 智慧書單的取名與改名：列出條件和目前件數，讓人知道存了什麼
// 新增書單：先選要哪一種（手機沒有側欄，從書庫的書單列按「＋」進來）
export function ListTypeDialog({ onManual, onSmart, onClose }) {
  const dialog = useFocusTrap()
  return createPortal(<div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label="新增書單" onClick={event => event.target === event.currentTarget && onClose()}>
    <section className="oas-modal-card smart-dialog"><h2>新增書單</h2>
      <div className="list-type">
        <button type="button" onClick={onManual}><b>手動書單</b><small>自己把作品一件件加進來</small></button>
        <button type="button" onClick={onSmart}><b>智慧書單</b><small>設好條件，符合的作品自動出現</small></button>
      </div>
      <div className="smart-dialog-actions"><button type="button" className="ghost" onClick={onClose}>取消</button></div>
    </section>
  </div>, document.body)
}

export function SmartDialog({ title, initial, applied, total, submit, onSubmit, onClose }) {
  const [name, setName] = useState(initial)
  const dialog = useFocusTrap()
  const send = event => { event.preventDefault(); if (name.trim()) onSubmit(name.trim()) }
  return createPortal(<div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label={title} onClick={event => event.target === event.currentTarget && onClose()} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose() } }}>
    <section className="oas-modal-card smart-dialog"><h2>{title}</h2>
      <form onSubmit={send}>
        <label className="field"><span>名稱</span><input autoFocus value={name} maxLength={60} onFocus={event => event.target.select()} onChange={event => setName(event.target.value)} placeholder="例如：週末要看" /></label>
        {applied && <div className="smart-dialog-rules"><span>條件</span><p>{applied.map(item => <b key={item.key}>{item.label}</b>)}</p></div>}
        {applied && <p className="smart-dialog-note">目前 {total} 件。之後收錄的作品符合條件會自動出現，不用手動加入；排序方式也會一起記住。</p>}
        <div className="smart-dialog-actions"><button type="button" className="ghost" onClick={onClose}>取消</button><button type="submit" className="save" disabled={!name.trim()}>{submit}</button></div>
      </form>
    </section>
  </div>, document.body)
}
