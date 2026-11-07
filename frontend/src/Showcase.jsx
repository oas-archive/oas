import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { canReadPages, readPage } from './pageReader.js'
import { api, colourFor, coverRatio, coverSrc, Dropdown, EMPTY, groupTags, hideBroken, CoverFace, isFaceCover, isEditing, KINDS, loadTagCategories, Stars, STATUS_COLOURS, statusOptions, shortTitle, titleFor, TOUCH, ownsSideSwipe, whenIdle, ask, confirmLeave, useFocusTrap } from './shared.jsx'

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

// 下拉關閉：手指移動距離 × 0.55 才是畫面位移（阻力）；位移超過 96px，或以 0.45 px/ms 以上往下甩（至少 24px）就關閉
const PULL_RESISTANCE = 0.55
const PULL_CLOSE = 96
const PULL_FLICK = 0.45
const PULL_EASE = '.3s cubic-bezier(.23,1,.32,1)'

// 封面開關動畫（FLIP）：只對封面做 transform，交給合成器跑，不必像 View Transition 那樣拍整頁快照。
const COVER_MS = 580  // 封面飛行：打開和關閉同樣時間
const PANEL_MS = 500
const BACKDROP_MS = 400
const COVER_EASE = 'cubic-bezier(.2,.8,.2,1)'
const FADE_MS = 300
const COVER_HOME = 'translate(0px, 0px) scale(1)'
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const usableRect = rect => rect?.width > 0 && rect.height > 0
// 把定位在 home 的封面對到 rect：等比例縮放不變形，比例不同時置中放在 rect 裡面
const coverFrame = (home, rect) => {
  const scale = Math.min(rect.width / home.width, rect.height / home.height)
  const x = rect.left + rect.width / 2 - home.left - home.width / 2
  const y = rect.top + rect.height / 2 - home.top - home.height / 2
  return `translate(${x}px, ${y}px) scale(${scale})`
}
// 開啟：有卡片位置就從卡片放大；沒有（網址直接開、卡片不在畫面內）就淡入。
const playShowcaseOpen = (cover, origin) => {
  if (!cover || reducedMotion()) return
  if (usableRect(origin)) cover.animate({ transform: [coverFrame(cover.getBoundingClientRect(), origin), COVER_HOME] }, { duration: COVER_MS, easing: COVER_EASE })
  else cover.animate({ opacity: [0, 1], transform: ['scale(.97)', 'scale(1)'] }, { duration: PANEL_MS, easing: COVER_EASE })
}
// 關閉：封面從目前的位置（可能還在放大途中、或被手指拉下）縮回 target，其餘淡出；target 不能用就整個淡出。
// 回傳動畫結束的 Promise；不需要動畫時回傳 null，由呼叫端直接卸載。動畫的結束狀態留在元素上，靠卸載移除。
export const playShowcaseClose = target => {
  const shell = document.querySelector('.showcase')
  const stage = shell?.querySelector('.showcase-stage'), cover = shell?.querySelector('.showcase-cover')
  if (!cover || !stage || reducedMotion()) return null
  shell.style.pointerEvents = 'none'  // 動畫中點擊直接落到底下的頁面，不必等它播完
  const from = cover.getBoundingClientRect()
  const opacity = Number(stage.style.opacity || 1)
  const flies = usableRect(target) && from.bottom > 0 && from.top < window.innerHeight  // 手機往下捲過封面時，封面不在畫面內，不飛
  const fadeOut = { duration: FADE_MS, easing: 'ease-out', fill: 'forwards' }
  const running = [...shell.querySelectorAll('.showcase-close, .showcase-step, .showcase-keys')].map(element => element.animate({ opacity: [1, 0] }, fadeOut))
  const backdrop = shell.querySelector('.showcase-backdrop')
  if (backdrop) running.push(backdrop.animate({ opacity: [1, 0] }, { ...fadeOut, duration: flies ? BACKDROP_MS : PANEL_MS }))
  if (flies) {
    cover.getAnimations().forEach(animation => animation.cancel())
    // 下拉關閉時整頁帶著位移：放掉位移，封面的起點仍是剛剛量到的位置，看起來是從手指放開處縮回去
    stage.style.transition = 'none'; stage.style.transform = ''; stage.style.opacity = ''
    const home = cover.getBoundingClientRect()
    const flight = { duration: COVER_MS, fill: 'forwards' }
    running.push(cover.animate({ transform: [coverFrame(home, from), coverFrame(home, target)] }, { ...flight, easing: COVER_EASE }))
    running.push(cover.animate({ opacity: [1, 1, 0], offset: [0, 0.8, 1] }, flight))  // 最後才淡掉，露出底下同一張卡片，比例不同也不會跳一下
    const panel = shell.querySelector('.showcase-panel')
    if (panel) running.push(panel.animate({ opacity: [opacity, 0], transform: ['translateX(0px)', 'translateX(18px)'] }, { ...fadeOut, duration: PANEL_MS }))
  } else running.push(stage.animate({ opacity: [opacity, 0] }, { ...fadeOut, duration: PANEL_MS }))
  return Promise.all(running.map(animation => animation.finished))
}

