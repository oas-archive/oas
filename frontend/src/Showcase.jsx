import { useEffect, useRef, useState } from 'react'
import { api, colourFor, coverRatio, coverSrc, Dropdown, EMPTY, groupTags, hideBroken, CoverFace, isFaceCover, isEditing, KINDS, loadTagCategories, Stars, STATUS_COLOURS, STATUS_OPTIONS, titleFor } from './shared.jsx'

const formatDate = value => {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`
}
const sinceText = value => {
  if (!value) return '尚未開啟'
  const days = Math.floor((Date.now() - new Date(value).getTime()) / 86400000)
  return days <= 0 ? '今天開啟過' : days < 30 ? `${days} 天前開啟` : `${formatDate(value)} 開啟`
}

// 展示頁：取代右側抽屜。左側完整比例的封面，右側預設為閱讀模式；按「編輯」或 E 才切換為表單。
// 封面容器帶 view-transition-name，搭配 App 的 View Transitions，從卡片位置放大進場、關閉時縮回原卡片。
export default function Showcase({ item, link, prev, next, direction = 0, onNavigate, onClose, onQuick, onSave, onRemove, onGraph, onTag, onVisit, onOpenDuplicate, onRelinked, collections = [], onMembership, onCreateCollection, onBulk }) {
  const [mode, setMode] = useState(item.id ? 'view' : 'edit')
  const [related, setRelated] = useState(null)
  const [loadedSrc, setLoadedSrc] = useState('')
  const [preview, setPreview] = useState('')
  const [categories, setCategories] = useState({})
  const [altOpen, setAltOpen] = useState(false)
  const [unreadOnly, setUnreadOnly] = useState(() => { try { return localStorage.getItem('oas-related-unread') === '1' } catch { return false } })
  const toggleUnread = value => { setUnreadOnly(value); try { localStorage.setItem('oas-related-unread', value ? '1' : '0') } catch { /* 只影響這次 */ } }
  useEffect(() => { loadTagCategories().then(setCategories) }, [])
  const colour = colourFor(item)
  const lastStep = useRef(0)

  // 切換作品時不重建整個展示頁，只重設狀態；內容區以 key 觸發依方向的淡入動畫。
  useEffect(() => { setMode(item.id ? 'view' : 'edit'); setPreview(''); setAltOpen(false) }, [item.id])

  // 原圖等放大動畫結束才載入：動畫期間先顯示已快取的縮圖，避免大圖解碼卡住轉場（從書架開啟時特別明顯）。
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    setSettled(false)
    const timer = setTimeout(() => setSettled(true), 480)
    return () => clearTimeout(timer)
  }, [item.id])

  // 前後兩件等動畫結束、瀏覽器空閒時再預先載入，切換時不必等待下載。
  useEffect(() => {
    if (!settled) return
    const idle = window.requestIdleCallback || (callback => setTimeout(callback, 200))
    const cancel = window.cancelIdleCallback || clearTimeout
    const handle = idle(() => {
      for (const entry of [prev, next]) {
        if (!entry?.image_url) continue
        new Image().src = coverSrc(entry, 360)
        new Image().src = coverSrc(entry)
      }
    })
    return () => cancel(handle)
  }, [settled, prev?.id, next?.id])
  const step = target => {
    const now = performance.now()
    if (!target || now - lastStep.current < 160) return  // 按住方向鍵時限制切換速度
    lastStep.current = now
    onNavigate(target, target === prev ? -1 : 1)
  }

  useEffect(() => {
    if (!item.id) return
    let live = true
    setRelated(null)
    api(`/resources/${item.id}/related?limit=12${unreadOnly ? '&unread=true' : ''}`).then(rows => live && setRelated(rows)).catch(() => live && setRelated([]))
    return () => { live = false }
  }, [item.id, item.updated_at, unreadOnly])

  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [])

  useEffect(() => {
    const listener = event => {
      if (mode !== 'view' || isEditing() || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('.palette')) return
      if (event.key === 'ArrowLeft' && prev) { event.preventDefault(); step(prev) }
      if (event.key === 'ArrowRight' && next) { event.preventDefault(); step(next) }
      if (event.key.toLowerCase() === 'e') { event.preventDefault(); setMode('edit') }
      if (/^[0-5]$/.test(event.key) && item.id) { event.preventDefault(); onQuick(item, { rating: Number(event.key) }).catch(() => {}) }
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [mode, prev, next, item, onNavigate, onQuick])

  const ratio = isFaceCover(item) && !(mode === 'edit' && preview) ? 1.15 : coverRatio(item)
  return <div className="showcase" role="dialog" aria-modal="true" aria-label={titleFor(item)} style={{ '--cover-colour': colour }}>
    <Backdrop item={item} onClose={onClose} />
    <button type="button" className="showcase-close" onClick={onClose} aria-label="關閉（Esc）">×</button>
    {prev && mode === 'view' && <button type="button" className="showcase-step is-prev" onClick={() => step(prev)} aria-label="上一件（←）">‹</button>}
    {next && mode === 'view' && <button type="button" className="showcase-step is-next" onClick={() => step(next)} aria-label="下一件（→）">›</button>}

    <div className="showcase-stage" key={item.id ?? 'new'} data-direction={direction > 0 ? 'next' : direction < 0 ? 'prev' : 'none'}>
      <figure className="showcase-cover" style={{ aspectRatio: `1 / ${ratio}`, viewTransitionName: 'oas-cover' }}>
        {mode === 'edit' && preview && preview !== item.image_url ? <img src={preview} alt="" onError={hideBroken} /> : isFaceCover(item) ? <CoverFace item={item} width={480} /> : item.image_url ? <>
          <img src={coverSrc(item, 360)} alt="" onError={hideBroken} />
          {settled && <img className={`is-full${loadedSrc === coverSrc(item) ? ' is-loaded' : ''}`} src={coverSrc(item)} alt="" decoding="async" onLoad={event => setLoadedSrc(event.currentTarget.getAttribute('src'))} onError={hideBroken} />}
        </> : <span>OAS<small>尚未設定封面</small></span>}
      </figure>

      <section className="showcase-panel">
        {mode === 'view' ? <>
          <p className="showcase-facts">{item.kind && <span>{item.kind}</span>}<span>{item.source_name}</span><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}</span></p>
          <h1 className="showcase-title">{titleFor(item)}</h1>
          <div className="showcase-rate">
            <Stars value={item.rating} allowHalf onChange={rating => onQuick(item, { rating }).catch(() => {})} />
            <b>{item.rating > 0 ? Number(item.rating).toFixed(1) : '未評分'}</b>
            <Dropdown ariaLabel="閱讀狀態" value={item.status} options={STATUS_OPTIONS} onChange={status => onQuick(item, { status }).catch(() => {})} />
          </div>
          {item.id && (item.status === '閱讀中' || item.progress) && <ProgressField key={`${item.id}-${item.progress}`} item={item} onQuick={onQuick} />}
          <div className="showcase-actions">
            {link ? <a className="primary" href={link} target="_blank" rel="noreferrer noopener" onClick={() => onVisit(item)}>前往閱讀 ↗</a> : <span className="primary is-disabled" title="此來源尚未設定網址規則">無來源連結</span>}
            <button type="button" className="ghost" onClick={() => setMode('edit')}>編輯 <kbd>E</kbd></button>
            {item.id && <CollectionMenu item={item} collections={collections} onMembership={onMembership} onCreate={onCreateCollection} />}
            {related?.length > 0 && <button type="button" className="ghost" onClick={() => onGraph(item)}>在關聯圖中展開</button>}
            {item.id && !item.link_dead && !altOpen && <button type="button" className="ghost subtle" onClick={() => setAltOpen(true)} title="作品在來源網站被下架、找不到時">來源失效？</button>}
          </div>
          {item.id && (item.link_dead || altOpen) && <AlternativesPanel item={item} onQuick={onQuick} onRelinked={onRelinked} onOpenOwned={onOpenDuplicate} onClose={() => setAltOpen(false)} />}
          {item.tags.length > 0 && <div className="showcase-tag-groups">{groupTags(item.tags, categories).map(group => <div key={group.key} className="showcase-tags">
            <span className="tag-group-label">{group.label}</span>
            {group.names.map(tag => <button key={tag} type="button" onClick={() => onTag(tag)} title={`在典藏館中查看 #${tag}`}>{group.key === 'tag' ? '#' : ''}{tag}</button>)}
          </div>)}</div>}
          <blockquote className={`showcase-note${item.note ? '' : ' is-empty'}`} onClick={() => !item.note && setMode('edit')}>{item.note || '還沒有筆記。按 E 寫下只屬於你的閱讀記憶。'}</blockquote>
          <dl className="showcase-meta">
            <div><dt>收錄</dt><dd>{formatDate(item.created_at) || '—'}</dd></div>
            <div><dt>閱讀</dt><dd>{sinceText(item.last_opened_at)}</dd></div>
            <div><dt>代碼</dt><dd title={item.code}>{item.code}</dd></div>
          </dl>
          <section className="showcase-related">
            <header><h2>相似的典藏</h2><div className="related-filter" role="group" aria-label="相似作品範圍">{[[false, '全部'], [true, '只看未讀']].map(([value, label]) => <button key={label} type="button" aria-pressed={unreadOnly === value} className={unreadOnly === value ? 'is-active' : ''} onClick={() => toggleUnread(value)}>{label}</button>)}</div></header>
            {related === null ? <p className="muted">計算中…</p> : related.length === 0 ? <p className="muted">{unreadOnly ? '未讀的作品裡沒有相似的。切到「全部」看看。' : '沒有足夠的共用標籤。加入更多標籤後，這裡會出現相近的作品。'}</p> :
              <div className="related-row">{related.map(entry => <button key={entry.id} type="button" onClick={() => onNavigate(entry, 0)} style={{ '--node': colourFor(entry) }} title={entry.shared.map(tag => `#${tag}`).join(' ')}>
                <i>{entry.image_url && <img src={coverSrc(entry, 240)} alt="" loading="lazy" onError={hideBroken} />}<em>{Math.round(entry.similarity * 100)}%</em></i>
                <span>{titleFor(entry)}</span>
                {entry.shared?.length > 0 && <small>{entry.shared.map(tag => `#${tag}`).join(' ')}</small>}
              </button>)}</div>}
          </section>
        </> : <EditForm item={item} link={link} onBulk={onBulk} onPreview={setPreview} onOpenDuplicate={onOpenDuplicate} onCancel={() => item.id ? setMode('view') : onClose()} onSave={async payload => { if (await onSave(payload)) setMode('view') }} onRemove={onRemove} />}
      </section>
    </div>
    {mode === 'view' && <p className="showcase-keys" aria-hidden="true"><kbd>←</kbd><kbd>→</kbd> 切換　<kbd>1</kbd>–<kbd>5</kbd> 評分　<kbd>E</kbd> 編輯　<kbd>Esc</kbd> 關閉</p>}
  </div>
}

