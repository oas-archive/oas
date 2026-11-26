package io.github.oasarchive.oas;

/** 這個版本的外殼設定（MainActivity 讀這裡，Python 服務另看 NAME）。 */
final class Edition {
    private Edition() {}

    /** 交給 Python 服務的版本名稱（android_main.start 的 edition）。 */
    static final String NAME = "public";
    /** 背景讀作品頁：排進來與每一頁開讀前，都自己向服務確認使用者開了背景讀取，不只信網頁說的。 */
    static final boolean CHECK_READER_SETTING = true;
    /** 讀作品頁的 WebView 收不收第三方 cookie。 */
    static final boolean READER_THIRD_PARTY_COOKIES = false;
    /** 隱私相關的外殼功能：最近使用畫面隱藏、關掉背景讀取時清 cookie、清暫存、離開 App、授權聲明頁。 */
    static final boolean PRIVACY_CONTROLS = true;

    /** 畫面建立、服務開始啟動後要另外做的事：沒有。 */
    static void afterCreate(MainActivity activity) {}
}
