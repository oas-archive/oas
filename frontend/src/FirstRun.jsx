// 公開版第一次啟動：年齡與用途限制，以及封面、背景讀取兩個選擇。沒有預設值，要自己選過才能開始
import { useState } from 'react'
import { errorNote } from './shared.jsx'
import { savePreferences } from './preferences.js'

export const COVER_CHOICES = [
  [true, '存到手機', '看得快，來源移除圖片後仍看得到。封面圖片會留在這支手機上。'],
  [false, '不存', '手機只存網址，封面每次向來源網站載入。沒有網路或來源移除圖片時就看不到。'],
]
export const READER_CHOICES = [
  [true, '開啟', '收錄時會在你看不到的瀏覽器裡打開作品頁，取得標題與標籤；網站的驗證若不需要操作，會自動通過。'],
  [false, '關閉', '只用一般連線讀取。有防護的網站可能讀不到標籤，要自己填。'],
]

function Choice({ name, title, options, value, onChange }) {
  return <fieldset className="first-run-question"><legend>{title}</legend>
    <div className="mode-switch">{options.map(([option, label, hint]) => <label key={label} className={value === option ? 'is-active' : ''}>
      <input type="radio" name={name} checked={value === option} onChange={() => onChange(option)} /><b>{label}</b><small>{hint}</small>
    </label>)}</div>
  </fieldset>
}

export default function FirstRun({ onDone }) {
  const [adult, setAdult] = useState(false)
  const [saveCovers, setSaveCovers] = useState(null)
  const [reader, setReader] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const ready = adult && saveCovers !== null && reader !== null
  const start = async () => {
    setBusy(true)
    setError('')
    try {
      onDone(await savePreferences({ adult_confirmed: true, save_covers: saveCovers, background_reader: reader }))
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
    <Choice name="first-run-covers" title="要把封面存到手機嗎？" options={COVER_CHOICES} value={saveCovers} onChange={setSaveCovers} />
    <Choice name="first-run-reader" title="要在背景讀取作品頁嗎？" options={READER_CHOICES} value={reader} onChange={setReader} />
    <p className="maint-note">之後都可以在「整理 → 維護」更改。你的資料只存在這支手機上，OAS 不會傳給開發者。</p>
    {error && <p className="first-run-error" role="alert">{error}</p>}
    <div className="cover-actions">
      <button type="button" className="save" disabled={!ready || busy} onClick={start}>{busy ? '儲存中…' : '開始使用'}</button>
      {window.OASNative?.exitApp && <button type="button" className="ghost" onClick={() => window.OASNative.exitApp()}>離開</button>}
    </div>
  </main>
}