// 「收錄到 OAS」書籤：在來源網站頁面上執行，讀取標題、封面與標籤後開啟本機收錄表單。
// 只讀取使用者自己瀏覽器裡已顯示的頁面內容，不需要伺服器去連那個網站，所以有防護的網站也能用。
const BOOKMARKLET = `javascript:(()=>{const q=s=>[...document.querySelectorAll(s)],m=n=>(document.querySelector('meta[property="'+n+'"],meta[name="'+n+'"]')||{}).content||'',h=location.hostname;let t=m('og:title')||document.title,i=m('og:image'),g=[];if(/nhentai/.test(h)){const c=document.querySelector('#cover img');if(c){t=c.alt||t;i=c.src||c.dataset.src||i}g=q('#tags a[href^="/"] .name').map(e=>e.textContent)}else if(/18comic|jmcomic/.test(h)){t=(document.querySelector('h1')||{}).textContent||t;g=q('.tag-block a,span[data-type] a').map(e=>e.textContent)}else if(/wnacg/.test(h)){t=(document.querySelector('h2')||{}).textContent||t;g=q('.tagshow').map(e=>e.textContent)}else if(/missav/.test(h)){g=q('a[href*="/genres/"]').map(e=>e.textContent)}else if(/hanime1/.test(h)){g=q('.single-video-tag a').map(e=>e.childNodes[0].textContent)}else{g=m('keywords').split(/[,，]/)}const d={url:location.href,title:(t||'').trim(),image_url:i,tags:[...new Set(g.map(s=>s.trim()).filter(Boolean))].slice(0,60)};window.open('${window.location.origin}/?add='+encodeURIComponent(JSON.stringify(d)),'_blank')})()`