// 展示頁：取代右側抽屜。左側完整比例的封面，右側預設為閱讀模式；按「編輯」或 E 才切換為表單。
// 封面從卡片位置放大進場（flipFrom 是 App 記下的卡片位置）、關閉時縮回原卡片（playShowcaseClose）。
export default function Showcase({ item, link, prev, next, direction = 0, flipFrom, coverHint, warm = false, dirtyRef, onNavigate, onClose, onQuick, onSave, onRemove, onTag, onVisit, onOpenDuplicate, onRelinked, collections = [], onMembership, onCreateCollection, onBulk }) {
  const [mode, setMode] = useState(item.id ? 'view' : 'edit')
  const [related, setRelated] = useState(null)
  const [loadedSrc, setLoadedSrc] = useState('')
  const [preview, setPreview] = useState('')
  const [categories, setCategories] = useState({})
  const [altOpen, setAltOpen] = useState(false)
  const [twins, setTwins] = useState([])  // 可能和這件重複的作品（重複作品偵測的結果）
  const [unreadOnly, setUnreadOnly] = useState(() => { try { return localStorage.getItem('oas-related-unread') === '1' } catch { return false } })
  const toggleUnread = value => { setUnreadOnly(value); try { localStorage.setItem('oas-related-unread', value ? '1' : '0') } catch { /* 只影響這次 */ } }
  useEffect(() => { loadTagCategories().then(setCategories) }, [item.id, item.updated_at])  // 存檔可能帶來新標籤的分類
  const colour = colourFor(item)
  const lastStep = useRef(0)

  // 切換作品時不重建整個展示頁，只重設狀態；內容區以 key 觸發依方向的淡入動畫。
  useEffect(() => { setMode(item.id ? 'view' : 'edit'); setPreview(''); setAltOpen(false) }, [item.id])

  // 原圖等放大動畫結束才載入：動畫期間先顯示已快取的縮圖，避免大圖解碼卡住轉場（從書架開啟時特別明顯）。
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    setSettled(false)
    const timer = setTimeout(() => setSettled(true), 600)  // 封面動畫 580ms 之後
    return () => clearTimeout(timer)
  }, [item.id])

  // 可能重複的作品：等轉場結束再查，不拖慢開啟
  useEffect(() => {
    setTwins([])
    if (!settled || !item.id) return
    let live = true
    api(`/duplicates/of/${item.id}`).then(result => { if (live) setTwins(result.items) }).catch(() => {})
    return () => { live = false }
  }, [settled, item.id])

  // 前後兩件等動畫結束、瀏覽器空閒時再預先載入，切換時不必等待下載。
  useEffect(() => {
    if (!settled) return
    return whenIdle(() => {
      for (const entry of [prev, next]) {
        if (!entry?.image_url) continue
        new Image().src = coverSrc(entry, 240)
        new Image().src = coverSrc(entry)
      }
    }, 2000)
  }, [settled, prev?.id, next?.id])
  const step = target => {
    const now = performance.now()
    if (!target || now - lastStep.current < 160) return  // 按住方向鍵時限制切換速度
    lastStep.current = now
    onNavigate(target, target === prev ? -1 : 1)
  }

  // 相似作品等切換動畫結束才抓，避免動畫途中插入一整排卡片造成卡頓
  useEffect(() => {
    if (!item.id) return
    setRelated(null)
    if (!settled) return
    let live = true
    let cancelIdle = () => {}
    // 回來的資料也等瀏覽器有空才畫：一排卡片的版面計算不要疊在原圖、背景出現的同一格
    api(`/resources/${item.id}/related?limit=12${unreadOnly ? '&unread=true' : ''}`)
      .then(rows => { if (live) cancelIdle = whenIdle(() => { if (live) setRelated(rows) }) })
      .catch(() => live && setRelated([]))
    return () => { live = false; cancelIdle() }
  }, [item.id, item.updated_at, unreadOnly, settled])

  // 手機：在作品頁左右滑換上一件／下一件（取代左右按鈕）；從相似作品列、評分星星、輸入框開始滑則不處理。
  // 拖曳時整頁跟著手指，放開後滑出畫面再換下一件（新的一件由另一側滑入）；沒有上／下一件時有阻力並彈回。
  // 內容捲在最上面時往下拉：整頁跟著手指往下（有阻力）、變淡；放開超過門檻或往下甩就關閉（封面縮回原卡片），否則彈回。
  const shell = useRef(null)
  useFocusTrap(!warm, shell)
  // 封面放大：在瀏覽器畫出第一格之前就定位到卡片上。展示頁內切換作品（沒有卡片位置）不做，由方向動畫負責
  const mounted = useRef(false)
  useLayoutEffect(() => {
    const first = !mounted.current, origin = flipFrom?.current
    mounted.current = true
    if (flipFrom) flipFrom.current = null
    if (!warm && (first || origin)) playShowcaseOpen(shell.current?.querySelector('.showcase-cover'), origin)
  }, [item.id])
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const element = shell.current
    if (!element || mode !== 'view') return
    let start = null
    const stage = () => element.querySelector('.showcase-stage')
    const move = (dx, animate) => {
      const target = stage()
      if (!target) return
      target.style.transition = animate ? 'transform .3s cubic-bezier(.22,.61,.36,1), opacity .3s' : 'none'
      target.style.transform = dx ? `translate3d(${dx}px, 0, 0)` : ''
      target.style.opacity = dx ? String(Math.max(0.2, 1 - Math.abs(dx) / element.clientWidth)) : ''
    }
    const pull = (offset, animate) => {
      const target = stage()
      if (!target) return
      target.style.transition = animate ? `transform ${PULL_EASE}, opacity ${PULL_EASE}` : 'none'
      target.style.transform = offset ? `translate3d(0, ${offset}px, 0) scale(${1 - Math.min(offset, 300) / 3000})` : ''
      target.style.opacity = offset ? String(Math.max(0.35, 1 - offset / 360)) : ''
    }
    // 從輸入框、評分星星開始，或手指下有已往下捲的區塊時，不當成下拉關閉
    const pullable = node => {
      for (; node && node !== element; node = node.parentElement) {
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(node.tagName) || node.getAttribute?.('role') === 'slider' || node.scrollTop > 0) return false
      }
      return true
    }
    const onStart = event => {
      const touch = event.touches[0], at = performance.now()
      start = event.touches.length === 1 ? { x: touch.clientX, y: touch.clientY, at, axis: null, dx: 0, dy: 0, side: ownsSideSwipe(event.target), pull: pullable(event.target), lastY: 0, lastAt: at, speed: 0 } : null
    }
    const onMove = event => {
      if (!start) return
      const touch = event.touches[0], dx = touch.clientX - start.x, dy = touch.clientY - start.y
      if (!start.axis) {
        if (Math.abs(dx) + Math.abs(dy) < 10) {
          // 方向還沒定時就先擋住往下拉的原生捲動：瀏覽器一旦開始捲動，之後的 touchmove 就不能取消
          if (start.pull && dy > Math.abs(dx) && event.cancelable) event.preventDefault()
          return
        }
        start.axis = Math.abs(dx) > Math.abs(dy) * 1.2 ? 'x' : start.pull && dy > 0 ? 'pull' : 'y'
      }
      if (start.axis === 'pull') {
        if (event.cancelable) event.preventDefault()
        const now = performance.now()
        start.speed = 0.6 * (dy - start.lastY) / Math.max(1, now - start.lastAt) + 0.4 * start.speed  // 平滑後的往下速度（px/ms）
        start.lastY = dy; start.lastAt = now
        start.dy = Math.max(0, dy)
        pull(start.dy * PULL_RESISTANCE, false)
        return
      }
      if (start.axis !== 'x' || start.side) return
      start.dx = (dx < 0 ? next : prev) ? dx : dx * 0.3
      move(start.dx, false)
    }
    const onEnd = () => {
      const state = start
      start = null
      if (state?.axis === 'pull') {
        const offset = state.dy * PULL_RESISTANCE
        const speed = performance.now() - state.lastAt > 100 ? 0 : state.speed  // 停住後才放開不算甩
        if (offset > PULL_CLOSE || (speed > PULL_FLICK && offset > 24)) closeRef.current()  // 封面從被拉下的位置縮回原卡片
        else pull(0, true)
        return
      }
      if (!state || state.axis !== 'x' || state.side) return
      const target = state.dx < 0 ? next : prev
      const speed = Math.abs(state.dx) / Math.max(1, performance.now() - state.at)
      if (!target || !(Math.abs(state.dx) > element.clientWidth * 0.22 || (speed > 0.35 && Math.abs(state.dx) > 30))) { move(0, true); return }
      move(Math.sign(state.dx) * element.clientWidth * 0.6, true)  // 往外滑出，接著換成下一件
      setTimeout(() => step(target), 160)
    }
    element.addEventListener('touchstart', onStart, { passive: true })
    element.addEventListener('touchmove', onMove, { passive: false })  // 下拉關閉時要擋住原生捲動
    element.addEventListener('touchend', onEnd, { passive: true })
    element.addEventListener('touchcancel', onEnd, { passive: true })
    return () => {
      element.removeEventListener('touchstart', onStart); element.removeEventListener('touchmove', onMove)
      element.removeEventListener('touchend', onEnd); element.removeEventListener('touchcancel', onEnd)
    }
  }, [mode, prev, next, onNavigate])

  useEffect(() => {
    if (warm) return
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
  // 先顯示卡片上已經載入、解碼好的那張圖（不必再要一張縮圖）；不是從卡片開的就用小縮圖，原圖等動畫結束再淡入
  const hint = coverHint?.current
  const firstSrc = hint && item.id && hint.id === item.id && hint.image_url === item.image_url ? hint.src : coverSrc(item, 240)  // 新增作品沒有編號，不能和「沒有 hint」比成相等
  const showsFull = Boolean(item.id && item.image_url) && firstSrc === new URL(coverSrc(item), window.location.href).href
  return <div className="showcase" ref={shell} role="dialog" aria-modal="true" aria-label={titleFor(item)} style={{ '--cover-colour': colour }}>
    <Backdrop item={item} onClose={onClose} />
    <button type="button" className="showcase-close" onClick={onClose} aria-label="關閉（Esc）">×</button>
    {prev && mode === 'view' && <button type="button" className="showcase-step is-prev" onClick={() => step(prev)} aria-label="上一件（←）">‹</button>}
    {next && mode === 'view' && <button type="button" className="showcase-step is-next" onClick={() => step(next)} aria-label="下一件（→）">›</button>}

    <div className="showcase-stage" key={item.id ?? 'new'} data-direction={direction > 0 ? 'next' : direction < 0 ? 'prev' : 'none'}>
      <figure className="showcase-cover" style={{ aspectRatio: `1 / ${ratio}` }}>
        {mode === 'edit' && preview && preview !== item.image_url ? <img src={preview} alt="" onError={hideBroken} /> : isFaceCover(item) ? <CoverFace item={item} width={480} /> : item.image_url ? <>
          <img src={firstSrc} alt="" onError={hideBroken} />
          {settled && !showsFull && <img className={`is-full${loadedSrc === coverSrc(item) ? ' is-loaded' : ''}`} src={coverSrc(item)} alt="" decoding="async" onLoad={event => setLoadedSrc(event.currentTarget.getAttribute('src'))} onError={hideBroken} />}
        </> : <span>OAS<small>尚未設定封面</small></span>}
      </figure>

      <section className="showcase-panel">
        {mode === 'view' ? <>
          <p className="showcase-facts">{item.kind && <span>{item.kind}</span>}<span>{item.source_name}</span><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}</span></p>
          <h1 className="showcase-title">{shortTitle(item)}</h1>
          {shortTitle(item) !== titleFor(item) && <FullTitle key={item.id} title={titleFor(item)} />}
          <div className="showcase-rate">
            <Stars value={item.rating} allowHalf onChange={rating => onQuick(item, { rating }).catch(() => {})} />
            <b>{item.rating > 0 ? Number(item.rating).toFixed(1) : '未評分'}</b>
            <Dropdown ariaLabel="閱讀狀態" value={item.status} options={statusOptions(item.status)} onChange={status => onQuick(item, { status }).catch(() => {})} />
          </div>
          {item.id && (item.status === '閱讀中' || item.progress) && <ProgressField key={`${item.id}-${item.progress}`} item={item} onQuick={onQuick} />}
          <div className="showcase-actions">
            {link ? <a className="primary" href={link} target="_blank" rel="noreferrer noopener" onClick={() => onVisit(item)}>前往閱讀 ↗</a> : <span className="primary is-disabled" title="此來源尚未設定網址規則">無來源連結</span>}
            <button type="button" className="ghost" onClick={() => setMode('edit')}>編輯 <kbd>E</kbd></button>
            {item.id && <CollectionMenu item={item} collections={collections} onMembership={onMembership} onCreate={onCreateCollection} />}
            {item.id && !item.link_dead && !altOpen && <button type="button" className="ghost subtle" onClick={() => setAltOpen(true)} title="作品在來源網站被下架、找不到時">來源失效？</button>}
          </div>
          {twins.map(entry => <p key={entry.id} className="fetch-note is-warn">可能和「{entry.title || entry.code}」（{entry.source_name}）重複<button type="button" onClick={() => onOpenDuplicate(entry.id)}>開啟它</button></p>)}
          {item.id && (item.link_dead || altOpen) && <AlternativesPanel item={item} reveal={altOpen} onQuick={onQuick} onRelinked={onRelinked} onOpenOwned={onOpenDuplicate} onClose={() => setAltOpen(false)} />}
          {item.tags.length > 0 && <div className="showcase-tag-groups">{groupTags(item.tags, categories).map(group => <div key={group.key} className="showcase-tags">
            <span className="tag-group-label">{group.label}</span>
            {group.names.map(tag => <button key={tag} type="button" onClick={() => onTag(tag)} title={`在書庫中查看 #${tag}`}>{group.key === 'tag' ? '#' : ''}{tag}</button>)}
          </div>)}</div>}
          <blockquote className={`showcase-note${item.note ? '' : ' is-empty'}`} onClick={() => !item.note && setMode('edit')}>{item.note || `還沒有筆記。${TOUCH ? '點這裡' : '按 E '}寫下只屬於你的閱讀記憶。`}</blockquote>
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
                <span>{shortTitle(entry)}</span>
                {entry.shared?.length > 0 && <small>{entry.shared.map(tag => `#${tag}`).join(' ')}</small>}
              </button>)}</div>}
          </section>
        </> : <EditForm item={item} link={link} onBulk={onBulk} onPreview={setPreview} onOpenDuplicate={onOpenDuplicate} dirtyRef={dirtyRef} onCancel={async () => { if (dirtyRef?.current && !await confirmLeave()) return; if (dirtyRef) dirtyRef.current = false; if (item.id) setMode('view'); else onClose() }} onSave={async payload => { if (await onSave(payload)) setMode('view') }} onRemove={onRemove} />}
      </section>
    </div>
    {mode === 'view' && <p className="showcase-keys" aria-hidden="true"><kbd>←</kbd><kbd>→</kbd> 切換　<kbd>1</kbd>–<kbd>5</kbd> 評分　<kbd>E</kbd> 編輯　<kbd>Esc</kbd> 關閉</p>}
  </div>
}

