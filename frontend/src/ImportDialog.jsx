// 一次收錄：貼上就解析（規則只有後端一份，見 batch_plan.py），這裡只顯示一行狀態與看不懂的提示，不逐筆勾選
import { useEffect, useState } from 'react'
import { canReadPages, fillFromPages } from './pageReader.js'
import { api, confirmLeave, errorNote, hideBroken, TOUCH, useFocusTrap } from './shared.jsx'
import { PROTECTED } from './sites.js'

const SHOWN_HINTS = 4
const POLL_MS = 2000
// 「複製格式」：每段底下留空讓人填，不放範例數字，免得忘記改就收到別的作品
const FORMAT = 'N：\n\nW：\n\nJM：\n'
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) })
const willImport = row => row.kind === 'url' && !row.duplicate && !row.short

function Rules() {
  return <details className="import-rules"><summary>怎麼寫</summary><dl>
    <div><dt>https://…</dt><dd>完整網址，任何網站都收，混在句子裡或一行好幾個都可以</dd></div>
    <div><dt>N123456　W123456　JM123456</dt><dd>漫畫編號，可以用逗號或空白分開；純數字算 N</dd></div>
    <div><dt>N：（換行）123456</dt><dd>底下的數字都算這個網站；W：、JM： 也一樣</dd></div>
    <div><dt>作品標題</dt><dd>可以到漫畫網站搜尋，按了才連線</dd></div>
    <div><dd>影片和其他網站沒有簡寫，請貼完整網址。一次最多 300 件。</dd></div>
  </dl></details>
}