// 來源失效：標記失效，並到 nhentai、紳士漫畫找同名作品，選中後改用新來源（舊連結會記在筆記裡）。
// 加入書單：勾選即加入或移出，也可以直接建立新書單並加入。
function CollectionMenu({ item, collections, onMembership, onCreate }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const root = useRef(null)
  const member = new Set(item.collections || [])
  useEffect(() => {
    if (!open) return
    const close = event => { if (!root.current?.contains(event.target)) setOpen(false) }
    const escape = event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false) } }
    document.addEventListener('pointerdown', close)
    root.current?.addEventListener('keydown', escape)
    const node = root.current
    return () => { document.removeEventListener('pointerdown', close); node?.removeEventListener('keydown', escape) }
  }, [open])
  const create = async event => {
    event.preventDefault()
    if (!name.trim()) return
    const made = await onCreate(name.trim())
    if (made) { await onMembership([item.id], made.id, 'add'); setName('') }
  }
  const count = member.size
  return <div className="collection-menu" ref={root}>
    <button type="button" className={`ghost${count ? ' is-member' : ''}`} aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)}>
      {count ? `在 ${count} 個書單` : '加入書單'}
    </button>
    {open && <div className="collection-popover" role="dialog" aria-label="加入書單">
      {collections.length > 0 ? <ul>{collections.map(collection => {
        const checked = member.has(collection.id)
        return <li key={collection.id}><label>
          <input type="checkbox" checked={checked} onChange={() => onMembership([item.id], collection.id, checked ? 'remove' : 'add')} />
          <span>{collection.name}</span><small>{collection.count}</small>
        </label></li>
      })}</ul> : <p>還沒有書單，輸入名稱建立第一個。</p>}
      <form onSubmit={create}><input value={name} onChange={event => setName(event.target.value)} placeholder="新書單名稱" aria-label="新書單名稱" /><button type="submit" disabled={!name.trim()}>建立並加入</button></form>
    </div>}
  </div>
}