// 「收錄到 OAS」書籤：在來源網站頁面上執行，讀取標題、封面與標籤後開啟本機收錄表單。
// 只讀取使用者自己瀏覽器裡已顯示的頁面內容，不需要伺服器去連那個網站，所以有防護的網站也能用。
const BOOKMARKLET = `javascript:(()=>{const q=s=>[...document.querySelectorAll(s)],m=n=>(document.querySelector('meta[property="'+n+'"],meta[name="'+n+'"]')||{}).content||'',h=location.hostname;let t=m('og:title')||document.title,i=m('og:image'),g=[];if(/nhentai/.test(h)){const c=document.querySelector('#cover img');if(c){t=c.alt||t;i=c.src||c.dataset.src||i}g=q('#tags a[href^="/"] .name').map(e=>e.textContent)}else if(/18comic|jmcomic/.test(h)){t=(document.querySelector('#book-name')||document.querySelector('h1')||{}).textContent||t;i='';g=q('span[data-type="tags"] a,span[data-type="author"] a,span[data-type="actor"] a').map(e=>e.textContent)}else if(/wnacg/.test(h)){t=(document.querySelector('h2')||{}).textContent||t;g=q('.tagshow').map(e=>e.textContent)}else if(/missav/.test(h)){g=q('a[href*="/genres/"]').map(e=>e.textContent)}else if(/hanime1/.test(h)){g=q('.single-video-tag a').map(e=>e.childNodes[0].textContent)}else{g=m('keywords').split(/[,，]/)}const d={url:location.href,title:(t||'').trim(),image_url:i,tags:[...new Set(g.map(s=>s.trim()).filter(Boolean))].slice(0,60)};window.open('${window.location.origin}/?add='+encodeURIComponent(JSON.stringify(d)),'_blank')})()`

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

