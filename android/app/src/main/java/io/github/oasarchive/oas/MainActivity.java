package io.github.oasarchive.oas;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.UriPermission;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.util.Log;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebSettings;
import android.webkit.WebViewRenderProcess;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.Arrays;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import org.json.JSONObject;
import org.json.JSONTokener;

/** 在背景啟動 Python 服務（127.0.0.1），就緒後用 WebView 開啟典藏館；其他網站的連結交給手機的瀏覽器。 */
public class MainActivity extends AppCompatActivity {
    private static final String TAG = "OAS";
    private static final int PORT = BuildConfig.OAS_PORT;  // 每個版本各用一個固定埠（build.gradle.kts），兩支才能同時開著
    private static final String HOME = "http://127.0.0.1:" + PORT;
    private static volatile boolean serverStarted = false;
    private static volatile boolean serverFailed = false;
    private static volatile String serverError = "";
    // 通行碼：每次啟動服務時隨機產生，只交給 Python 服務與自己的 WebView（cookie）。同一支手機上的其他 App 拿不到，連進來會被拒絕
    private static final String TOKEN = newToken();

    private WebView web;
    private boolean homeLoaded = false;
    private boolean serverReady = false;
    private String pendingShare;  // 服務還沒就緒時收到的分享，就緒後再開啟
    // 網址遇到空白、引號、中文標點就結束（和網頁「一次收錄」相同）；尾端的英文標點與沒成對的右括號在 cleanLink 去掉
    private static final Pattern LINK = Pattern.compile("https?://[^\\s\"'<>，、。；：！？「」『』【】（）《》]+", Pattern.CASE_INSENSITIVE);
    private final Handler main = new Handler(Looper.getMainLooper());

