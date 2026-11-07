// 「整理 → 維護」：背景維護工作（封面快取、補齊作品資訊、巡檢來源），原本放在備份中心
import { useEffect, useRef, useState } from 'react'
import { api, formatBytes, errorNote } from './shared.jsx'
import { backupText, jobText } from './Sidebar.jsx'
import PublicSettings from './PublicSettings.jsx'
import { isPublic, preferences } from './preferences.js'

export default function Maintenance({ notify, reload, onBackup }) {
  const [savingCovers, setSavingCovers] = useState(() => preferences().save_covers === true)
  return <div className="insight-body">
    <header className="insight-head"><div><h2>維護</h2><p>這些工作會在背景自動進行，平常不用動；補不到的作品會列在「待整理」。</p></div></header>
    <div className="maintenance-blocks">
      {isPublic() && <PublicSettings notify={notify} onCoversChanged={setSavingCovers} />}
      <BackupBlock onBackup={onBackup} />
      {savingCovers && <CoverCacheBlock notify={notify} />}
      <RefreshBlock notify={notify} />
      <LinkCheckBlock notify={notify} reload={reload} />
    </div>
  </div>
}

// 備份：上次備份的時間與備份中心的入口（手機沒有側欄，放在這裡）
function BackupBlock({ onBackup }) {
  const [status, setStatus] = useState(null)
  useEffect(() => {
    const refresh = () => api('/system/status').then(setStatus).catch(() => {})
    refresh()
    const timer = setInterval(refresh, 30000)
    return () => clearInterval(timer)
  }, [])
  const job = jobText(status)
  return <div className="backup-block"><h3>備份</h3><p className={status && !status.backup ? 'backup-warn' : undefined} title={status?.backup?.name}>{backupText(status)}</p>
    {job && <p>{job}</p>}
    <div className="cover-actions"><button type="button" className="save" onClick={onBackup}>開啟備份中心</button></div>
  </div>
}

// 封面快取：把封面存在本機，來源移除圖片後仍可顯示
function CoverCacheBlock({ notify }) {
  const [coverStats, setCoverStats] = useState(null)
  const refreshCovers = () => api('/covers/status').then(setCoverStats).catch(() => {})
  // 進行中才密集更新；閒置時慢慢查，開著這一頁不會一直掃全部封面
  const coverRunning = Boolean(coverStats?.batch?.running)
  useEffect(() => {
    refreshCovers()
    const timer = setInterval(refreshCovers, coverRunning ? 2000 : 15000)
    return () => clearInterval(timer)
  }, [coverRunning])
  const startCovers = async retryFailed => {
    try {
      const result = await api(`/covers/cache${retryFailed ? '?retry_failed=true' : ''}`, { method: 'POST' })
      notify(result.queued ? `已排入 ${result.queued} 張封面，於背景下載` : '沒有需要下載的封面')
      refreshCovers()
    } catch (error) { notify(errorNote(error)) }
  }
  const batch = coverStats?.batch
  return <div className="backup-block"><h3>封面快取</h3><p>封面存一份在本機，來源刪掉圖片後仍看得到；瀏覽時也會自動存。</p>
    {coverStats ? <>
      <div className="cover-meter" role="progressbar" aria-label="封面快取進度" aria-valuenow={coverStats.with_image ? Math.round((coverStats.cached / coverStats.with_image) * 100) : 0}><span style={{ width: `${coverStats.with_image ? (coverStats.cached / coverStats.with_image) * 100 : 0}%` }} /></div>
      <p className="cover-numbers">已存 <b>{coverStats.cached}</b> / {coverStats.with_image} 張・{formatBytes(coverStats.bytes)}{coverStats.pending ? `・還沒存 ${coverStats.pending}` : ''}{coverStats.failed ? `・下載失敗 ${coverStats.failed}` : ''}{batch?.running ? `。背景進行中 ${batch.done} / ${batch.queued}` : ''}</p>
      {coverStats.failed > 0 && <p className="maint-note">下載失敗的封面多半是來源網址已經失效，作品會列在「待整理」（缺封面）；被網站擋下的仍會由瀏覽器直接顯示。</p>}
      <div className="cover-actions"><button type="button" className="save" disabled={batch?.running} onClick={() => startCovers(false)}>{batch?.running ? '快取中…' : '快取全部封面'}</button>{coverStats.failed > 0 && !batch?.running && <button type="button" className="ghost" onClick={() => startCovers(true)}>重試失敗項目</button>}</div>
    </> : <p>讀取中…</p>}
  </div>
}

