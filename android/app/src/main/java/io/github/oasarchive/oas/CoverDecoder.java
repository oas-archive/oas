package io.github.oasarchive.oas;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.ImageDecoder;
import android.os.Build;
import android.util.Size;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/**
 * 封面解碼（backend/app/imaging.py 呼叫）：用系統內建的解碼器，隨系統更新（OAS_IMAGE_ENGINE=android 時全部用它；用 Pillow 的版本只在 Pillow 打不開 WebP 時用）。
 * 宣告的尺寸太大就在配置記憶體前拒絕，一律直接解到要用的小尺寸；太細長的圖也拒絕，縮小後仍可能很大。
 */
public final class CoverDecoder {
    private static final long MAX_PIXELS = 50_000_000L;  // 和 covers.py 的 Pillow 上限相同
    private static final int MAX_TALLNESS = 8;  // 縮圖最高是寬的幾倍；超過就改用原圖，由網頁顯示

    private CoverDecoder() {
    }

    /** 只讀檔頭，回傳 {寬, 高}。 */
    public static int[] size(String path) throws IOException {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(path, options);
        if (options.outWidth <= 0 || options.outHeight <= 0) throw new IOException("讀不出圖片尺寸");
        return new int[]{options.outWidth, options.outHeight};
    }

    /** 解碼第一格。width 大於 0、height 為 0 時等比縮小；兩個都大於 0 時縮成指定大小。 */
    private static Bitmap decode(String path, int width, int height) throws IOException {
        return ImageDecoder.decodeBitmap(ImageDecoder.createSource(new File(path)), (decoder, info, source) -> {
            Size size = info.getSize();
            if (size.getWidth() <= 0 || size.getHeight() <= 0 || (long) size.getWidth() * size.getHeight() > MAX_PIXELS) {
                throw new IllegalArgumentException("圖片尺寸不接受：" + size);
            }
            decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);
            if (height > 0) {
                decoder.setTargetSize(width, height);
            } else if (size.getWidth() > width) {
                long scaled = (long) size.getHeight() * width / size.getWidth();
                if (scaled > (long) width * MAX_TALLNESS) throw new IllegalArgumentException("圖片太細長：" + size);
                decoder.setTargetSize(width, Math.max(1, (int) scaled));
            }
        });
    }

    /** 縮圖的副檔名：WEBP_LOSSY 要 Android 11 以上。 */
    public static String thumbnailExtension() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.R ? "webp" : "jpg";
    }

    /** 做縮圖存到 out。透明的部分鋪深色底，和畫面底色相近。 */
    public static void thumbnail(String in, String out, int width) throws IOException {
        thumbnail(in, out, width, false);
    }

    /** jpeg 為 true 時一律存 JPEG（呼叫端的縮圖副檔名固定是 jpg 時）。 */
    public static void thumbnail(String in, String out, int width, boolean jpeg) throws IOException {
        Bitmap bitmap = decode(in, width, 0);
        Bitmap opaque = Bitmap.createBitmap(bitmap.getWidth(), bitmap.getHeight(), Bitmap.Config.ARGB_8888);
        new Canvas(opaque).drawARGB(255, 18, 24, 39);
        new Canvas(opaque).drawBitmap(bitmap, 0, 0, null);
        bitmap.recycle();
        try (OutputStream stream = new FileOutputStream(out)) {
            boolean written = !jpeg && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
                    ? opaque.compress(Bitmap.CompressFormat.WEBP_LOSSY, 80, stream)
                    : opaque.compress(Bitmap.CompressFormat.JPEG, 82, stream);
            if (!written) throw new IOException("縮圖存檔失敗");
        } finally {
            opaque.recycle();
        }
    }

    /** 解到 factor 倍大，再每 factor × factor 個像素平均成一個，回傳 width × height 個 ARGB。 */
    public static int[] pixels(String path, int width, int height, int factor) throws IOException {
        int bigWidth = width * factor, bigHeight = height * factor;
        Bitmap bitmap = decode(path, bigWidth, bigHeight);
        Bitmap argb = bitmap.getConfig() == Bitmap.Config.ARGB_8888 ? bitmap : bitmap.copy(Bitmap.Config.ARGB_8888, false);
        int[] big = new int[bigWidth * bigHeight];
        argb.getPixels(big, 0, bigWidth, 0, 0, bigWidth, bigHeight);
        if (argb != bitmap) argb.recycle();
        bitmap.recycle();
        int[] result = new int[width * height];
        int area = factor * factor;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                int r = 0, g = 0, b = 0;
                for (int row = y * factor; row < (y + 1) * factor; row++) {
                    for (int column = x * factor; column < (x + 1) * factor; column++) {
                        int pixel = big[row * bigWidth + column];
                        r += pixel >> 16 & 255;
                        g += pixel >> 8 & 255;
                        b += pixel & 255;
                    }
                }
                result[y * width + x] = 0xff000000 | r / area << 16 | g / area << 8 | b / area;
            }
        }
        return result;
    }
}
