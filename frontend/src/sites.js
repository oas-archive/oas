// 網頁認得的網站（後端的對應資料在 backend/app/sites.py）。
// 伺服器讀不到、要靠手機瀏覽器讀的網站（與後端 sites.PROTECTED_HOSTS 相同）
export const PROTECTED = /18comic|jmcomic|missav/i
export const JM_SOURCE = '禁漫天堂'
export const JM_SEARCH = 'https://18comic.vip/search/photos?search_query='
// 畫面上統稱「有防護、要靠手機讀」的網站（背景進度列用）；移除網站時一起改
export const PROTECTED_LABEL = '禁漫與 MissAV'
