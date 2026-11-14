// 公開版「整理 → 維護」的設定：封面、背景讀取、最近使用畫面、自己的翻譯包、第三方授權。電腦只有封面與授權（背景讀取、翻譯包匯入是手機才有）
import { useState } from 'react'
import { api, ask, errorNote } from './shared.jsx'
import { Choice, coverChoices, OLD_COVER_CHOICES, READER_CHOICES } from './FirstRun.jsx'
import { clearReads } from './pageReader.js'
import { asks, clearStoredCovers, isDesktop, preferences, savePreferences, storedCoverCount } from './preferences.js'
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
  // 電腦改成「不存」而且已經存了封面：在這裡問要不要刪（count 是張數），兩個都不預選
  const [oldCovers, setOldCovers] = useState(null)
  const desktop = isDesktop()
  const changeCovers = async keep => {
    if (desktop) return changeDesktopCovers(keep)
    if (!keep && !await ask('要刪除已經存在手機上的封面嗎？之後封面會每次向來源網站載入，沒有網路或來源移除圖片時就看不到。', { confirm: '刪除並停止儲存', danger: true })) return
    setBusy(true)
    try {
      setPrefs(await savePreferences({ save_covers: keep }))
      if (!keep) notify(`已刪除 ${await clearStoredCovers()} 個封面檔`)
      onCoversChanged(keep)
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const changeDesktopCovers = async keep => {
    setBusy(true)
    try {
      const count = keep ? 0 : await storedCoverCount()
      if (count > 0) { setOldCovers({ count, remove: null }); return }
      setPrefs(await savePreferences({ save_covers: keep }))
      onCoversChanged(keep)
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const stopSavingCovers = async remove => {
    setBusy(true)
    try {
      setPrefs(await savePreferences({ save_covers: false }))
      setOldCovers(null)
      onCoversChanged(false)
      // 刪掉的檔案數含縮圖，和上面問的張數對不上，所以不寫數字
      if (remove) await clearStoredCovers()
      notify(remove ? '已刪除這台電腦上的封面，之後不再儲存' : '已停止儲存封面，已存的封面保留')
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const changeReader = async on => {
    try {
      setPrefs(await savePreferences({ background_reader: on }))
      if (!on) {
        clearReads()  // 排隊的網址不留在手機上
        native()?.clearReaderCookies?.()
      }
    } catch (error) { notify(errorNote(error)) }
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
  return <div className="backup-block"><h3>{desktop ? '設定' : 'App 設定'}</h3>
    <Switch title={desktop ? '把封面存到這台電腦' : '把封面存到手機'} hint={coverChoices()[prefs.save_covers ? 0 : 1][2]} checked={prefs.save_covers === true} disabled={busy || oldCovers !== null} onChange={changeCovers} />
    {oldCovers && <div className="pref-followup">
      <Choice name="settings-old-covers" title={`這台電腦上已經存了 ${oldCovers.count} 張封面，要刪掉嗎？`} options={OLD_COVER_CHOICES} value={oldCovers.remove} onChange={remove => setOldCovers({ ...oldCovers, remove })} />
      <div className="cover-actions">
        <button type="button" className={oldCovers.remove ? 'ghost danger is-strong-danger' : 'save'} disabled={busy || oldCovers.remove === null} onClick={() => stopSavingCovers(oldCovers.remove)}>{busy ? '處理中…' : oldCovers.remove ? '刪除並停止儲存' : '停止儲存封面'}</button>
        <button type="button" className="ghost" disabled={busy} onClick={() => setOldCovers(null)}>取消</button>
      </div>
    </div>}
    {asks('background_reader') && <Switch title="在背景讀取作品頁" hint={READER_CHOICES[prefs.background_reader ? 0 : 1][2]} checked={prefs.background_reader === true} onChange={changeReader} />}
    {native()?.setHideRecents && <Switch title="在最近使用畫面隱藏內容" hint="切換 App 時，別人看不到你的書庫畫面。" checked={hideRecents} onChange={changeRecents} />}
    {!desktop && <>
      <p className="pref-heading"><b>我的翻譯包</b><small>匯入自己的標籤翻譯，格式為 JSON（translate、suggest、block）。會取代上次匯入的內容，更新版本不會動到它。</small></p>
      <div className="cover-actions"><label className={`save file-pick${busy ? ' is-busy' : ''}`}>匯入翻譯包<input type="file" accept="application/json,.json" disabled={busy} onChange={importPack} /></label></div>
    </>}
    <p className="maint-note">僅供已滿 18 歲的人使用。嚴禁用於收錄或管理任何涉及未滿 18 歲者的性相關內容。</p>
    {desktop
      ? <p className="maint-note">第三方軟體的授權見 OAS 資料夾裡的 THIRD_PARTY_NOTICES.md。</p>
      : <div className="cover-actions"><button type="button" className="ghost" onClick={() => setNotices(true)}>第三方授權聲明</button></div>}
    {notices && <ThirdPartyNotices close={() => setNotices(false)} />}
  </div>
}