// 標題文字：按了才搜尋，找到的按「加入」；讀不到的網站給搜尋連結
function SearchHint({ row, added, onAdd, notify }) {
  const [plans, setPlans] = useState(null)
  const [busy, setBusy] = useState(false)
  const search = async () => {
    setBusy(true)
    try { setPlans((await post('/import/search', { text: row.text, sites: row.sites })).plans) } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const results = plans?.flatMap(plan => plan.results.map(item => ({ ...item, site: plan.site }))) || []
  const links = plans?.filter(plan => plan.mode === 'link') || []
  return <li>
    <div className="import-hint-row"><span><code>{row.text}</code><small>{!plans ? '不是網址或編號' : results.length ? `找到 ${results.length} 件` : '沒有搜到'}</small></span>
      {!plans && <button type="button" className="import-link" disabled={busy} onClick={search}>{busy ? '搜尋中…' : '搜尋'}</button>}</div>
    {results.length > 0 && <ul className="import-results">{results.map(item => <li key={item.url}>
      <i>{item.thumb && <img src={item.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" onError={hideBroken} />}</i>
      <span title={item.title}>{item.title}<small>{item.site}</small></span>
      <button type="button" className="import-link" disabled={added.has(item.url)} onClick={() => onAdd(item.url)}>{added.has(item.url) ? '已加入' : '加入'}</button>
    </li>)}</ul>}
    {links.map(plan => <a key={plan.site} className="import-link" href={plan.link} target="_blank" rel="noreferrer noopener">到 {plan.site} 搜尋 ↗</a>)}
  </li>
}

// 收錄之後：一條總進度，失敗的才列出來。作品已經存好，關掉視窗也會繼續補
function Progress({ report, notify, onDone, onUndo, onAgain, close }) {
  const created = report.created
  const blocked = created.filter(row => PROTECTED.test(row.url))
  const reader = canReadPages()
  const unreadable = reader ? [] : blocked
  const background = created.length - blocked.length
  const [queued, setQueued] = useState(background)
  const [read, setRead] = useState(0)
  const [reading, setReading] = useState(reader && blocked.length > 0)
  const [failed, setFailed] = useState([])
  const finished = queued === 0 && !reading
  const total = background + (reader ? blocked.length : 0)
  const done = Math.max(0, background - queued) + read
  const readPages = rows => {
    setReading(true)
    setRead(0)
    fillFromPages(api, rows, index => setRead(index)).finally(() => { setRead(rows.length); setReading(false); onDone() })
  }
  useEffect(() => { if (reader && blocked.length) readPages(blocked) }, [])
  useEffect(() => {
    if (queued === 0) return
    const timer = setInterval(() => api('/intake/status').then(state => setQueued(Math.min(queued, state.queued))).catch(() => {}), POLL_MS)
    return () => clearInterval(timer)
  }, [queued])
  useEffect(() => {
    if (!finished || !created.length) return
    onDone()
    const ids = new Set(created.map(row => row.id))
    const skip = new Set(unreadable.map(row => row.id))
    api('/attention').then(({ groups }) => setFailed([...groups.retry.map(row => ({ ...row, again: 'retry' })), ...groups.manual.map(row => ({ ...row, again: 'read' }))].filter(row => ids.has(row.id) && !skip.has(row.id)))).catch(() => {})
  }, [finished])
  const retry = async row => {
    setFailed(current => current.filter(item => item.id !== row.id))
    try {
      if (row.again === 'read') { if (reader) readPages([{ id: row.id, url: row.url }]) } else {
        const { queued: count } = await post('/attention/retry', { ids: [row.id] })
        setQueued(count)
      }
    } catch (error) { notify(errorNote(error)) }
  }
  return <>
    <h2>{created.length ? `已收錄 ${created.length} 件` : '沒有新增作品'}</h2>
    {total > 0 && <div className="cover-meter import-meter" role="progressbar" aria-label="補資料進度" aria-valuenow={Math.round(done / total * 100)}><span style={{ width: `${done / total * 100}%` }} /></div>}
    <p className="import-status"><span>{total === 0 ? '' : !finished ? `背景補資料中 ${done} / ${total}，可以關掉這個視窗` : failed.length ? `有 ${failed.length} 件沒補到資料` : '資料補好了'}{report.duplicates.length > 0 ? `${total ? '・' : ''}${report.duplicates.length} 件已在典藏` : ''}</span></p>
    {unreadable.length > 0 && <p className="maint-note">有 {unreadable.length} 件的網站有防護，App 讀不到：{TOUCH ? '要補標題和標籤，請開啟「背景讀取」，或在作品頁按分享。' : '要補標題和標籤，請到作品頁按「收錄到 OAS」書籤。'}</p>}
    {failed.length > 0 && <ul className="import-hints" aria-label="沒補到資料的作品">{failed.map(row => <li key={row.id}><div className="import-hint-row"><span><code>{row.title || row.url}</code><small>沒讀到資料，作品還在</small></span>{(row.again === 'retry' || reader) && <button type="button" className="import-link" onClick={() => retry(row)}>重試</button>}</div></li>)}</ul>}
    <div className="import-buttons">{report.undo ? <button type="button" className="ghost" onClick={() => { onUndo(report.undo); onAgain(false) }}>復原</button> : <button type="button" className="ghost" onClick={close}>關閉</button>}
      <button type="button" className="save" onClick={() => onAgain(true)}>再貼一批</button></div>
  </>
}

export default function ImportDialog({ close, notify, onDone, onUndo, dirtyRef }) {
  const dialog = useFocusTrap()
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState(null)
  const [added, setAdded] = useState(new Set())
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState(null)
  // 貼上就解析（不連外）；打字時等一下再送
  useEffect(() => {
    if (!text.trim()) { setParsed(null); return }
    let live = true
    const timer = setTimeout(() => post('/import/parse', { text }).then(result => { if (live) setParsed(result) }).catch(error => { if (live) notify(errorNote(error)) }), 250)
    return () => { live = false; clearTimeout(timer) }
  }, [text])
  useEffect(() => {
    const listener = event => { if (event.key === 'Escape') { event.stopPropagation(); close() } }
    window.addEventListener('keydown', listener, true)
    return () => window.removeEventListener('keydown', listener, true)
  }, [close])
  // 貼了內容還沒收錄就關掉：先問
  useEffect(() => { if (dirtyRef) dirtyRef.current = Boolean(text.trim() && !report) }, [text, report])
  useEffect(() => () => { if (dirtyRef) dirtyRef.current = false }, [])
  const leave = async () => { if (text.trim() && !report && !await confirmLeave()) return; close() }
  const rows = parsed?.rows || []
  const urls = [...new Set([...rows.filter(willImport).map(row => row.url), ...added])]
  const owned = rows.filter(row => row.kind === 'url' && row.duplicate).length
  const others = [owned > 0 && `${owned} 件已在典藏`, parsed?.repeats > 0 && `${parsed.repeats} 個重複`, parsed?.dropped > 0 && `超過 ${parsed.limit} 件，有 ${parsed.dropped} 行沒算到`].filter(Boolean).join('・')
  const hints = rows.filter(row => row.kind !== 'url' || row.short)
  const shown = expanded ? hints : hints.slice(0, SHOWN_HINTS)
  const submit = async () => {
    setBusy(true)
    try {
      setReport(await post('/import/urls', { urls }))
      onDone()
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  // 剪貼簿不能用時（舊的 WebView、沒有權限）改成直接填進輸入框；已經有內容就不蓋掉
  const copyFormat = async () => {
    try {
      await navigator.clipboard.writeText(FORMAT)
      notify('已複製格式，貼到記事本填好，再整段貼回來')
    } catch {
      if (text.trim()) notify('這台裝置不能複製；清空輸入框再按一次，會直接填進來')
      else { setText(FORMAT); notify('這台裝置不能複製，已直接填進輸入框') }
    }
  }
  const again = fresh => {
    setReport(null)
    setAdded(new Set())
    if (fresh) { setText(''); setParsed(null) }
  }
  return <div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label="一次收錄" onClick={event => event.target === event.currentTarget && leave()}>
    <section className="oas-modal-card import-card">
      <button className="drawer-close" onClick={report ? close : leave} aria-label="關閉">×</button>
      {report ? <Progress report={report} notify={notify} onDone={onDone} onUndo={onUndo} onAgain={again} close={close} /> : <>
        <h2>一次收錄</h2>
        <textarea className="import-text" autoFocus={!TOUCH} autoCapitalize="none" autoCorrect="off" spellCheck={false} name="import-urls" aria-label="貼上網址或編號" value={text} onChange={event => setText(event.target.value)} placeholder="貼上網址，或 N123456、W123456、JM123456" />
        <div className="import-tools"><Rules /><button type="button" className="import-link" onClick={copyFormat}>複製格式</button></div>
        <p className="import-status">{!text.trim() ? <span>貼上網址或編號</span> : <><b>{urls.length}</b> 件會收錄{others && <span>・{others}</span>}</>}</p>
        {hints.length > 0 && <ul className="import-hints" aria-label="需要注意的內容">{shown.map((row, index) => row.kind === 'search'
          ? <SearchHint key={`${row.text} ${index}`} row={row} added={added} notify={notify} onAdd={url => setAdded(current => new Set(current).add(url))} />
          : <li key={`${row.line} ${index}`}><div className="import-hint-row"><span><code>{row.text || row.code}</code><small>{row.short ? `太短，可能是頁數；是編號的話寫成 N${row.code}` : row.reason}</small></span></div></li>)}</ul>}
        {hints.length > SHOWN_HINTS && !expanded && <button type="button" className="import-link" onClick={() => setExpanded(true)}>還有 {hints.length - SHOWN_HINTS} 行</button>}
        <button type="button" className="save import-go" disabled={busy || !urls.length} onClick={submit}>{busy ? '收錄中…' : `收錄 ${urls.length} 件`}</button>
      </>}
    </section>
  </div>
}
