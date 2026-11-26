// 手機版：用 Chaquopy 在 App 裡執行電腦版同一套 Python 後端（只開放給手機自己，127.0.0.1），畫面用 WebView 顯示打包好的網頁。
plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val backend = rootDir.resolve("../backend")
val frontendDist = rootDir.resolve("../frontend/dist")
// 版本號只有一個來源：settings.py 的 VERSION
val oasVersion = Regex("""^VERSION = "([^"]+)"""", RegexOption.MULTILINE).find(backend.resolve("app/settings.py").readText())!!.groupValues[1]
// 0.20.50 → 20050：只會往上加，Obtainium 與覆蓋安裝才認得出是新版
val publicVersionCode = oasVersion.split(".").map { it.toInt() }.let { (major, minor, patch) -> major * 1_000_000 + minor * 1_000 + patch }

// 把後端（app/）與打包好的網頁（放進 oas_web 套件）複製成 Chaquopy 的 Python 原始碼。
fun copyPythonTask(flavor: String, vararg skip: String) = tasks.register<Sync>("copyOasPython${flavor.replaceFirstChar { it.uppercase() }}") {
    from(backend.resolve("app")) {
        into("app")
        exclude("**/__pycache__/**", *skip)
    }
    from(frontendDist) { into("oas_web") }
    from(file("python-boot")) // android_main.py、oas_web/__init__.py（不放在 src/main/python：那是 Chaquopy 預設的原始碼資料夾，會重複）
    into(layout.buildDirectory.dir("oas-python/$flavor"))
    doFirst {
        val index = frontendDist.resolve("index.html")
        check(index.exists()) { "找不到 frontend/dist，請先在 frontend 執行 npm run build" }
        // 網頁原始碼比打包結果新：代表改了畫面卻還沒重新打包，裝到手機會是舊畫面
        val sources = rootDir.resolve("../frontend").let { frontend -> listOf(frontend.resolve("src"), frontend.resolve("index.html"), frontend.resolve("public")) }
        val newest = sources.flatMap { source -> if (source.isDirectory) source.walkTopDown().filter { it.isFile }.toList() else listOf(source) }
            .filter { it.exists() }.maxOfOrNull { it.lastModified() } ?: 0L
        check(newest <= index.lastModified()) { "frontend/dist 比網頁原始碼舊，請先在 frontend 執行 npm run build" }
    }
    doLast {
        if (skip.isNotEmpty()) check(destinationDir.walkTopDown().none { it.name == "tag_pack_private.json" }) { "公開版裡出現私人翻譯包，已停止打包" }
    }
}

val copyOasPythonPublic = copyPythonTask("public", "data/tag_pack_private.json")
val copyOasPythonTasks = mutableListOf<Any>(copyOasPythonPublic)

android {
    namespace = "io.github.oasarchive.oas"
    compileSdk = 35

    defaultConfig {
        minSdk = 29  // 最低支援 Android 10（API 29）
        targetSdk = 35
    }

    buildFeatures { buildConfig = true }
    flavorDimensions += "edition"
    // 兩版不同的外殼行為放在各自 source set 的 Edition.java（src/<flavor>/java/），MainActivity 只讀它
    productFlavors {
        create("public") {
            dimension = "edition"
            applicationId = "io.github.oasarchive.app"
            versionCode = publicVersionCode
            versionName = oasVersion
            ndk { abiFilters += "arm64-v8a" }
            buildConfigField("int", "OAS_PORT", "8081")
        }
    }

    // 打包工具預設會把 commit 資訊放進 release 版，公開的 APK 不要帶
    buildTypes.getByName("release").vcsInfo.include = false

    // 正式金鑰不放 repo：路徑與密碼由環境變數提供；沒有設定時，公開版 release 是未簽署的 APK
    System.getenv("OAS_RELEASE_STORE")?.let { store ->
        signingConfigs.create("release") {
            storeFile = file(store)
            storePassword = System.getenv("OAS_RELEASE_STORE_PASSWORD")
            keyAlias = System.getenv("OAS_RELEASE_KEY_ALIAS")
            keyPassword = System.getenv("OAS_RELEASE_KEY_PASSWORD")
        }
        buildTypes.getByName("release").signingConfig = signingConfigs.getByName("release")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}


chaquopy {
    defaultConfig {
        version = "3.11"
        // 建置用的 Python 3.11：可用環境變數 OAS_BUILD_PYTHON 指定執行檔，沒有時用 Windows 的 py 啟動器
        System.getenv("OAS_BUILD_PYTHON")?.let { buildPython(it) } ?: buildPython("py", "-3.11")
        // 後端以 __file__ 找預設內容（app/data），網頁要以實際檔案提供，所以這兩個套件解開成一般檔案
        extractPackages("app", "oas_web")
    }
    productFlavors {
        // 公開版不帶 Pillow 與它老舊的 JPEG、字型函式庫，用系統內建的解碼器
        getByName("public") { pip { install("-r", backend.resolve("requirements-android-public.txt").path) } }
    }
    sourceSets {
        getByName("public") { srcDir(layout.buildDirectory.dir("oas-python/public")) }
    }
}

tasks.named("preBuild") { dependsOn(copyOasPythonTasks) }
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach { dependsOn(copyOasPythonTasks) }

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    // appcompat 帶進不同版本的 Kotlin 標準庫，統一成同一版避免類別重複
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:1.8.22"))
}
