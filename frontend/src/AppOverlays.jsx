import { useEffect, useState } from 'react'
import { api, ask, colourFor, CoverFace, coverSrc, Dropdown, errorNote, formatBytes, hideBroken, isFaceCover, KINDS, shortTitle, Stars, STATUS_OPTIONS, titleFor, useFocusTrap } from './shared.jsx'

const SHORTCUTS = [
  ['任何地方', [['/', '搜尋'], ['Ctrl K', '指令面板'], ['N', '收錄新典藏'], ['?', '顯示這份快捷鍵'], ['Home', '回到頂端'], ['Esc', '關閉視窗或離開批次選取']]],
  ['典藏卡片（懸停或用 Tab 聚焦時）', [['1–5', '評分，0 清除'], ['Enter', '開啟']]],
  ['展示頁', [['← →', '上一件、下一件'], ['1–5', '評分'], ['E', '編輯'], ['Esc', '關閉']]],
  ['批次選取', [['Shift ＋ 點擊', '一次選取一段範圍']]],
]

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// 批次編輯工具列：固定在畫面底部，對已選作品一次設定狀態、評分、類型或增刪標籤。
export function BatchBar({ count, onSelectAll, onClear, onRun, onDone, collections, onCollection }) {
  const [tagText, setTagText] = useState('')
  const [tagNames, setTagNames] = useState([])
  useEffect(() => { api('/tags').then(setTagNames).catch(() => {}) }, [])
  const tags = tagText.split(/[,，]/).map(value => value.trim()).filter(Boolean)
  const disabled = count === 0
  return <div className="batch-bar" role="toolbar" aria-label="批次編輯">
    <b>已選 {count} 件</b>
    <button type="button" className="ghost" onClick={onSelectAll}>全選已載入</button>
    {count > 0 && <button type="button" className="ghost" onClick={onClear}>取消選取</button>}
    <span className="batch-divider" />
    <Dropdown small ariaLabel="設定狀態" value="" onChange={value => value && onRun('status', { status: value })} options={[{ value: '', label: '設定狀態' }, ...STATUS_OPTIONS]} />
    <Dropdown small ariaLabel="設定評分" value="" onChange={value => value !== '' && onRun('rating', { rating: Number(value) })} options={[{ value: '', label: '設定評分' }, ...[5, 4.5, 4, 3.5, 3, 2.5, 2, 1.5, 1, 0.5, 0].map(value => ({ value: String(value), label: value ? `${'★'.repeat(Math.floor(value))}${value % 1 ? '½' : ''}　${value.toFixed(1)}` : '清除評分' }))]} />
    <Dropdown small ariaLabel="設定類型" value="" onChange={value => value && onRun('kind', { kind: value })} options={[{ value: '', label: '設定類型' }, ...KINDS.map(value => ({ value, label: value }))]} />
    {collections.length > 0 && <Dropdown small ariaLabel="加入書單" value="" onChange={value => value && !disabled && onCollection(Number(value), 'add')} options={[{ value: '', label: '加入書單' }, ...collections.map(collection => ({ value: String(collection.id), label: collection.name }))]} />}
    <span className="batch-divider" />
    <input className="batch-tags" list="oas-batch-tags" value={tagText} onChange={event => setTagText(event.target.value)} placeholder="標籤（逗號分隔）" disabled={disabled} />
    <datalist id="oas-batch-tags">{tagNames.map(name => <option key={name} value={name} />)}</datalist>
    <button type="button" className="ghost" disabled={disabled || !tags.length} onClick={() => { onRun('add_tags', { tags }); setTagText('') }}>＋ 加標籤</button>
    <button type="button" className="ghost" disabled={disabled || !tags.length} onClick={() => { onRun('remove_tags', { tags }); setTagText('') }}>－ 移除</button>
    <span className="batch-divider" />
    <button type="button" className="remove" disabled={disabled} onClick={() => onRun('delete')}>刪除</button>
    <button type="button" className="primary" onClick={onDone}>完成</button>
  </div>
}