function RefreshBlock({ notify }) {
  const [status, setStatus] = useState(null)
  const refreshStatus = () => api('/refresh/status').then(setStatus).catch(() => {})
  useEffect(() => { refreshStatus(); const timer = setInterval(refreshStatus, status?.running ? 2000 : 15000); return () => clearInterval(timer) }, [Boolean(status?.running)])
  const start = async mode => {
    try { const result = await api(`/refresh?mode=${mode}`, { method: 'POST' }); notify(`已排入 ${result.queued} 件，約 ${Math.ceil(result.seconds / 60)} 分鐘`); refreshStatus() } catch (error) { notify(errorNote(error)) }
  }
  const running = status?.running
  return <div className="backup-block"><h3>補齊作品資訊</h3>
    <p>重新讀 nhentai、紳士、Hanime1 的作品頁，只新增、不覆蓋。「只補缺少的」處理沒封面或標籤的作品；「全部重新整理」每件重讀，並替標籤分類。</p>
    {status && (running || status.finished_at) && <>
      <div className="cover-meter" role="progressbar" aria-label="補資料進度" aria-valuenow={status.total ? Math.round((status.done / status.total) * 100) : 0}><span style={{ width: `${status.total ? (status.done / status.total) * 100 : 0}%` }} /></div>
      <p className="cover-numbers">{running ? '進行中' : '已完成'} <b>{status.done}</b> / {status.total}，新增標籤 {status.added_tags}、補上封面 {status.added_covers}、分類標籤 {status.categorised}{status.failed ? `、失敗 ${status.failed}` : ''}{status.message ? `。${status.message}` : ''}</p>
    </>}
    <div className="cover-actions">
      {running ? <button type="button" className="ghost" onClick={() => api('/refresh/stop', { method: 'POST' }).then(refreshStatus)}>停止</button> : <>
        <button type="button" className="save" onClick={() => start('missing')}>只補缺少的</button>
        <button type="button" className="ghost" onClick={() => start('all')}>全部重新整理</button>
      </>}
    </div>
  </div>
}

function LinkCheckBlock({ notify, reload }) {
  const [status, setStatus] = useState(null)
  const refreshStatus = () => api('/linkcheck/status').then(setStatus).catch(() => {})
  useEffect(() => { refreshStatus(); const timer = setInterval(refreshStatus, status?.running ? 3000 : 15000); return () => clearInterval(timer) }, [Boolean(status?.running)])
  const wasRunning = useRef(false)
  useEffect(() => { if (wasRunning.current && status && !status.running && status.dead) reload(); wasRunning.current = Boolean(status?.running) }, [status?.running])
  const start = () => api('/linkcheck/start', { method: 'POST' }).then(() => { notify('開始巡檢來源，於背景進行'); refreshStatus() }).catch(error => notify(errorNote(error)))
  const running = status?.running
  return <div className="backup-block"><h3>定期巡檢來源</h3>
    <p>每 {status?.interval_days ?? 7} 天檢查一次來源還在不在，找不到就標成來源失效。禁漫、MissAV 和網站頁面不檢查。</p>
    {running && status.total > 0 && <div className="cover-meter" role="progressbar" aria-label="巡檢進度" aria-valuenow={Math.round((status.done / status.total) * 100)}><span style={{ width: `${(status.done / status.total) * 100}%` }} /></div>}
    {status && <p className="cover-numbers">{running ? (status.total ? <>進行中 <b>{status.done}</b> / {status.total}，發現失效 {status.dead}</> : '等待開始…') : status.finished_at ? <>上次巡檢檢查 {status.done} 件，發現失效 {status.dead}</> : '這次啟動尚未巡檢'}{status.message ? `。${status.message}` : ''}。待檢查 {status.pending} 件。</p>}
    <div className="cover-actions">{running ? <button type="button" className="ghost" onClick={() => api('/linkcheck/stop', { method: 'POST' }).then(refreshStatus)}>停止</button> : <button type="button" className="ghost" disabled={!status?.pending} onClick={start}>立即巡檢</button>}</div>
  </div>
}