    // 網頁的「選擇備份檔」：交給手機的檔案選擇器，選好的檔案再交回網頁
    private ValueCallback<Uri[]> pendingFiles;
    private final ActivityResultLauncher<Intent> pickFile = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                if (pendingFiles == null) return;
                pendingFiles.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData()));
                pendingFiles = null;
            });

    // 讀作品頁（使用者在設定裡開啟才會用）：有些網站 Python 服務連線讀不到，改在看不到的 WebView 打開，頁面 HTML 交回典藏館網頁再給服務解析。
    // 用 WebView 預設的身分，不偽裝，也不替使用者點按；要操作的驗證就讀不到
    private final ArrayDeque<String[]> readQueue = new ArrayDeque<>();
    private WebView reader;
    private static final int READ_TIMEOUT_MS = 30_000;
    private static final int READ_GIVE_UP_MS = READ_TIMEOUT_MS + 5_000;
    private static final int RENDERER_MAX_DEATHS = 3;
    private static final long RENDERER_WINDOW_MS = 120_000;
    private static final ArrayDeque<Long> RENDERER_DEATHS = new ArrayDeque<>();
    private boolean rebuilding = false;
    private Runnable readLimit;
    private boolean readAnswered;
    private boolean readBusy;  // 正在確認設定或讀一頁：新排進來的等它結束
    private String readingId;  // 目前這頁的編號，取消時要回 null 給網頁
    private int readGeneration;  // 取消一次加一：確認設定回來時已取消就不讀
    // 還在防護驗證頁（Cloudflare 的「Just a moment」等）、還沒載入完、或被廣告帶到別的網站時回傳空字串，等一下再讀；
    // 讀到了就去掉 script、style 等用不到的部分，只交回頁面結構；開頭加一行 <!--oas-url:轉址後的網址-->，後端靠它認得「作品不存在」的錯誤頁
    // （%% 是 String.format 的跳脫；網址的 - 都換成 %2D，註解裡才不會出現 --）
    private static final String GRAB = "(function(key){var t=document.title||'';"
            + "if(document.readyState==='loading'||/just a moment|attention required|請稍候|请稍候|checking your browser/i.test(t)||location.hostname.indexOf(key)<0)return '';"
            + "var d=document.documentElement.cloneNode(true);d.querySelectorAll('script,style,svg,noscript,iframe,template').forEach(function(e){e.remove()});"
            + "return '<!--oas-url:'+encodeURI(location.href).replace(/-/g,'%%2D')+'-->'+d.outerHTML})(%s)";

    // 備份都放 App 私有空間，別的 App 讀不到；匯出才存到使用者選的資料夾，選過就記住，下次按一下就好。
    // 匯出位置等外殼設定記在這裡（名稱沿用最早的設定檔，已裝的 App 不必重選資料夾）
    static final String SHELL_PREFS = "public-shell";
    private static final String NOTICES_PATH = "/third-party-notices.json";
    private File pendingExport;
    private final ActivityResultLauncher<Intent> pickExportFolder = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                File file = pendingExport;
                pendingExport = null;
                Uri tree = result.getData() == null ? null : result.getData().getData();
                if (result.getResultCode() != Activity.RESULT_OK || tree == null) {
                    if (file != null) Toast.makeText(this, "已存在 App 裡，沒有匯出到資料夾", Toast.LENGTH_LONG).show();
                    return;
                }
                getContentResolver().takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                getSharedPreferences(SHELL_PREFS, MODE_PRIVATE).edit().putString("export-tree", tree.toString()).apply();
                web.evaluateJavascript("window.__oasFolderChanged&&window.__oasFolderChanged()", null);
                if (file != null) new Thread(() -> exportBackup(tree, file), "oas-export").start();
            });
    // 自動備份：離開畫面時資料有變才存；10 分鐘內再變動就換掉剛才那份，只留最新幾份，不會堆滿資料夾
    private static final String AUTO_PREFIX = "oas-auto-";
    private static final long AUTO_SLOT_MS = 10 * 60_000;
    private static final int AUTO_KEEP = 10;
    private final AtomicBoolean autoBackingUp = new AtomicBoolean(false);

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 測試版（debuggable）開放 WebView 遠端偵錯：電腦可經 USB 直接檢查手機上的畫面；正式簽署的版本不會開
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0) WebView.setWebContentsDebuggingEnabled(true);
        if (Edition.PRIVACY_CONTROLS) applyHideRecents(getSharedPreferences(SHELL_PREFS, MODE_PRIVATE).getBoolean("hide-recents", true));
        web = new WebView(this);
        web.setBackgroundColor(0xff080b12);
        // Android 15 起畫面預設延伸到狀態列與導覽列底下：外層留出系統列、瀏海與鍵盤的空間，網頁才不會被蓋住
        FrameLayout frame = new FrameLayout(this);
        frame.setBackgroundColor(0xff080b12);
        frame.addView(web);
        setContentView(frame);
        ViewCompat.setOnApplyWindowInsetsListener(frame, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets keyboard = insets.getInsets(WindowInsetsCompat.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, keyboard.bottom));
            return WindowInsetsCompat.CONSUMED;
        });
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);  // 只開自己的 http 頁面，用不到手機上的檔案；選檔案還原走系統的檔案選擇器，不受影響
        settings.setAllowContentAccess(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isOwnPage(uri)) return false;
                // 只把一般網址交給手機的瀏覽器；file:// 等其他類型會讓 Android 丟例外而閃退，也不該從典藏館打開
                String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
                if (!scheme.equals("http") && !scheme.equals("https")) {
                    Toast.makeText(MainActivity.this, "只能開啟 http／https 網址", Toast.LENGTH_LONG).show();
                    return true;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (RuntimeException error) {  // 沒有 App 能開這個連結時提示，不讓整個 App 閃退
                    Toast.makeText(MainActivity.this, "手機上沒有可以開啟這個連結的 App", Toast.LENGTH_LONG).show();
                }
                return true;
            }

            // 圖片、轉址等子資源：cookie 不分連接埠，連到這支手機的其他埠會把通行碼送給別的 App，一律擋下
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (Edition.PRIVACY_CONTROLS && isOwnPage(uri) && NOTICES_PATH.equals(uri.getPath())) return noticesResponse();
                if (!isLoopback(uri) || isOwnPage(uri)) return null;
                return blockedResponse();
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // 典藏館開好後清掉「啟動中」畫面的瀏覽紀錄，返回鍵才不會退回那個畫面
                if (url.startsWith(HOME) && !homeLoaded) {
                    homeLoaded = true;
                    view.clearHistory();
                }
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                rendererGone();
                return true;  // 回傳 false 時系統會殺掉整個 App
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            // 沒有設定時，網頁的 confirm() 不會跳出視窗而是直接當成「取消」，刪除等需要確認的動作會沒有反應
            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                        .setPositiveButton("確定", (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                        .setPositiveButton("確定", (dialog, which) -> result.confirm())
                        .setNegativeButton("取消", (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFiles != null) pendingFiles.onReceiveValue(null);
                pendingFiles = callback;
                // 不限檔案類型：手機上的 .json 常被標成其他類型而無法選取；內容由後端檢查。選過匯出資料夾就直接開在那裡
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                Uri start = exportFolderDocument();
                if (start != null) intent.putExtra(DocumentsContract.EXTRA_INITIAL_URI, start);
                try {
                    pickFile.launch(intent);
                } catch (ActivityNotFoundException error) {
                    pendingFiles = null;
                    callback.onReceiveValue(null);
                    return false;
                }
                return true;
            }
        });
        web.addJavascriptInterface(new PageBridge(), "OASNative");  // 這個 WebView 只開典藏館（127.0.0.1），其他網址都交給瀏覽器
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            if (!isOwnPage(Uri.parse(url))) return;  // 只處理典藏館自己的下載
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
            new Thread(() -> saveBackup(url, name), "oas-download").start();
        });
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (web.canGoBack()) web.goBack();
                else moveTaskToBack(true);  // 退到背景而不是關閉，回來時不必重新載入
            }
        });
        web.loadDataWithBaseURL(null, "<body style='background:#080b12;color:#cfd6e4;font-family:sans-serif;display:grid;place-items:center;height:90vh'>典藏館啟動中…</body>", "text/html", "UTF-8", null);
        // 只在真的是剛分享進來時開收錄表單：從「最近使用的 App」重新打開、或系統重建畫面時，不重播舊的分享
        boolean fromHistory = (getIntent().getFlags() & Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) != 0;
        if (savedInstanceState == null && !fromHistory) pendingShare = shareTarget(getIntent());
        setIntent(new Intent(Intent.ACTION_MAIN));
        setTokenCookie();
        if (serverFailed) {  // 上次啟動失敗（例如埠被占用）：重新打開畫面時再試一次
            serverFailed = false;
            serverStarted = false;
        }
        startServer();
        waitForServer(System.currentTimeMillis());
        Edition.afterCreate(this);
    }

    @Override
    protected void onDestroy() {
        main.removeCallbacksAndMessages(null);
        readQueue.clear();
        if (reader != null) {
            ((ViewGroup) reader.getParent()).removeView(reader);
            reader.destroy();
            reader = null;
        }
        ((ViewGroup) web.getParent()).removeView(web);
        web.destroy();
        super.onDestroy();
    }

    private void setTokenCookie() {
        CookieManager.getInstance().setCookie(HOME, "oas_token=" + TOKEN + "; Path=/; HttpOnly; SameSite=Strict");
    }

    /** 網頁或圖片弄垮畫面元件時：丟掉現在的 WebView，重建畫面。連續發生就停下來顯示說明，不一直重試。 */
    private void rendererGone() {
        if (rebuilding || isDestroyed()) return;
        rebuilding = true;
        long now = System.currentTimeMillis();
        while (!RENDERER_DEATHS.isEmpty() && now - RENDERER_DEATHS.peekFirst() > RENDERER_WINDOW_MS) RENDERER_DEATHS.pollFirst();
        RENDERER_DEATHS.addLast(now);
        Log.w(TAG, "畫面元件中斷，重建畫面");
        main.post(this::recreate);
    }

    /** 典藏館自己的服務：主機與連接埠都要對。 */
    private static boolean isOwnPage(Uri uri) {
        return "http".equalsIgnoreCase(uri.getScheme()) && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == PORT;
    }

    /** http／https 網址是否連到這支手機自己（含 127.1、十進位、十六進位等各種寫法）。 */
    private static boolean isLoopback(Uri uri) {
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
        return (scheme.equals("http") || scheme.equals("https")) && LoopbackHost.matches(uri.getHost());
    }

    private static WebResourceResponse blockedResponse() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", new HashMap<>(), new ByteArrayInputStream(new byte[0]));
    }

    /** 去掉網址尾端的英文標點與沒有成對的右括號（「看這本 https://…/123).」）。 */
    static String cleanLink(String link) {
        String clean = link.replaceAll("[.,;:!?'\"]+$", "");
        while (clean.endsWith(")") && clean.chars().filter(c -> c == '(').count() < clean.chars().filter(c -> c == ')').count()) {
            clean = clean.substring(0, clean.length() - 1).replaceAll("[.,;:!?]+$", "");
        }
        return clean;
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String target = shareTarget(intent);
        if (target == null) return;
        if (serverReady) web.loadUrl(target);
        else pendingShare = target;
    }

    /** 其他 App 分享過來的文字裡找出網址，轉成網頁的 ?add=（與「收錄到 OAS」書籤相同格式），開啟預先填好的收錄表單。 */
    private static String shareTarget(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return null;
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        Matcher match = LINK.matcher(text == null ? "" : text);
        if (!match.find()) return null;
        try {
            JSONObject data = new JSONObject().put("url", cleanLink(match.group()));
            String subject = intent.getStringExtra(Intent.EXTRA_SUBJECT);
            if (subject != null && !subject.trim().isEmpty()) data.put("title", subject.trim());
            return HOME + "/?add=" + URLEncoder.encode(data.toString(), StandardCharsets.UTF_8.name());
        } catch (Exception error) {
            Log.e(TAG, "無法處理分享的內容", error);
            return null;
        }
    }

    // 滑掉 App 時系統不一定會通知，但一定會先離開畫面，所以在這裡備份；開連結到瀏覽器也會經過這裡，靠指紋判斷資料沒變就略過
    @Override
    protected void onStop() {
        super.onStop();
        if (!serverReady || !autoBackingUp.compareAndSet(false, true)) return;
        new Thread(() -> {
            try {
                autoBackup();
            } catch (Exception error) {
                Log.e(TAG, "自動備份失敗", error);
            } finally {
                autoBackingUp.set(false);
            }
        }, "oas-auto-backup").start();
    }

    private void autoBackup() throws Exception {
        SharedPreferences prefs = getSharedPreferences("auto-backup", MODE_PRIVATE);
        String fingerprint = new JSONObject(fetchText(HOME + "/api/backup/fingerprint")).getString("fingerprint");
        if (fingerprint.equals(prefs.getString("fingerprint", null))) return;
        long now = System.currentTimeMillis();
        String name = AUTO_PREFIX + new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date(now)) + ".json";
        String saved = writePrivateBackup(HOME + "/api/backup", name).getPath();
        long slot = prefs.getLong("slot", 0);
        String previous = prefs.getString("uri", null);
        boolean replace = previous != null && now - slot < AUTO_SLOT_MS;
        prefs.edit().putString("fingerprint", fingerprint).putString("uri", saved).putLong("slot", replace ? slot : now).apply();
        // 只刪私有空間裡的；記下的路徑不在私有空間時那份留著不動
        if (replace && previous.startsWith(privateBackupFolder().getPath() + File.separator)) new File(previous).delete();
        prunePrivateBackups(AUTO_PREFIX);
    }

    /** 備份資料夾：App 私有空間的 files/oas/backups（電腦上可用 adb run-as 讀）。 */
    File privateBackupFolder() {
        return new File(getFilesDir(), "oas/backups");
    }

    /** 備份寫進 App 私有空間。先寫暫存檔，寫到一半失敗不會留下壞的備份。 */
    private File writePrivateBackup(String url, String name) throws IOException {
        File folder = privateBackupFolder();
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("無法建立備份資料夾");
        File target = new File(folder, name), partial = new File(folder, name + ".part");
        HttpURLConnection connection = openLocal(url);
        try (InputStream in = connection.getInputStream(); OutputStream out = new FileOutputStream(partial)) {
            copy(in, out);
        } catch (IOException | RuntimeException error) {
            partial.delete();
            throw error;
        }
        if (!partial.renameTo(target)) throw new IOException("無法儲存備份");
        return target;
    }

    /** 同一種備份（自動、手動）只留最新幾份；檔名帶時間，排序就是新舊。 */
    private void prunePrivateBackups(String prefix) {
        File[] files = privateBackupFolder().listFiles((folder, name) -> name.startsWith(prefix) && name.endsWith(".json"));
        if (files == null) return;
        Arrays.sort(files, (a, b) -> b.getName().compareTo(a.getName()));
        for (int index = AUTO_KEEP; index < files.length; index++) files[index].delete();
    }

    static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[64 * 1024];
        for (int read; (read = in.read(buffer)) > 0; ) out.write(buffer, 0, read);
    }

    private Uri exportFolder() {
        String saved = getSharedPreferences(SHELL_PREFS, MODE_PRIVATE).getString("export-tree", null);
        if (saved == null) return null;
        Uri tree = Uri.parse(saved);
        for (UriPermission permission : getContentResolver().getPersistedUriPermissions()) {
            if (permission.getUri().equals(tree) && permission.isWritePermission()) return tree;
        }
        return null;  // 使用者收回權限或資料夾被刪了：當成還沒選過
    }

    private Uri exportFolderDocument() {
        Uri tree = exportFolder();
        return tree == null ? null : DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
    }

    private void askExportFolder() {
        try {
            pickExportFolder.launch(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE));
        } catch (ActivityNotFoundException error) {
            pendingExport = null;
            Toast.makeText(this, "這支手機沒有可以選資料夾的 App", Toast.LENGTH_LONG).show();
        }
    }

    /** 手動備份：先存一份在 App 裡，再存到選好的資料夾；還沒選過就先問。 */
    private void saveBackup(String url, String name) {
        try {
            File file = writePrivateBackup(url, name);
            prunePrivateBackups("oas-backup-");
            Uri tree = exportFolder();
            if (tree != null) {
                exportBackup(tree, file);
                return;
            }
            main.post(() -> {
                if (isDestroyed()) return;
                pendingExport = file;
                askExportFolder();
            });
        } catch (Exception error) {
            Log.e(TAG, "儲存備份失敗", error);
            main.post(() -> Toast.makeText(this, "儲存失敗：" + error.getMessage(), Toast.LENGTH_LONG).show());
        }
    }

    private void exportBackup(Uri tree, File file) {
        try {
            Uri target = DocumentsContract.createDocument(getContentResolver(), DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree)), "application/json", file.getName());
            if (target == null) throw new IOException("無法在資料夾建立檔案");
            try (InputStream in = new FileInputStream(file); OutputStream out = getContentResolver().openOutputStream(target)) {
                copy(in, out);
            }
            main.post(() -> Toast.makeText(this, "已匯出到 " + folderName(tree) + "／" + file.getName(), Toast.LENGTH_LONG).show());
        } catch (Exception error) {
            Log.e(TAG, "匯出備份失敗", error);
            main.post(() -> Toast.makeText(this, "備份已存在 App 裡，但匯出失敗：" + error.getMessage(), Toast.LENGTH_LONG).show());
        }
    }

    /** 給使用者看的資料夾名稱：「primary:Download/OAS」只留路徑。 */
    private static String folderName(Uri tree) {
        String id = DocumentsContract.getTreeDocumentId(tree);
        String path = id.substring(id.indexOf(':') + 1);
        return path.isEmpty() ? "手機儲存空間" : path;
    }

    /** Android 13 起可以只在最近使用畫面隱藏、截圖不受影響；更舊的版本只能連截圖一起擋。 */
    private void applyHideRecents(boolean hide) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) setRecentsScreenshotEnabled(!hide);
        else if (hide) getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
    }

    private WebResourceResponse noticesResponse() {
        try {
            return new WebResourceResponse("application/json", "UTF-8", getAssets().open("THIRD_PARTY_NOTICES.json"));
        } catch (IOException error) {
            return blockedResponse();
        }
    }

    private HttpURLConnection openLocal(String url) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setRequestProperty("Cookie", "oas_token=" + TOKEN);  // 連本機服務都要帶通行碼
        connection.setConnectTimeout(5_000);
        connection.setReadTimeout(60_000);
        return connection;
    }

    private String fetchText(String url) throws IOException {
        HttpURLConnection connection = openLocal(url);
        try (InputStream in = connection.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8 * 1024];
            for (int read; (read = in.read(buffer)) > 0; ) out.write(buffer, 0, read);
            return out.toString("UTF-8");
        }
    }

    private void startServer() {
        if (serverStarted) return;
        serverStarted = true;
        if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
        String filesDir = getFilesDir().getAbsolutePath();
        String backupDir = privateBackupFolder().getAbsolutePath();
        Thread thread = new Thread(() -> {
            try {
                Python.getInstance().getModule("android_main").callAttr("start", filesDir, backupDir, PORT, TOKEN, Edition.NAME);
            } catch (Throwable error) {
                Log.e(TAG, "服務停止", error);
                serverError = error.toString();
                serverFailed = true;  // 讓等待迴圈顯示真正的錯誤原因；畫面重建過也看得到
            }
        }, "oas-server");
        thread.setDaemon(true);
        thread.start();
    }

    private void waitForServer(long startedAt) {
        new Thread(() -> {
            boolean ready = healthy();
            main.post(() -> {
                if (isDestroyed()) return;
                if (serverFailed) {  // 顯示服務的錯誤，不再用「等了兩分鐘」蓋掉
                    showError(serverError);
                    return;
                }
                if (ready) {
                    Log.i(TAG, "服務就緒，耗時 " + (System.currentTimeMillis() - startedAt) + " ms");
                    serverReady = true;
                    if (RENDERER_DEATHS.size() >= RENDERER_MAX_DEATHS) {
                        RENDERER_DEATHS.clear();  // 使用者重新打開時再試一次
                        showError("顯示畫面的元件連續中斷，先停下來。資料沒有受影響，請關閉 App 再打開。");
                        return;
                    }
                    web.loadUrl(pendingShare != null ? pendingShare : HOME);
                    pendingShare = null;
                } else if (System.currentTimeMillis() - startedAt > 120_000) {
                    showError("等了兩分鐘服務還沒就緒");
                } else {
                    main.postDelayed(() -> waitForServer(startedAt), 300);
                }
            });
        }).start();
    }

    /** 給典藏館網頁呼叫：readPage(編號, 網址)，讀完呼叫網頁的 window.__oasPageDone(編號, HTML)；一次讀一頁，其餘排隊。 */
    private class PageBridge {
        @JavascriptInterface
        public void readPage(String id, String url) {
            if (id == null || url == null || !url.matches("(?i)https?://[^\\s\\\\]+") || isLoopback(Uri.parse(url))) {
                main.post(() -> deliverPage(id, null));
                return;
            }
            if (Edition.CHECK_READER_SETTING) {
                // 不只信網頁說的：自己向服務確認使用者開了背景讀取才讀。網路請求不能在主執行緒，也不要卡住呼叫的網頁
                new Thread(() -> {
                    boolean allowed = backgroundReaderOn();
                    main.post(() -> {
                        if (allowed) queueRead(id, url);
                        else deliverPage(id, null);
                    });
                }, "oas-read-check").start();
                return;
            }
            main.post(() -> queueRead(id, url));
        }

        // 備份中心：備份都在 App 私有空間，網頁據此顯示「App 裡的備份」與匯出資料夾
        @JavascriptInterface
        public boolean privateBackups() {
            return true;
        }

        @JavascriptInterface
        public String backupFolderName() {
            Uri tree = exportFolder();
            return tree == null ? "" : folderName(tree);
        }

        @JavascriptInterface
        public void chooseBackupFolder() {
            main.post(MainActivity.this::askExportFolder);
        }

        // 以下是隱私相關的外殼功能（Edition.PRIVACY_CONTROLS）
        /** 關掉背景讀取時：取消排隊中與正在讀的作品頁，再清掉網站留下的 cookie。通行碼也是 cookie，清完要放回去。 */
        @JavascriptInterface
        public void clearReaderCookies() {
            if (!Edition.PRIVACY_CONTROLS) return;
            main.post(() -> {
                cancelReads();
                CookieManager.getInstance().removeAllCookies(done -> {
                    setTokenCookie();
                    CookieManager.getInstance().flush();
                });
            });
        }

        @JavascriptInterface
        public void clearWebCache() {
            if (Edition.PRIVACY_CONTROLS) main.post(() -> web.clearCache(true));
        }

        @JavascriptInterface
        public void exitApp() {
            if (Edition.PRIVACY_CONTROLS) main.post(MainActivity.this::finishAndRemoveTask);
        }

        @JavascriptInterface
        public boolean hidesRecents() {
            return Edition.PRIVACY_CONTROLS && getSharedPreferences(SHELL_PREFS, MODE_PRIVATE).getBoolean("hide-recents", true);
        }

        @JavascriptInterface
        public void setHideRecents(boolean hide) {
            if (!Edition.PRIVACY_CONTROLS) return;
            getSharedPreferences(SHELL_PREFS, MODE_PRIVATE).edit().putBoolean("hide-recents", hide).apply();
            main.post(() -> applyHideRecents(hide));
        }
    }

    private void queueRead(String id, String url) {
        if (isDestroyed()) return;
        readQueue.add(new String[]{id, url});
        if (!readBusy) nextRead();
    }

    /** 關掉背景讀取時：排隊中的全部回 null，正在讀的那頁立刻結束；正在確認設定的那頁回來時看到已取消，也回 null。主執行緒呼叫。 */
    private void cancelReads() {
        readGeneration++;
        for (String[] job; (job = readQueue.poll()) != null; ) deliverPage(job[0], null);
        if (reader != null) finishRead(readingId, null);
    }

    /** 帶通行碼問本機服務，第一次啟動選完且背景讀取開著才算數；讀不到設定一律當作沒開。不可在主執行緒呼叫。 */
    private boolean backgroundReaderOn() {
        try {
            JSONObject prefs = new JSONObject(fetchText(HOME + "/api/preferences"));
            return prefs.optBoolean("setup_done", false) && prefs.optBoolean("background_reader", false);
        } catch (Exception error) {
            Log.w(TAG, "讀作品頁：確認背景讀取設定失敗，不讀", error);
            return false;
        }
    }

    private void nextRead() {
        String[] job = readQueue.poll();
        readBusy = job != null;
        if (job == null || isDestroyed()) return;
        if (!Edition.CHECK_READER_SETTING) {
            startRead(job);
            return;
        }
        // 每一頁開讀前都再問一次服務：排隊時開著、之後關掉的就不讀。網路請求不能在主執行緒
        int generation = readGeneration;
        new Thread(() -> {
            boolean allowed = backgroundReaderOn();
            main.post(() -> {
                if (isDestroyed()) return;
                if (allowed && generation == readGeneration) {
                    startRead(job);
                } else {
                    deliverPage(job[0], null);
                    nextRead();
                }
            });
        }, "oas-read-check").start();
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void startRead(String[] job) {
        readingId = job[0];
        String host = Uri.parse(job[1]).getHost();
        String key = host == null ? "" : host.toLowerCase().replaceFirst("^www\\.", "").split("\\.")[0];  // 只比網域的第一段：同一個網站換後綴也算
        reader = new WebView(this);
        WebSettings settings = reader.getSettings();
        settings.setJavaScriptEnabled(true);  // 很多網站要跑 JavaScript 才有內容；不需要操作的自動驗證也會照常通過，App 的說明有寫
        settings.setDomStorageEnabled(true);
        settings.setBlockNetworkImage(true);  // 只要文字，不載圖片
        settings.setAllowFileAccess(false);  // 這個 WebView 載入外部網站，不能碰手機上的檔案
        settings.setAllowContentAccess(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(reader, Edition.READER_THIRD_PARTY_COOKIES);
        reader.setWebViewClient(new WebViewClient() {
            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                rendererGone();
                return true;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return scheme == null || !scheme.toLowerCase().startsWith("http") || isLoopback(request.getUrl());  // 網站轉址照走；intent:、market: 與這支手機上的服務不開
            }

            // 網頁載入的所有網址都檢查，這裡拿到的是瀏覽器正規化後的網址：cookie 不分連接埠，連到這支手機的其他埠會把通行碼送給別的 App
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return isLoopback(request.getUrl()) ? blockedResponse() : null;
            }
        });
        reader.setAlpha(0f);  // 要掛在畫面上網頁才會正常執行，但大小 1 像素、完全透明
        ((ViewGroup) web.getParent()).addView(reader, new FrameLayout.LayoutParams(1, 1));
        reader.loadUrl(job[1]);
        long started = System.currentTimeMillis();
        WebView current = reader;
        readAnswered = true;
        // 硬性上限：網頁卡死（例如無窮迴圈）時，下面的查詢不會有回音，逾時判斷也跑不到。畫面元件是共用的，要終止它才救得回來
        readLimit = () -> {
            if (reader != current) return;
            WebViewRenderProcess renderer = readAnswered ? null : current.getWebViewRenderProcess();
            finishRead(job[0], null);
            if (renderer != null) renderer.terminate();
        };
        main.postDelayed(readLimit, READ_GIVE_UP_MS);
        main.postDelayed(() -> pollPage(job[0], key, started, current), 2500);
    }

    private void pollPage(String id, String key, long started, WebView current) {
        if (reader != current) return;
        readAnswered = false;
        current.evaluateJavascript(String.format(GRAB, JSONObject.quote(key)), value -> {
            if (reader != current) return;  // 已經逾時結束，回音晚到不算數
            readAnswered = true;
            String html = "";
            try {
                Object decoded = new JSONTokener(value == null ? "null" : value).nextValue();
                if (decoded instanceof String) html = (String) decoded;
            } catch (Exception error) {
                Log.w(TAG, "讀作品頁：結果無法解析", error);
            }
            if (!html.isEmpty()) finishRead(id, html);
            else if (System.currentTimeMillis() - started > READ_TIMEOUT_MS) finishRead(id, null);
            else main.postDelayed(() -> pollPage(id, key, started, current), 1500);
        });
    }

    private void finishRead(String id, String html) {
        if (readLimit != null) main.removeCallbacks(readLimit);
        readLimit = null;
        if (reader != null) {
            ((ViewGroup) reader.getParent()).removeView(reader);
            reader.stopLoading();
            reader.destroy();
            reader = null;
        }
        deliverPage(id, html);
        nextRead();
    }

    private void deliverPage(String id, String html) {
        if (id == null || isDestroyed()) return;
        web.evaluateJavascript("window.__oasPageDone&&window.__oasPageDone(" + JSONObject.quote(id) + "," + (html == null ? "null" : JSONObject.quote(html)) + ")", null);
    }

    private static String newToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        StringBuilder hex = new StringBuilder();  // HexFormat 要 Android 14 以上，App 支援到 Android 10
        for (byte b : bytes) hex.append(String.format("%02x", b));
        return hex.toString();
    }

    /** 服務就緒、而且確實是自己啟動的那個服務：送出隨機 nonce，回覆的 proof 要等於 HMAC-SHA256(通行碼, nonce)。 */
    private static boolean healthy() {
        try {
            String nonce = newToken();
            HttpURLConnection connection = (HttpURLConnection) new URL(HOME + "/api/health?nonce=" + nonce).openConnection();
            connection.setConnectTimeout(500);
            connection.setReadTimeout(1000);
            if (connection.getResponseCode() != 200) return false;
            ByteArrayOutputStream body = new ByteArrayOutputStream();
            try (InputStream in = connection.getInputStream()) {
                byte[] buffer = new byte[4096];
                for (int read; (read = in.read(buffer)) > 0 && body.size() < 65536; ) body.write(buffer, 0, read);
            }
            connection.disconnect();
            String proof = new JSONObject(body.toString(StandardCharsets.UTF_8.name())).optString("proof", "");
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(TOKEN.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            StringBuilder expected = new StringBuilder();
            for (byte b : mac.doFinal(nonce.getBytes(StandardCharsets.UTF_8))) expected.append(String.format("%02x", b));
            boolean genuine = java.security.MessageDigest.isEqual(expected.toString().getBytes(StandardCharsets.UTF_8), proof.getBytes(StandardCharsets.UTF_8));
            if (!genuine) Log.w(TAG, PORT + " 埠上的服務回覆的驗證碼不對，可能是其他 App 佔用了這個埠");
            return genuine;
        } catch (Exception error) {
            return false;
        }
    }

    private void showError(String message) {
        String safe = message.replace("&", "&amp;").replace("<", "&lt;");
        web.loadDataWithBaseURL(null, "<body style='background:#080b12;color:#ffb4b4;font-family:sans-serif;padding:24px'><h3>典藏館無法啟動</h3><pre style='white-space:pre-wrap'>" + safe + "</pre></body>", "text/html", "UTF-8", null);
    }
}