function AlternativesPanel({ item, onQuick, onRelinked, onOpenOwned, onClose }) {
  const [query, setQuery] = useState('')
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const search = async text => {
    setBusy(true); setError('')
    try {
      const result = await api(`/resources/${item.id}/alternatives${text !== undefined ? `?q=${encodeURIComponent(text)}` : ''}`)
      setData(result); setQuery(result.query)
    } catch (failure) { setError(failure.message) } finally { setBusy(false) }
  }
  useEffect(() => { search() }, [item.id])
  const relink = async row => {
    if (!confirm(`改用「${row.source_name}」的這一件？
${row.title}

原本的連結會記在筆記裡。`)) return
    try { onRelinked(await api(`/resources/${item.id}/relink`, { method: 'POST', body: JSON.stringify({ source_name: row.source_name, code: row.code }) })) }
    catch (failure) { setError(failure.message) }
  }
  return <section className="alt-panel">
    <header>
      <div><b>{item.link_dead ? '此來源已標記為失效' : '來源失效？'}</b><small>到 nhentai、紳士漫畫找同名作品，選中後改用新來源</small></div>
      {item.link_dead
        ? <button type="button" className="ghost" onClick={() => onQuick(item, { link_dead: false }).catch(() => {})}>取消失效標記</button>
        : <><button type="button" className="ghost" onClick={() => onQuick(item, { link_dead: true }).catch(() => {})}>標記為失效</button><button type="button" className="alt-close" onClick={onClose} aria-label="收起">×</button></>}
    </header>
    <form className="alt-search" onSubmit={event => { event.preventDefault(); search(query) }}>
      <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋關鍵字" aria-label="搜尋關鍵字" />
      <button type="submit" className="ghost" disabled={busy}>{busy ? '搜尋中…' : '搜尋'}</button>
    </form>
    {error && <p className="alt-note is-warn">{error}</p>}
    {data?.errors?.map(message => <p key={message} className="alt-note">{message}</p>)}
    {data && !busy && data.results.length === 0 && <p className="alt-note">找不到同名作品，可以換個關鍵字（例如只留作品名，或改用日文、英文原名）。</p>}
    {data && data.results.length > 0 && <div className="alt-grid">{data.results.map(row => <article key={`${row.source_name}-${row.code}`} className="alt-card">
      <i>{row.thumb && <img src={row.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" onError={hideBroken} />}</i>
      <div>
        <small>{row.source_name}{row.pages ? `，${row.pages} 頁` : ''}</small>
        <a href={row.url} target="_blank" rel="noreferrer noopener" title="在來源網站開啟確認">{row.title}</a>
        {row.owned_id ? <button type="button" className="ghost" onClick={() => onOpenOwned(row.owned_id)}>已在典藏中，開啟</button> : <button type="button" className="primary" onClick={() => relink(row)}>改用這個</button>}
      </div>
    </article>)}</div>}
  </section>
}

function EditForm({ item, link, onCancel, onSave, onRemove, onPreview, onOpenDuplicate, onBulk }) {
  const [form, setForm] = useState({ ...EMPTY, ...item, tags: (item.tags || []).join(', ') })
  const [url, setUrl] = useState(item.source_url || link || '')
  const [lookup, setLookup] = useState(item.duplicate ? { duplicate: item.duplicate } : null)
  const [busy, setBusy] = useState(false)
  const bookmarklet = useRef(null)
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  useEffect(() => { onPreview(form.image_url) }, [form.image_url])
  // React 會擋下 javascript: 連結，所以直接寫入 DOM 屬性。
  useEffect(() => { bookmarklet.current?.setAttribute('href', BOOKMARKLET) }, [])

  const fetchInfo = async () => {
    if (!url.trim()) return
    setBusy(true)
    try {
      const info = await api('/metadata', { method: 'POST', body: JSON.stringify({ url: url.trim() }) })
      setLookup(info)
      if (info.url && info.url !== url.trim()) setUrl(info.url)  // 輸入純數字時換成完整網址，讓你看到收錄的是哪一頁
      setForm(current => {
        const next = { ...current }
        if (!item.id) { next.source_name = info.source_name; next.code = info.code }
        // 新收錄直接帶入；編輯既有作品時只補空白欄位、標籤取聯集，不覆蓋你已整理過的內容。
        if (info.title && (!item.id || !current.title.trim())) next.title = info.title
        if (info.image_url && (!item.id || !current.image_url.trim())) next.image_url = info.image_url
        if (info.tags.length) {
          const existing = current.tags.split(/[,，]/).map(value => value.trim()).filter(Boolean)
          next.tags = [...new Set([...existing, ...info.tags])].join(', ')
        }
        return next
      })
    } catch (error) { setLookup({ message: error.message }) } finally { setBusy(false) }
  }
  const submit = event => { event.preventDefault(); onSave({ ...form, rating: Number(form.rating), tags: form.tags.split(/[,，]/).map(value => value.trim()).filter(Boolean) }) }
  const duplicate = lookup?.duplicate && lookup.duplicate.id !== item.id ? lookup.duplicate : null
  return <form className="showcase-form" onSubmit={submit}>
    <p className="overline">{item.id ? '編輯典藏' : '收錄新典藏'}</p>
    <div className="fetch-box">
      <label>作品網址<div className="fetch-row"><input autoFocus={!item.id} value={url} onChange={event => setUrl(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); fetchInfo() } }} placeholder="貼上作品頁網址，或直接輸入 N 網的作品編號（例如 123456）" /><button type="button" className="primary" onClick={fetchInfo} disabled={busy || !url.trim()}>{busy ? '讀取中…' : item.id ? '補齊資訊' : '抓取資訊'}</button></div></label>
      {duplicate && <p className="fetch-note is-warn">這件已在典藏中：「{duplicate.title}」<button type="button" onClick={() => onOpenDuplicate(duplicate.id)}>開啟它</button></p>}
      {!duplicate && lookup?.similar?.map(entry => <p key={entry.id} className="fetch-note is-warn">可能是同一本的另一個版本：「{entry.title}」（{entry.source_name}）<button type="button" onClick={() => onOpenDuplicate(entry.id)}>開啟它</button></p>)}
      {!item.id && onBulk && <p className="fetch-note"><button type="button" onClick={onBulk}>一次貼多個網址</button>，在背景慢慢補資料。</p>}
      {lookup?.fetched && !duplicate && <p className="fetch-note is-ok">已帶入{lookup.title ? '標題、' : ''}{lookup.image_url ? '封面、' : ''}{lookup.tags?.length || 0} 個標籤（來源：{lookup.source_name}），請確認後儲存。</p>}
      {lookup?.message && <p className={`fetch-note${lookup.blocked ? ' is-warn' : ''}`}>{lookup.message}</p>}
    </div>
    <label>作品名稱<input value={form.title} onChange={event => set('title', event.target.value)} /></label>
    <div className="form-split">
      <label>代碼或網址<input required value={form.code} onChange={event => set('code', event.target.value)} /></label>
      <label>來源<input value={form.source_name} onChange={event => set('source_name', event.target.value)} /></label>
    </div>
    <div className="field"><span>類型（留空則沿用來源的類型）</span><Dropdown ariaLabel="類型" value={form.kind || ''} onChange={value => set('kind', value)} options={[{ value: '', label: '沿用來源' }, ...KINDS.map(value => ({ value, label: value }))]} /></div>
    <label>封面網址<input value={form.image_url} onChange={event => set('image_url', event.target.value)} /></label>
    <label>標籤（以逗號分隔）<input value={form.tags} onChange={event => set('tags', event.target.value)} /></label>
    <div className="form-split">
      <div className="field"><span>閱讀狀態</span><Dropdown ariaLabel="閱讀狀態" value={form.status} onChange={value => set('status', value)} options={STATUS_OPTIONS} /></div>
      <div className="field"><span>評分 <b>{Number(form.rating).toFixed(1)}</b></span><Stars id="oas-rating" value={Number(form.rating)} allowHalf onChange={value => set('rating', value)} /></div>
    </div>
    <label>私人筆記<textarea value={form.note} onChange={event => set('note', event.target.value)} placeholder="只屬於你的閱讀記憶…" /></label>
    <div className="form-actions">
      <button type="submit" className="primary">儲存</button>
      <button type="button" className="ghost" onClick={onCancel}>取消</button>
      {item.id && <button type="button" className="remove" onClick={onRemove}>移出典藏</button>}
    </div>
    {!item.id && <details className="bookmarklet-help">
      <summary>有防護的網站（禁漫、missav…）怎麼收錄？</summary>
      <p>把這顆按鈕拖曳到瀏覽器的書籤列：<a ref={bookmarklet} className="bookmarklet" onClick={event => event.preventDefault()} title="拖曳到書籤列">＋ 收錄到 OAS</a></p>
      <p>之後在任何作品頁點一下這個書籤，就會讀取該頁的標題、封面與標籤，並開啟這個收錄表單。只讀取你已經開著的頁面，典藏館需在執行中。</p>
    </details>}
  </form>
}