// 來源失效：標記失效，比對同一個編號在 N 網、紳士、禁漫有沒有作品，並用標題到 N 網、紳士搜尋；選中後改用新來源（舊連結會記在筆記裡）。
function AlternativesPanel({ item, reveal, onQuick, onRelinked, onOpenOwned, onClose }) {
  const panel = useRef(null)
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
  useEffect(() => { if (reveal) panel.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [reveal])  // 按了按鈕要看得到面板出現
  const relink = async row => {
    if (!await ask(`${row.unconfirmed ? '注意：這個候選只確認到封面，作品可能已下架或是章節編號，請先點標題到網站確認。\n\n' : ''}改用「${row.source_name}」的這一件？
${row.title}

原本的連結會記在筆記裡。`, { confirm: '改用這個' })) return
    try { onRelinked(await api(`/resources/${item.id}/relink`, { method: 'POST', body: JSON.stringify({ source_name: row.source_name, code: row.code }) })) }
    catch (failure) { setError(failure.message) }
  }
  return <section className="alt-panel" ref={panel}>
    <header>
      <div><b>{item.link_dead ? '此來源已標記為失效' : '來源失效？'}</b><small>比對同一個編號（N 網、紳士、禁漫），{item.title ? '並用標題搜尋 N 網、紳士，' : ''}選中後改用新來源</small></div>
      {item.link_dead
        ? <button type="button" className="ghost" onClick={() => onQuick(item, { link_dead: false }).catch(() => {})}>取消失效標記</button>
        : <><button type="button" className="ghost" onClick={() => onQuick(item, { link_dead: true }).catch(() => {})}>標記為失效</button><button type="button" className="alt-close" onClick={onClose} aria-label="收起">×</button></>}
    </header>
    <form className="alt-search" onSubmit={event => { event.preventDefault(); search(query) }}>
      <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋關鍵字" aria-label="搜尋關鍵字" />
      <button type="submit" className="ghost" disabled={busy}>{busy ? '搜尋中…' : '搜尋'}</button>
    </form>
    {busy && !data && <p className="alt-note">正在比對同編號、搜尋同名作品…</p>}
    {error && <p className="alt-note is-warn">{error}</p>}
    {data?.errors?.map(message => <p key={message} className="alt-note">{message}</p>)}
    {data && !busy && data.results.length === 0 && <p className="alt-note">{data.query ? '找不到同名作品，可以換個關鍵字（例如只留作品名，或改用日文、英文原名）。' : 'N 網、紳士、禁漫都沒有同一個編號。這件沒有標題，在上面輸入作品名、作者或社團再搜尋。'}</p>}
    {data && data.results.length > 0 && <div className="alt-grid">{data.results.map(row => <article key={`${row.source_name}-${row.code}`} className="alt-card">
      <i>{row.thumb && <img src={row.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" onError={hideBroken} />}</i>
      <div>
        <small>{row.source_name}{row.same_code ? `（同一個編號 ${row.code}${row.unconfirmed ? '，未確認' : ''}）` : ''}{row.pages ? `，${row.pages} 頁` : ''}</small>
        <a href={row.url} target="_blank" rel="noreferrer noopener" title="在來源網站開啟確認">{row.title}</a>
        {row.owned_id ? <button type="button" className="ghost" onClick={() => onOpenOwned(row.owned_id)}>已在典藏中，開啟</button> : <button type="button" className="primary" onClick={() => relink(row)}>改用這個</button>}
      </div>
    </article>)}</div>}
  </section>
}

function EditForm({ item, link, dirtyRef, onCancel, onSave, onRemove, onPreview, onOpenDuplicate, onBulk }) {
  const [form, setForm] = useState({ ...EMPTY, ...item, tags: (item.tags || []).join(', ') })
  const [url, setUrl] = useState(item.source_url || link || '')
  // 分享進來時已讀過作品頁：重複與「可能是另一個版本」（例如同一本在別的網站收過）直接顯示
  const [lookup, setLookup] = useState(item.duplicate ? { duplicate: item.duplicate } : item.similar?.length ? { similar: item.similar } : null)
  const [busy, setBusy] = useState(false)
  const bookmarklet = useRef(null)
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  useEffect(() => { onPreview(form.image_url) }, [form.image_url])
  // 和剛打開時比：有改過就算還沒儲存（App 關閉時會先問）
  const baseline = useRef(JSON.stringify([form, url]))
  useEffect(() => { if (dirtyRef) dirtyRef.current = JSON.stringify([form, url]) !== baseline.current }, [form, url])
  useEffect(() => () => { if (dirtyRef) dirtyRef.current = false }, [])
  // React 會擋下 javascript: 連結，所以直接寫入 DOM 屬性。
  useEffect(() => { bookmarklet.current?.setAttribute('href', BOOKMARKLET) }, [])

  const fetchInfo = async () => {
    if (!url.trim()) return
    setBusy(true)
    try {
      let info = await api('/metadata', { method: 'POST', body: JSON.stringify({ url: url.trim() }) })
      if (!info.title && canReadPages()) {  // 伺服器讀不到（禁漫、MissAV 等有防護）：手機 App 改用看不到的瀏覽器讀
        setLookup({ ...info, message: '伺服器讀不到，改用手機的瀏覽器讀取中…', blocked: false })
        const html = await readPage(info.url || url.trim())
        if (html) info = await api('/metadata', { method: 'POST', body: JSON.stringify({ url: url.trim(), html }) })
        if (!info.title) info = { ...info, message: info.message || '手機的瀏覽器也沒讀到（可能卡在網站的防護驗證），可以打開作品頁再分享一次' }
      }
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
  const submit = event => {
    event.preventDefault()
    // 讀作品頁得到的分類（作者、角色…）一起送出，存檔時標到還是一般標籤的那些上
    onSave({ ...form, rating: Number(form.rating), tags: form.tags.split(/[,，]/).map(value => value.trim()).filter(Boolean), tag_categories: { ...(form.tag_categories || {}), ...(lookup?.categories || {}) } })
  }
  const duplicate = lookup?.duplicate && lookup.duplicate.id !== item.id ? lookup.duplicate : null
  const formTags = form.tags.split(/[,，]/).map(value => value.trim()).filter(Boolean)
  // 帶入的新標籤可能就是現有標籤（英文、簡體、寫法不同）：只提示，按一下才換；規則要到「整理 → 標籤規則」建立
  const tagHints = (lookup?.tag_suggestions || []).filter(entry => formTags.includes(entry.source))
  const takeHint = entry => set('tags', [...new Set(formTags.map(name => name === entry.source ? entry.target : name))].join(', '))
  const canFill = duplicate && !item.id && (form.title.trim() || form.image_url.trim() || formTags.length)
  // 分享或書籤帶來的作品已在典藏中（例如一次收錄、只有編號的作品）：把這次的標題、封面補進那一件的空白欄位、標籤取聯集，不覆蓋已整理過的內容，再開啟它
  const fillDuplicate = async () => {
    setBusy(true)
    try {
      const target = await api(`/resources/${duplicate.id}`)
      const tags = [...new Set([...(target.tags || []), ...formTags])]
      const next = { ...target, tags }
      if (!target.title.trim() && form.title.trim()) next.title = form.title.trim()
      if (!target.image_url.trim() && form.image_url.trim()) next.image_url = form.image_url.trim()
      if (next.title !== target.title || next.image_url !== target.image_url || tags.length > (target.tags || []).length) {
        await api(`/resources/${duplicate.id}`, { method: 'PATCH', body: JSON.stringify(next) })
      }
      onOpenDuplicate(duplicate.id)
    } catch (error) { setLookup(current => ({ ...current, message: error.message })) } finally { setBusy(false) }
  }
  return <form className="showcase-form" onSubmit={submit}>
    <p className="overline">{item.id ? '編輯典藏' : '收錄新典藏'}</p>
    <div className="fetch-box">
      <label>作品網址<div className="fetch-row"><input name="source-url" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off" enterKeyHint="go" autoFocus={!item.id && !TOUCH} value={url} onChange={event => setUrl(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); fetchInfo() } }} placeholder="貼上作品頁網址，或直接輸入 N 網的作品編號（例如 123456）" /><button type="button" className="primary" onClick={fetchInfo} disabled={busy || !url.trim()}>{busy ? '讀取中…' : item.id ? '補齊資訊' : '抓取資訊'}</button></div></label>
      {duplicate && <p className="fetch-note is-warn">這件已在典藏中：「{duplicate.title}」<button type="button" onClick={() => onOpenDuplicate(duplicate.id)}>開啟它</button>{canFill && <button type="button" onClick={fillDuplicate} disabled={busy}>補上它的空白欄位</button>}</p>}
      {!duplicate && lookup?.similar?.map(entry => <p key={entry.id} className="fetch-note is-warn">可能是同一本的另一個版本：「{entry.title}」（{entry.source_name}）<button type="button" onClick={() => onOpenDuplicate(entry.id)}>開啟它</button></p>)}
      {!item.id && onBulk && <p className="fetch-note"><button type="button" onClick={onBulk}>一次貼多個網址</button>收錄後在背景慢慢補資料</p>}
      {lookup?.fetched && !duplicate && <p className="fetch-note is-ok">已帶入{lookup.title ? '標題、' : ''}{lookup.image_url ? '封面、' : ''}{lookup.tags?.length || 0} 個標籤（來源：{lookup.source_name}），請確認後儲存。</p>}
      {lookup?.fetched && !duplicate && (lookup.tag_changes?.length > 0 || lookup.tag_dropped?.length > 0) && <p className="fetch-note rule-note">依你的標籤規則：{lookup.tag_changes?.map(change => `${change.from} → ${change.to}`).join('、')}{lookup.tag_changes?.length && lookup.tag_dropped?.length ? '；' : ''}{lookup.tag_dropped?.length ? `略過封鎖的 ${lookup.tag_dropped.join('、')}` : ''}</p>}
      {lookup?.fetched && !duplicate && tagHints.length > 0 && <p className="fetch-note suggest-note">可能就是現有的標籤：{tagHints.map(entry => <span key={entry.source}>{entry.source} → {entry.target}<button type="button" onClick={() => takeHint(entry)} title={entry.reason}>換成「{entry.target}」</button></span>)}</p>}
      {lookup?.message && <p className={`fetch-note${lookup.blocked ? ' is-warn' : ''}`}>{lookup.message}</p>}
    </div>
    <label>作品名稱<input name="title" autoComplete="off" value={form.title} onChange={event => set('title', event.target.value)} /></label>
    <div className="form-split">
      <label>代碼或網址<input name="code" required autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off" value={form.code} onChange={event => set('code', event.target.value)} /></label>
      <label>來源<input name="source" autoComplete="off" value={form.source_name} onChange={event => set('source_name', event.target.value)} /></label>
    </div>
    <div className="field"><span>類型（留空則沿用來源的類型）</span><Dropdown ariaLabel="類型" value={form.kind || ''} onChange={value => set('kind', value)} options={[{ value: '', label: '沿用來源' }, ...KINDS.map(value => ({ value, label: value }))]} /></div>
    <label>封面網址<input name="cover-url" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off" value={form.image_url} onChange={event => set('image_url', event.target.value)} /></label>
    <label>標籤（以逗號分隔）<input name="tags" autoComplete="off" autoCapitalize="none" value={form.tags} onChange={event => set('tags', event.target.value)} /></label>
    <div className="form-split">
      <div className="field"><span>閱讀狀態</span><Dropdown ariaLabel="閱讀狀態" value={form.status} onChange={value => set('status', value)} options={statusOptions(form.status)} /></div>
      <div className="field"><span>評分 <b>{Number(form.rating).toFixed(1)}</b></span><Stars id="oas-rating" value={Number(form.rating)} allowHalf onChange={value => set('rating', value)} /></div>
    </div>
    <label>私人筆記<textarea value={form.note} onChange={event => set('note', event.target.value)} placeholder="只屬於你的閱讀記憶…" /></label>
    <div className="form-actions">
      <button type="submit" className="primary">儲存</button>
      <button type="button" className="ghost" onClick={onCancel}>取消</button>
      {item.id && <button type="button" className="remove" onClick={onRemove}>移出典藏</button>}
    </div>
    {!item.id && !TOUCH && <details className="bookmarklet-help">
      <summary>有防護的網站（禁漫、missav…）怎麼收錄？</summary>
      <p>把這顆按鈕拖曳到瀏覽器的書籤列：<a ref={bookmarklet} className="bookmarklet" onClick={event => event.preventDefault()} title="拖曳到書籤列">＋ 收錄到 OAS</a></p>
      <p>之後在任何作品頁點一下這個書籤，就會讀取該頁的標題、封面與標籤，並開啟這個收錄表單。只讀取你已經開著的頁面，典藏館需在執行中。</p>
    </details>}
  </form>
}

// 背景：封面縮圖縮小後模糊再放大（比整張大圖直接模糊省很多運算），新舊兩層交叉淡化，切換時不閃黑。
function Backdrop({ item, onClose }) {
  const [layers, setLayers] = useState(() => [{ key: item.id, src: item.image_url ? coverSrc(item, 240) : '' }])
  const [ready, setReady] = useState(false)
  useEffect(() => { const timer = setTimeout(() => setReady(true), 600); return () => clearTimeout(timer) }, [])
  useEffect(() => {
    setLayers(current => current[current.length - 1]?.key === item.id ? current : [...current.slice(-1), { key: item.id, src: item.image_url ? coverSrc(item, 240) : '' }])
  }, [item.id])
  return <div className="showcase-backdrop" onClick={onClose} aria-hidden="true">
    {ready && layers.map(layer => layer.src && <img key={layer.key} src={layer.src} alt="" onError={hideBroken} />)}
  </div>
}

// 主標題是短標題時，下方小字放完整原標題：平常最多兩行，點一下（或 Enter）展開；文字可以選取複製。
function FullTitle({ title }) {
  const [open, setOpen] = useState(false)
  const toggle = () => { if (!window.getSelection()?.toString()) setOpen(value => !value) }
  return <p className={`showcase-fulltitle${open ? ' is-open' : ''}`} role="button" tabIndex={0} aria-expanded={open} aria-label={`完整標題：${title}`} title={open ? undefined : '點一下展開完整標題'}
    onClick={toggle} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setOpen(value => !value) } }}>{title}</p>
}

// 閱讀進度：自由填寫（第幾話、第幾頁都可以），離開輸入框或按 Enter 就儲存；側欄「繼續閱讀」會顯示。沒填過時只留一個小按鈕，要記才展開。
function ProgressField({ item, onQuick }) {
  const [value, setValue] = useState(item.progress || '')
  const [editing, setEditing] = useState(false)
  const cancelled = useRef(false)  // Esc：接下來的 blur 不存檔（blur 當下拿到的還是打到一半的文字）
  const save = () => {
    if (cancelled.current) { cancelled.current = false; if (!item.progress) setEditing(false); return }
    if (value.trim() !== (item.progress || '')) onQuick(item, { progress: value.trim() }).catch(() => {})
    if (!value.trim()) setEditing(false)
  }
  if (!item.progress && !editing) return <p className="showcase-progress"><button type="button" onClick={() => setEditing(true)}>記進度</button></p>
  return <label className="showcase-progress"><span>看到</span>
    <input autoFocus={editing} value={value} maxLength={40} onChange={event => setValue(event.target.value)} onBlur={save} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { event.stopPropagation(); cancelled.current = true; setValue(item.progress || ''); event.currentTarget.blur() } }} placeholder="例如：第 3 話、p.45" aria-label="閱讀進度" />
  </label>
}
