package kdk.lefu;

import org.json.JSONObject;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

/** Body Pod 2 scale-sniff capture (JSONL of {"kind":"frame","hex":...}); same rules as the lab's run.py. */
final class Capture {
    Double weightKg;
    final long[][] df = new long[2][5];   // df[x][i]: x=0 is the library's 20 kHz input, i = frame position

    static Capture parse(Path p) throws IOException {
        Capture c = new Capture();
        Map<String, byte[]> dfr = new HashMap<>();
        int be = 0;
        for (String line : Files.readAllLines(p)) {
            if (line.isBlank()) continue;
            JSONObject d = new JSONObject(line);
            if (!"frame".equals(d.optString("kind"))) continue;
            byte[] b = hex(d.getString("hex"));
            int x = 0;
            for (int i = 0; i < b.length - 1; i++) x ^= b[i] & 0xff;
            if (x != (b[b.length - 1] & 0xff)) continue;
            int t = b[0] & 0xff;
            if (t == 0xCF && b.length == 11 && (b[2] & 0x80) != 0 && c.weightKg == null) {
                int kg = (b[3] & 0xff) | (b[4] & 0xff) << 8;
                if (kg > 0) c.weightKg = kg / 100.0;
            } else if (t == 0xBE && b.length == 13) {
                be++;
            } else if (t == 0xDF && b.length == 17) {
                byte[] v = new byte[12]; System.arraycopy(b, 4, v, 0, 12);
                dfr.put((b[2] & 0xff) + "," + (b[3] & 0xff), v);
            }
        }
        if (c.weightKg == null || dfr.size() != 4)
            throw new IllegalArgumentException(p + ": incomplete weigh-in (weight=" + (c.weightKg != null) + ", BE=" + be + ", DF=" + dfr.size() + ")");
        for (int x = 0; x < 2; x++) {
            byte[] raw = new byte[20];
            System.arraycopy(dfr.get("0," + x), 0, raw, 0, 12);
            System.arraycopy(dfr.get("1," + x), 0, raw, 12, 8);
            for (int i = 0; i < 5; i++) {
                long v = 0;
                for (int k = 3; k >= 0; k--) v = (v << 8) | (raw[4 * i + k] & 0xff);   // little-endian
                c.df[x][i] = v;
            }
        }
        return c;
    }

    static byte[] hex(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }
}