// 背景：封面縮圖縮小後模糊再放大（比整張大圖直接模糊省很多運算），新舊兩層交叉淡化，切換時不閃黑。
function Backdrop({ item, onClose }) {
  const [layers, setLayers] = useState(() => [{ key: item.id, src: item.image_url ? coverSrc(item, 360) : '' }])
  useEffect(() => {
    setLayers(current => current[current.length - 1]?.key === item.id ? current : [...current.slice(-1), { key: item.id, src: item.image_url ? coverSrc(item, 360) : '' }])
  }, [item.id])
  return <div className="showcase-backdrop" onClick={onClose} aria-hidden="true">
    {layers.map(layer => layer.src && <img key={layer.key} src={layer.src} alt="" onError={hideBroken} />)}
  </div>
}

// 閱讀進度：自由填寫（第幾話、第幾頁都可以），離開輸入框或按 Enter 就儲存；側欄「繼續閱讀」會顯示。
function ProgressField({ item, onQuick }) {
  const [value, setValue] = useState(item.progress || '')
  const save = () => { if (value.trim() !== (item.progress || '')) onQuick(item, { progress: value.trim() }).catch(() => {}) }
  return <label className="showcase-progress"><span>看到</span>
    <input value={value} maxLength={40} onChange={event => setValue(event.target.value)} onBlur={save} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { event.stopPropagation(); setValue(item.progress || ''); event.currentTarget.blur() } }} placeholder="例如：第 3 話、p.45" aria-label="閱讀進度" />
  </label>
}
