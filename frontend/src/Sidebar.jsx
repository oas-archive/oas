import { useEffect, useState } from 'react'
import { api, ask, colourFor, coverSrc, hideBroken, shortTitle, titleFor } from './shared.jsx'

export const PAGES = [['home', '⌂', '首頁'], ['gallery', '▦', '書庫'], ['taste', '✦', '口味'], ['tidy', '✓', '整理'], ['stats', '▥', '統計']]
const READING_SHORT = 4, READING_LONG = 12, RECENT_SHORT = 5, RECENT_LONG = 12, UNRATED_SHOWN = 3

const sinceText = iso => {
  if (!iso) return ''
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return '剛剛'
  if (minutes < 60) return `${minutes} 分鐘前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小時前`
  const days = Math.round(hours / 24)
  return days < 30 ? `${days} 天前` : new Date(iso).toLocaleDateString('zh-TW')
}

// 側欄（YouTube 式）：
// - full   完整側欄 232px：導覽、繼續閱讀、最近開啟、書單、系統狀態。
// - mini   迷你側欄 76px：圖示＋小字，點「繼續閱讀」「書單」會展開成完整側欄。
// 手機沒有側欄：導覽在底部分頁列，其餘內容在首頁書架、書庫的書單列、整理頁（v0.20.43）。
// 與主畫面同底色、只用細邊框區隔（interface-design：側欄不另外上色）。
// 整理頁三個分頁的待處理數字：側欄（電腦）、底部分頁列（手機）、整理頁分頁列共用
export function useTidyCounts(refreshKey) {
  const [counts, setCounts] = useState({})
  useEffect(() => {
    let live = true
    const set = (key, value) => { if (live) setCounts(current => ({ ...current, [key]: value })) }
    api('/duplicates').then(result => set('duplicates', result.groups.length)).catch(() => {})
    api('/attention/summary').then(result => set('attention', result.total)).catch(() => {})
    api('/tags/suggestions').then(result => set('rules', result.total)).catch(() => {})
    return () => { live = false }
  }, [refreshKey])
  return counts
}

export const backupText = status => !status ? '讀取中…' : status.backup ? `上次備份 ${sinceText(status.backup.at)}` : '尚未找到備份'
export const jobText = status => status?.intake?.running ? (status.intake.message || `補新收錄 ${status.intake.done}/${status.intake.total}`) : status?.refresh?.running ? `補資料 ${status.refresh.done}/${status.refresh.total}` : ''

