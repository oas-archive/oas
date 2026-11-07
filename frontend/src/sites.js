// 網頁認得的網站（後端的對應資料在 backend/app/sites.py）。畫面文字提到的網站名稱另見 docs/架構.md。
// 伺服器讀不到、要靠手機瀏覽器讀的網站（與後端 sites.PROTECTED_HOSTS 相同）
export const PROTECTED = /18comic|jmcomic|missav/i
export const JM_SOURCE = '禁漫天堂'
export const JM_SEARCH = 'https://18comic.vip/search/photos?search_query='