export function BackupCenter({ close, notify, reload }) {
  const dialog = useFocusTrap()
  const [mode, setMode] = useState('merge')
  const [busy, setBusy] = useState(false)
  // 手機 App（個人版、公開版）：備份放在 App 私有空間，匯出才存到使用者選的資料夾，選過就記住；電腦版沒有 OASNative，照舊直接下載
  const appBackups = Boolean(window.OASNative?.privateBackups?.())
  const [privateFiles, setPrivateFiles] = useState([])
  const [folder, setFolder] = useState(() => window.OASNative?.backupFolderName?.() || '')
  useEffect(() => {
    if (!appBackups) return undefined
    api('/backup/files').then(setPrivateFiles).catch(() => {})
    window.__oasFolderChanged = () => setFolder(window.OASNative?.backupFolderName?.() || '')
    return () => { delete window.__oasFolderChanged }
  }, [appBackups])
  const restoreFromApp = async name => {
    if (mode === 'replace' && !await ask('「取代」會先清空目前所有典藏，再寫入備份內容。確定繼續？', { confirm: '清空並取代', danger: true })) return
    setBusy(true)
    try {
      const data = await api(`/backup/files/${encodeURIComponent(name)}`)
      const report = await api(`/restore?mode=${mode}`, { method: 'POST', body: JSON.stringify(data) })
      notify(`還原完成：新增 ${report.created} 筆，略過 ${report.skipped} 筆，新增來源 ${report.sources_created} 個`)
      reload(); close()
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const restore = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (mode === 'replace' && !await ask('「取代」會先清空目前所有典藏，再寫入備份內容。建議先匯出一份目前資料。確定繼續？', { confirm: '清空並取代', danger: true })) return
    setBusy(true)
    try {
      const data = JSON.parse(await file.text())
      const report = await api(`/restore?mode=${mode}`, { method: 'POST', body: JSON.stringify(data) })
      notify(`還原完成：新增 ${report.created} 筆，略過 ${report.skipped} 筆，新增來源 ${report.sources_created} 個`)
      reload(); close()
    } catch (error) { notify(error instanceof SyntaxError ? { text: '檔案不是有效的 JSON', error: true } : errorNote(error)) } finally { setBusy(false) }
  }
  return <div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label="備份中心" onClick={event => event.target === event.currentTarget && close()}>
    <section className="oas-modal-card">
      <button className="drawer-close" onClick={close} aria-label="關閉備份中心">×</button>
      <h2>備份中心</h2>
      <p className="drawer-subtitle">完整備份包含典藏、標籤、評分、狀態、筆記、時間戳與來源規則，格式為可讀的 JSON。</p>
      {appBackups
        ? <div className="backup-block"><h3>匯出</h3><p>備份平常存在 App 裡，解除安裝就會消失；要換手機或留底時，匯出到你選的資料夾。</p>
          {folder && <p className="maint-note">匯出位置：{folder}</p>}
          <div className="cover-actions"><a className="save" href="/api/backup" download>{folder ? '匯出完整備份' : '選資料夾並匯出'}</a>{folder && <button type="button" className="ghost" onClick={() => window.OASNative?.chooseBackupFolder?.()}>更換資料夾</button>}</div>
          <p className="maint-note">匯出的檔案沒有加密。選「下載」這類公用資料夾時，有檔案權限的其他 App 讀得到。</p></div>
        : <div className="backup-block"><h3>匯出</h3><p>下載目前所有資料的快照。建議在大量編輯或還原前先匯出一次。</p><a className="save" href="/api/backup" download>下載完整備份</a></div>}
      <div className="backup-block"><h3>還原</h3><p>選擇先前匯出的 <code>oas-backup-*.json</code>。</p>
        <div className="mode-switch">{[['merge', '合併', '保留現有資料，只加入缺少的典藏'], ['replace', '取代', '清空後完整還原至備份狀態']].map(([value, label, hint]) => <label key={value} className={mode === value ? 'is-active' : ''}><input type="radio" name="restore-mode" value={value} checked={mode === value} onChange={() => setMode(value)} /><b>{label}</b><small>{hint}</small></label>)}</div>
        <label className={`save file-pick${busy ? ' is-busy' : ''}`}>{busy ? '還原中…' : '選擇備份檔'}<input type="file" accept="application/json,.json" disabled={busy} onChange={restore} /></label>
        {privateFiles.length > 0 && <><p className="pref-heading"><b>App 裡的備份</b></p><ul className="private-backups">{privateFiles.map(file => <li key={file.name}><span>{new Date(file.at).toLocaleString('zh-TW', { hour12: false })}<small>{file.name.startsWith('oas-auto-') ? '自動' : '手動'}・{formatBytes(file.size)}</small></span><button type="button" className="ghost" disabled={busy} onClick={() => restoreFromApp(file.name)}>還原</button></li>)}</ul></>}
      </div>
      <p className="backup-moved">封面快取、補齊作品資訊與巡檢來源在「整理 → 維護」；缺標題、封面或來源失效的作品在「整理 → 待整理」。</p>
    </section>
  </div>
}

// 回到頂端：捲動超過一個半畫面才出現；批次工具列開著時往上避開。
export function BackToTop({ raised }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > window.innerHeight * 1.5)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return <button type="button" className={`back-to-top${shown ? ' is-shown' : ''}${raised ? ' is-raised' : ''}`} aria-label="回到頂端" title="回到頂端（Home）" tabIndex={shown ? 0 : -1} aria-hidden={!shown}
    onClick={event => {
      window.scrollTo({ top: 0, behavior: reducedMotion() ? 'auto' : 'smooth' })
      // 只有用鍵盤按（detail 為 0）才把焦點交給搜尋框；手指或滑鼠點不搶焦點，手機才不會跳出鍵盤、像是跳到搜尋
      if (event.detail === 0) document.querySelector('#oas-search')?.focus({ preventScroll: true })
      else event.currentTarget.blur()
    }}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></svg>
  </button>
}