export default function Sidebar({ variant, onToggle, page, goTo, openById, onShowList, onPalette, onBackup, onShortcuts, onDuplicates, onAttention, onTagSuggest, tidyCounts, refreshKey,
  collections, activeCollection, onCollection, onCreateCollection, onNewSmart, onRenameCollection, onDeleteCollection }) {
  const [reading, setReading] = useState([])
  const { duplicates: duplicateCount = 0, attention: attentionCount = 0, rules: suggestCount = 0 } = tidyCounts
  const [recent, setRecent] = useState([])
  const [unrated, setUnrated] = useState({ items: [], total: 0 })
  const [status, setStatus] = useState(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [editing, setEditing] = useState(null)
  const [moreReading, setMoreReading] = useState(false)
  const [moreRecent, setMoreRecent] = useState(false)
  const mini = variant === 'mini'

  useEffect(() => {
    let live = true
    Promise.all([
      api(`/resources?status=%E9%96%B1%E8%AE%80%E4%B8%AD&sort=opened&limit=${READING_LONG}`),
      api(`/resources?opened=true&sort=opened&limit=${READING_LONG + RECENT_LONG}`),
      api(`/resources?status=${encodeURIComponent('已完食')}&rated=false&sort=opened&limit=${UNRATED_SHOWN}`),
    ]).then(([inProgress, opened, notRated]) => {
      if (!live) return
      // 待評分：標成已完食、還沒評分（開過連結不代表看過）；件數與點進去的清單一致
      const pending = notRated.items
      setUnrated({ items: pending, total: notRated.total })
      setReading(inProgress.items)
      const shown = new Set([...inProgress.items, ...pending].map(item => item.id))
      setRecent(opened.items.filter(item => !shown.has(item.id)).slice(0, RECENT_LONG))
    }).catch(() => {})
    return () => { live = false }
  }, [refreshKey])

  useEffect(() => {
    const refresh = () => api('/system/status').then(setStatus).catch(() => {})
    refresh()
    const timer = setInterval(refresh, 30000)
    return () => clearInterval(timer)
  }, [refreshKey])

  const pick = action => (...args) => action(...args)
  const submitNew = async event => {
    event.preventDefault()
    if (!newName.trim()) { setCreating(false); return }
    if (await onCreateCollection(newName.trim())) { setNewName(''); setCreating(false) }
  }
  const submitRename = async event => {
    event.preventDefault()
    if (editing.name.trim() && await onRenameCollection(editing.id, editing.name.trim())) setEditing(null)
  }

  const job = jobText(status)  // 快取封面、巡檢來源在背景默默做，進度在「整理 → 維護」，不佔側欄
  const backupLine = backupText(status)
  const readingShown = reading.slice(0, moreReading ? READING_LONG : READING_SHORT)
  const recentShown = recent.slice(0, moreRecent ? RECENT_LONG : RECENT_SHORT)

  const masthead = <div className="nav-masthead">
    <button type="button" className="nav-toggle" onClick={onToggle} aria-label={mini ? '展開側欄' : '收合側欄'} aria-expanded={!mini}>
      <span aria-hidden="true" />
    </button>
    {!mini && <div className="oas-mark">O<span>A</span>S</div>}
  </div>

  if (mini) return <aside className="oas-nav is-mini" aria-label="主要導覽">
    {masthead}
    <nav className="nav-rail">
      {PAGES.map(([id, icon, label]) => <button key={id} type="button" className={page === id ? 'is-active' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => goTo(id)}><i aria-hidden="true">{icon}</i><span>{label}</span></button>)}
      <hr />
      <button type="button" onClick={onToggle} title="展開側欄查看繼續閱讀"><i aria-hidden="true">❯</i><span>繼續閱讀</span>{reading.length > 0 && <b className="nav-badge">{reading.length}</b>}</button>
      {collections.length > 0 && <button type="button" onClick={onToggle} className={activeCollection ? 'is-active' : ''} title="展開側欄查看書單"><i aria-hidden="true">▤</i><span>書單</span></button>}
    </nav>
    <div className="nav-rail nav-rail-bottom">
      <button type="button" className="for-keyboard" onClick={onPalette}><i aria-hidden="true">⌕</i><span>指令</span></button>
      <button type="button" onClick={onBackup} title={backupLine}><i aria-hidden="true">↧</i><span>備份</span><b className={`nav-dot ${status?.backup ? 'is-ok' : 'is-warn'}`} aria-label={backupLine} /></button>
    </div>
  </aside>

  return <>
    <aside className="oas-nav" aria-label="主要導覽">
      {masthead}
      <nav className="nav-pages">{PAGES.map(([id, icon, label]) => <button key={id} type="button" className={page === id ? 'is-active' : ''} aria-current={page === id ? 'page' : undefined} onClick={pick(goTo).bind(null, id)}><i aria-hidden="true">{icon}</i>{label}</button>)}</nav>

      <div className="nav-scroll">
        <section className="nav-section">
          <h2><button type="button" className="nav-heading" onClick={pick(onShowList).bind(null, 'reading')} title="在書庫列出所有閱讀中的作品">繼續閱讀<span aria-hidden="true">›</span></button></h2>
          {reading.length ? <ul className="nav-reading">{readingShown.map(item => <li key={item.id}>
            <button type="button" onClick={pick(openById).bind(null, item.id)} title={titleFor(item)} style={{ '--node': colourFor(item) }}>
              <i>{item.image_url && <img src={coverSrc(item, 240)} alt="" loading="lazy" onError={hideBroken} />}</i>
              {item.progress ? <b className="nav-reading-copy"><span>{shortTitle(item)}</span><small>看到 {item.progress}</small></b> : <span>{shortTitle(item)}</span>}
            </button>
          </li>)}</ul> : <p className="nav-empty">看完回來按「還在看」的作品會出現在這裡。</p>}
          {reading.length > READING_SHORT && <button type="button" className="nav-more-toggle" onClick={() => setMoreReading(!moreReading)}>{moreReading ? '顯示較少' : `顯示更多（${reading.length - READING_SHORT}）`}</button>}
        </section>

        {attentionCount > 0 && <section className="nav-section">
          <h2><button type="button" className="nav-heading" onClick={pick(onAttention)} title="缺標題、缺封面或來源失效的作品，到「整理 → 待整理」處理">待整理<b className="nav-count">{attentionCount} 件</b><span aria-hidden="true">›</span></button></h2>
        </section>}

        {duplicateCount > 0 && <section className="nav-section">
          <h2><button type="button" className="nav-heading" onClick={pick(onDuplicates)} title="到「整理 → 重複作品」檢查、合併">可能重複<b className="nav-count">{duplicateCount} 組</b><span aria-hidden="true">›</span></button></h2>
        </section>}

        {suggestCount > 0 && <section className="nav-section">
          <h2><button type="button" className="nav-heading" onClick={pick(onTagSuggest)} title="可能就是現有標籤的英文、簡體或不同寫法，到「整理 → 標籤規則」一鍵併入">標籤建議<b className="nav-count">{suggestCount} 個</b><span aria-hidden="true">›</span></button></h2>
        </section>}

        {unrated.total > 0 && <section className="nav-section">
          <h2><button type="button" className="nav-heading" onClick={pick(onShowList).bind(null, 'unrated')} title="在書庫列出已完食但還沒評分的作品">待評分<b className="nav-count">{unrated.total}</b><span aria-hidden="true">›</span></button></h2>
          <ul className="nav-recent">{unrated.items.map(item => <li key={item.id}>
            <button type="button" onClick={pick(openById).bind(null, item.id)} title={titleFor(item)}>
              <i style={{ background: colourFor(item) }} aria-hidden="true" /><span>{shortTitle(item)}</span><small>{sinceText(item.last_opened_at)}</small>
            </button>
          </li>)}</ul>
        </section>}

        {recent.length > 0 && <section className="nav-section">
          <h2><button type="button" className="nav-heading" onClick={pick(onShowList).bind(null, 'recent')} title="在書庫依最近開啟排序">最近開啟<span aria-hidden="true">›</span></button></h2>
          <ul className="nav-recent">{recentShown.map(item => <li key={item.id}>
            <button type="button" onClick={pick(openById).bind(null, item.id)} title={titleFor(item)}>
              <i style={{ background: colourFor(item) }} aria-hidden="true" /><span>{shortTitle(item)}</span><small>{sinceText(item.last_opened_at)}</small>
            </button>
          </li>)}</ul>
          {recent.length > RECENT_SHORT && <button type="button" className="nav-more-toggle" onClick={() => setMoreRecent(!moreRecent)}>{moreRecent ? '顯示較少' : `顯示更多（${recent.length - RECENT_SHORT}）`}</button>}
        </section>}

        <section className="nav-section">
          <h2><span className="nav-heading is-static">書單</span><button type="button" className="nav-add" aria-expanded={Boolean(creating)} onClick={() => setCreating(creating ? false : 'choose')} aria-label="新增書單" title="新增書單">＋</button></h2>
          <ul className="nav-collections">
            {collections.map(collection => <li key={collection.id} className={activeCollection?.id === collection.id ? 'is-active' : ''}>
              {editing?.id === collection.id ? <form className="nav-edit" onSubmit={submitRename}>
                <input autoFocus value={editing.name} onChange={event => setEditing({ ...editing, name: event.target.value })} onKeyDown={event => event.key === 'Escape' && (event.stopPropagation(), setEditing(null))} aria-label="書單名稱" />
                <button type="submit" aria-label="儲存名稱">✓</button>
                <button type="button" className="danger" onClick={async () => { if (await ask(`刪除書單「${collection.name}」？書單裡的作品不會被刪除。`, { confirm: '刪除書單', danger: true })) { onDeleteCollection(collection.id); setEditing(null) } }} aria-label="刪除書單">刪除</button>
              </form> : <>
                <button type="button" className="nav-collection" onClick={pick(onCollection).bind(null, activeCollection?.id === collection.id ? null : collection)}>
                  <i aria-hidden="true" title={collection.query ? '智慧書單：依條件自動更新' : undefined}>{collection.query ? '⌕' : '▤'}</i><span>{collection.name}</span><small>{collection.count}</small>
                </button>
                <button type="button" className="nav-more" onClick={() => setEditing({ id: collection.id, name: collection.name })} aria-label={`編輯書單 ${collection.name}`}>⋯</button>
              </>}
            </li>)}
          </ul>
          {creating === 'choose' && <div className="nav-new-list">
            <button type="button" onClick={() => setCreating('manual')}><b>手動書單</b><small>自己把作品一件件加進來</small></button>
            <button type="button" onClick={pick(() => { setCreating(false); onNewSmart() })}><b>智慧書單</b><small>設好條件，符合的作品自動出現</small></button>
          </div>}
          {creating === 'manual' ? <form className="nav-edit" onSubmit={submitNew}>
            <input autoFocus value={newName} onChange={event => setNewName(event.target.value)} onBlur={() => !newName.trim() && setCreating(false)} onKeyDown={event => event.key === 'Escape' && (event.stopPropagation(), setCreating(false))} placeholder="書單名稱，例如：週末要看" aria-label="新書單名稱" />
            <button type="submit" aria-label="建立書單">✓</button>
          </form> : null}
        </section>
      </div>

      <div className="nav-bottom">
        <button type="button" className="for-keyboard" onClick={pick(onPalette)}><i aria-hidden="true">⌕</i>指令面板<kbd>Ctrl K</kbd></button>
        <button type="button" onClick={pick(onBackup)}><i aria-hidden="true">↧</i>備份中心</button>
        <button type="button" className="for-keyboard" onClick={pick(onShortcuts)}><i aria-hidden="true">?</i>快捷鍵<kbd>?</kbd></button>
        <p className="nav-status" aria-live="polite">
          <span className={!status ? '' : status.backup ? 'is-ok' : 'is-warn'} title={status?.backup?.name}>{backupLine}</span>
          {job && <span>{job}</span>}
        </p>
      </div>
    </aside>
  </>
}

// 手機的底部分頁列：五個主要頁面；「整理」帶待處理的數字
export function BottomTabs({ page, goTo, tidyCounts = {} }) {
  const tidyTotal = (tidyCounts.attention || 0) + (tidyCounts.duplicates || 0) + (tidyCounts.rules || 0)
  return <nav className="bottom-tabs" aria-label="主要頁面">
    {PAGES.map(([id, icon, label]) => <button key={id} type="button" className={page === id ? 'is-active' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => goTo(id)}><i aria-hidden="true">{icon}</i>{label}{id === 'tidy' && tidyTotal > 0 && <b className="tab-count" aria-label={`${tidyTotal} 項待處理`}>{tidyTotal > 99 ? '99+' : tidyTotal}</b>}</button>)}
  </nav>
}
