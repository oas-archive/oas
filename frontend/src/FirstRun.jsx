// 公開版第一次啟動：年齡與用途限制，以及封面、背景讀取兩個選擇（電腦沒有背景讀取）。沒有預設值，要自己選過才能開始
import { useEffect, useState } from 'react'
import { errorNote } from './shared.jsx'
import { asks, clearStoredCovers, isDesktop, savePreferences, storedCoverCount } from './preferences.js'

export const COVER_CHOICES = [
  [true, '存到手機', '看得快，來源移除圖片後仍看得到。封面圖片會留在這支手機上。'],
  [false, '不存', '手機只存網址，封面每次向來源網站載入。沒有網路或來源移除圖片時就看不到。'],
]
const DESKTOP_COVER_CHOICES = [
  [true, '存到這台電腦', '看得快，來源移除圖片後仍看得到。封面圖片會留在這台電腦上。'],
  [false, '不存', '只存網址，封面每次向來源網站載入。沒有網路或來源移除圖片時就看不到。'],
]
export const coverChoices = () => isDesktop() ? DESKTOP_COVER_CHOICES : COVER_CHOICES
// 電腦改成「不存」時，已經存在這台電腦上的封面要不要刪：兩個都不預選
export const OLD_COVER_CHOICES = [
  [true, '刪除', '刪掉已存的封面。來源已經移除的圖片就看不到了。'],
  [false, '保留', '已存的封面留著，之後不再新增。'],
]
export const READER_CHOICES = [
  [true, '開啟', '收錄或補資料時，App 會在你看不到的瀏覽器裡打開作品頁（一次一頁、不載圖片），取得標題與標籤。網站看到的是你手機的連線；網頁自己的程式會執行，廣告或統計服務可能一起連線，也可能留下 cookie（關閉這個選項時會清掉）。不需要操作的自動驗證會照常通過；要你操作的（勾選、驗證碼、登入）不會處理，那一件通常就讀不到。'],
  [false, '關閉', '只用一般連線讀取，不開看不到的瀏覽器。有防護的網站可能讀不到標籤，要自己填。'],
]

export function Choice({ name, title, options, value, onChange }) {
  return <fieldset className="first-run-question"><legend>{title}</legend>
    <div className="mode-switch">{options.map(([option, label, hint]) => <label key={label} className={value === option ? 'is-active' : ''}>
      <input type="radio" name={name} checked={value === option} onChange={() => onChange(option)} /><b>{label}</b><small>{hint}</small>
    </label>)}</div>
  </fieldset>
}

export default function FirstRun({ onDone }) {
  const desktop = isDesktop()
  const askCovers = asks('save_covers')
  const askReader = asks('background_reader')
  const [adult, setAdult] = useState(false)
  const [saveCovers, setSaveCovers] = useState(null)
  const [reader, setReader] = useState(null)
  // 電腦選「不存」時才查已存的封面；有的話多問一題要不要刪
  const [oldCovers, setOldCovers] = useState(null)
  const [removeOld, setRemoveOld] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const askOld = desktop && saveCovers === false
  useEffect(() => {
    if (!askOld || oldCovers !== null) return
    storedCoverCount().then(setOldCovers, () => setOldCovers(0))
  }, [askOld])
  const oldAnswered = !askOld || (oldCovers !== null && (oldCovers === 0 || removeOld !== null))
  const ready = adult && (!askCovers || saveCovers !== null) && (!askReader || reader !== null) && oldAnswered
  const start = async () => {
    setBusy(true)
    setError('')
    try {
      const saved = await savePreferences({ adult_confirmed: true, ...(askCovers && { save_covers: saveCovers }), ...(askReader && { background_reader: reader }) })
      if (askOld && oldCovers > 0 && removeOld) await clearStoredCovers()
      onDone(saved)
    } catch (failure) {
      setError(errorNote(failure).text || String(failure))
      setBusy(false)
    }
  }
  return <main className="first-run">
    <h1>開始之前</h1>
    <section className="first-run-question">
      <h2>年齡與用途</h2>
      <p>OAS 可以用來管理成人向作品的連結，僅供已滿 18 歲的人使用。</p>
      <p className="first-run-rule">嚴禁用於收錄或管理任何涉及未滿 18 歲者的性相關內容。</p>
      <label className="pref-row"><input type="checkbox" checked={adult} onChange={event => setAdult(event.target.checked)} /><span>我已滿 18 歲，也同意上面的限制</span></label>
    </section>
    {askCovers && <Choice name="first-run-covers" title={desktop ? '要把封面存到這台電腦嗎？' : '要把封面存到手機嗎？'} options={coverChoices()} value={saveCovers} onChange={setSaveCovers} />}
    {askOld && oldCovers > 0 && <Choice name="first-run-old-covers" title={`這台電腦上已經存了 ${oldCovers} 張封面，要刪掉嗎？`} options={OLD_COVER_CHOICES} value={removeOld} onChange={setRemoveOld} />}
    {askReader && <Choice name="first-run-reader" title="要在背景讀取作品頁嗎？" options={READER_CHOICES} value={reader} onChange={setReader} />}
    {desktop
      ? <p className="maint-note">之後都可以在「整理 → 維護」更改。你的資料只存在這台電腦的資料夾裡（預設是 %LOCALAPPDATA%\OAS），OAS 不會傳給開發者；讀取作品資料、封面與檢查連結時會連到那些網站。</p>
      : <p className="maint-note">之後都可以在「整理 → 維護」更改。你的資料只存在這支手機上，OAS 不會傳給開發者；讀取作品資料、封面與檢查連結時會連到那些網站。解除安裝會刪除所有資料，要留著請先匯出備份。</p>}
    {error && <p className="first-run-error" role="alert">{error}</p>}
    <div className="cover-actions">
      <button type="button" className="save" disabled={!ready || busy} onClick={start}>{busy ? '儲存中…' : '開始使用'}</button>
      {window.OASNative?.exitApp && <button type="button" className="ghost" onClick={() => window.OASNative.exitApp()}>離開</button>}
    </div>
  </main>
}
