// 公開版「整理 → 維護」的設定：封面、背景讀取、最近使用畫面、自己的翻譯包、第三方授權
import { useState } from 'react'
import { api, ask, errorNote } from './shared.jsx'
import { COVER_CHOICES, READER_CHOICES } from './FirstRun.jsx'
import { preferences, savePreferences } from './preferences.js'
import ThirdPartyNotices from './ThirdPartyNotices.jsx'

const native = () => window.OASNative

function Switch({ title, hint, checked, disabled, onChange }) {
  return <label className="pref-row"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} /><span><b>{title}</b><small>{hint}</small></span></label>
}

export default function PublicSettings({ notify, onCoversChanged }) {
  const [prefs, setPrefs] = useState(preferences)
  const [hideRecents, setHideRecents] = useState(() => native()?.hidesRecents?.() ?? true)
  const [busy, setBusy] = useState(false)
  const [notices, setNotices] = useState(false)
  const changeCovers = async keep => {
    if (!keep && !await ask('要刪除已經存在手機上的封面嗎？之後封面會每次向來源網站載入，沒有網路或來源移除圖片時就看不到。', { confirm: '刪除並停止儲存', danger: true })) return
    setBusy(true)
    try {
      setPrefs(await savePreferences({ save_covers: keep }))
      if (!keep) {
        const { removed } = await api('/covers/clear', { method: 'POST' })
        notify(`已刪除 ${removed} 個封面檔`)
      }
      onCoversChanged(keep)
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const changeReader = async on => {
    try { setPrefs(await savePreferences({ background_reader: on })) } catch (error) { notify(errorNote(error)) }
  }
  const changeRecents = hide => { native()?.setHideRecents?.(hide); setHideRecents(hide) }
  const importPack = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    try {
      const pack = JSON.parse((await file.text()).replace(/^﻿/, ''))
      const result = await api('/tag-pack/mine', { method: 'POST', body: JSON.stringify({ translate: pack.translate || {}, suggest: pack.suggest || {}, block: pack.block || [] }) })
      notify(`已匯入：翻譯 ${result.translate} 條、建議 ${result.suggest} 條、封鎖 ${result.block} 條`)
    } catch (error) { notify(error instanceof SyntaxError ? { text: '檔案不是有效的 JSON', error: true } : errorNote(error)) } finally { setBusy(false) }
  }
  return <div className="backup-block"><h3>App 設定</h3>
    <Switch title="把封面存到手機" hint={COVER_CHOICES[prefs.save_covers ? 0 : 1][2]} checked={prefs.save_covers === true} disabled={busy} onChange={changeCovers} />
    <Switch title="在背景讀取作品頁" hint={READER_CHOICES[prefs.background_reader ? 0 : 1][2]} checked={prefs.background_reader === true} onChange={changeReader} />
    {native()?.setHideRecents && <Switch title="在最近使用畫面隱藏內容" hint="切換 App 時，別人看不到你的書庫畫面。" checked={hideRecents} onChange={changeRecents} />}
    <p className="pref-heading"><b>我的翻譯包</b><small>匯入自己的標籤翻譯，格式為 JSON（translate、suggest、block）。會取代上次匯入的內容，更新版本不會動到它。</small></p>
    <div className="cover-actions"><label className={`save file-pick${busy ? ' is-busy' : ''}`}>匯入翻譯包<input type="file" accept="application/json,.json" disabled={busy} onChange={importPack} /></label></div>
    <p className="maint-note">僅供已滿 18 歲的人使用。嚴禁用於收錄或管理任何涉及未滿 18 歲者的性相關內容。</p>
    <div className="cover-actions"><button type="button" className="ghost" onClick={() => setNotices(true)}>第三方授權聲明</button></div>
    {notices && <ThirdPartyNotices close={() => setNotices(false)} />}
  </div>
}
