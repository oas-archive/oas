package io.github.oasarchive.oas;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.text.Normalizer;

/** 判斷主機名稱是否指向這支手機自己。瀏覽器接受的寫法很多（127.1、2130706433、0x7f.1、0177.0.0.1、全形數字、結尾的點），只比對字面會被繞過。 */
final class LoopbackHost {
    private LoopbackHost() {
    }

    static boolean matches(String rawHost) {
        if (rawHost == null) return true;  // 解析不出主機的 http 網址：當成不安全
        String host = Normalizer.normalize(rawHost, Normalizer.Form.NFKC).replace('\u3002', '.').toLowerCase();
        while (host.endsWith(".")) host = host.substring(0, host.length() - 1);
        if (host.isEmpty() || host.equals("localhost") || host.endsWith(".localhost")) return true;
        if (host.startsWith("[") && host.endsWith("]")) host = host.substring(1, host.length() - 1);
        if (host.contains(":")) return isLoopbackIpv6(host);
        long address = ipv4(host);
        return address >= 0 && ((address >> 24) == 127 || (address >> 24) == 0);  // 0.0.0.0 在 Android 上也會連到自己
    }

    private static boolean isLoopbackIpv6(String host) {
        try {
            InetAddress address = InetAddress.getByName(host);  // 含冒號的字面位址，不會查 DNS
            return address.isLoopbackAddress() || address.isAnyLocalAddress();
        } catch (UnknownHostException | SecurityException error) {
            return true;
        }
    }

    /** 依瀏覽器的規則（inet_aton）解讀 IPv4；不是數字位址時回傳 -1。 */
    private static long ipv4(String host) {
        String[] parts = host.split("\\.", -1);
        if (parts.length > 4) return -1;
        long address = 0;
        for (int index = 0; index < parts.length; index++) {
            long value = number(parts[index]);
            if (value < 0) return -1;
            boolean last = index == parts.length - 1;
            int bytes = last ? 5 - parts.length : 1;
            if (value >= (1L << (8 * bytes))) return -1;
            address = last ? (address << (8 * bytes)) | value : (address << 8) | value;
        }
        return address;
    }

    private static long number(String part) {
        try {
            if (part.startsWith("0x")) return part.length() == 2 ? 0 : Long.parseLong(part.substring(2), 16);
            if (part.length() > 1 && part.startsWith("0")) return Long.parseLong(part.substring(1), 8);
            return part.isEmpty() ? -1 : Long.parseLong(part);
        } catch (NumberFormatException error) {
            return -1;
        }
    }
}