export function ShortcutHelp({ close }) {
  const dialog = useFocusTrap()
  return <div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label="鍵盤快捷鍵" onClick={event => event.target === event.currentTarget && close()}>
    <section className="oas-modal-card shortcut-card">
      <button className="drawer-close" onClick={close} aria-label="關閉">×</button>
      <h2>鍵盤快捷鍵</h2>
      <p className="drawer-subtitle">在輸入框裡打字時，這些按鍵不會觸發。</p>
      {SHORTCUTS.map(([group, rows]) => <div key={group} className="shortcut-group"><h3>{group}</h3>
        <dl>{rows.map(([key, label]) => <div key={key + label}><dt>{key.split(' ').map(part => part === '＋' ? <span key={part}> ＋ </span> : <kbd key={part}>{part}</kbd>)}</dt><dd>{label}</dd></div>)}</dl>
      </div>)}
    </section>
  </div>
}

// 從外部網站回來：一鍵評分、標成已完食或閱讀中、記下看到哪；不擋畫面，只是開來看看就直接關掉，狀態不會變。
export function ReturnPrompt({ item, onQuick, onOpen, onClose }) {
  const [editing, setEditing] = useState(false)
  const [progress, setProgress] = useState(item.progress || '')
  const done = item.status === '已完食'
  const reading = item.status === '閱讀中'
  // Esc 由 App 統一處理（先關其他視窗，最後才收起這張卡片）
  const saveProgress = event => { event.preventDefault(); onQuick(item, { progress: progress.trim() }).catch(() => {}); setEditing(false) }
  return <aside className="return-prompt" role="dialog" aria-label="剛剛在看的作品" style={{ '--cover-colour': colourFor(item) }}>
    <button type="button" className="return-cover" onClick={onOpen} title="開啟展示頁">{isFaceCover(item) ? <CoverFace item={item} width={240} /> : <img src={coverSrc(item, 240)} alt="" onError={hideBroken} />}</button>
    <div className="return-body">
      <p className="return-kicker">{done ? '已標成已完食' : reading ? '剛剛在看' : '剛剛開啟'}{item.progress ? `，看到 ${item.progress}` : ''}</p>
      <h3 title={titleFor(item)}>{shortTitle(item)}</h3>
      <div className="return-rate"><Stars value={item.rating} allowHalf onChange={rating => onQuick(item, { rating }).catch(() => {})} /><span>{item.rating > 0 ? Number(item.rating).toFixed(1) : '還沒評分'}</span></div>
      {editing ? <form className="return-progress" onSubmit={saveProgress}><input autoFocus value={progress} maxLength={40} onChange={event => setProgress(event.target.value)} placeholder="例如：第 3 話、p.45" aria-label="閱讀進度" /><button type="submit" className="primary">記下</button></form>
        : <div className="return-actions">
          {!done && <button type="button" className="primary" onClick={() => onQuick(item, { status: '已完食' }).catch(() => {})}>看完了</button>}
          {reading && <button type="button" className="ghost" onClick={() => setEditing(true)}>還沒看完，記進度</button>}
          {!done && !reading && <button type="button" className="ghost" onClick={() => onQuick(item, { status: '閱讀中' }).catch(() => {})}>還在看</button>}
          {done && <button type="button" className="ghost" onClick={onClose}>完成</button>}
        </div>}
    </div>
    <button type="button" className="return-close" onClick={onClose} aria-label="之後再說">×</button>
  </aside>
}
