package kdk.lefu;

import com.lefu.ppbase.PPBodyBaseModel;
import com.lefu.ppbase.PPDeviceModel;
import com.lefu.ppbase.PPScaleDefine;
import com.lefu.ppbase.PPScaleHelper;
import com.lefu.ppbase.vo.PPUnitType;
import com.lefu.ppbase.vo.PPUserGender;
import com.lefu.ppbase.vo.PPUserModel;
import com.lefu.ppcalculate.PPBodyFatModel;
import com.lefu.ppcalculate.PPCalculateKit;
import com.lefu.ppcalculate.vo.PPBodyDetailModel;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.*;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

/**
 * lefu-engine: Lefu 8-electrode body composition on a plain JVM, native engine emulated (arm64, unidbg).
 *
 *   lefu-engine --capture A.jsonl --height 170 --age 30 --sex male [--weight-kg W] [--variant V] [--order 1,0,4,3,2] [--full]
 *   lefu-engine --df v0,...,v9 --weight-kg W --height 170 --age 30 --sex male      (v0-4 = DF x=0 (20 kHz), v5-9 = x=1)
 *   lefu-engine --cases cases.json      (harness batch format; prints the same JSON array the emulator harness wrote)
 *   lefu-engine --serve                 (one case JSON per stdin line -> one result JSON per stdout line)
 *   lefu-engine --case -                (scale-bridge: ONE case JSON on stdin -> one result JSON line + "timing";
 *                                        exit 0 = result (check errorType), 3 = the calculation threw)
 *
 * Secret: LEFU_SECRET_8 (8-electrode) / LEFU_SECRET_4, or LEFU_SECRET_8_FILE / LEFU_SECRET_4_FILE. Never printed.
 * The case/result logic below is a line-for-line port of the emulator harness (project/src/.../CalcReceiver.java).
 */
public final class Main {
    static final String[] HEADLINE = {"getPpFat", "getPpWaterPercentage", "getPpBMR", "getPpBodySkeletalKg", "getPpMuscleKg",
            "getPpBoneKg", "getPpVisceralFat", "getPpBodyAge", "getPpBodyFatSubCutPercentage"};

    public static void main(String[] argv) {
        PrintStream err = System.err;
        try {
            System.exit(run(argv, err));
        } catch (Throwable t) {
            if (NativeBridge.Quiet.DEBUG) t.printStackTrace(err);
            err.println("lefu-engine: " + t);
            System.exit(2);
        }
    }

    static int run(String[] argv, PrintStream err) throws Exception {
        Map<String, String> a = args(argv);
        long t0 = System.nanoTime();
        if (!NativeBridge.Quiet.DEBUG) {   // vendor Java code prints its inputs to System.out; keep it off stdout/stderr
            System.setOut(NativeBridge.Quiet.NULL); System.setErr(NativeBridge.Quiet.NULL);
        }
        PPCalculateKit.INSTANCE.initSdk(new android.content.Context(
                new File(System.getenv().getOrDefault("LEFU_ASSETS", "/opt/lefu/assets"))));
        PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        if (a.containsKey("case")) {   // scale-bridge: one case JSON on stdin -> one result JSON line on stdout
            if (!a.get("case").equals("-")) throw new IllegalArgumentException("--case takes - (stdin)");
            JSONObject c = new JSONObject(new String(System.in.readAllBytes(), StandardCharsets.UTF_8));
            long t1 = System.nanoTime();
            JSONObject r = runCase(c);
            long t2 = System.nanoTime();
            r.put("timing", new JSONObject().put("init_ms", Math.round((t1 - t0) / 1e6)).put("calc_ms", Math.round((t2 - t1) / 1e6)));
            out.println(r.toString());
            return r.has("exception") ? 3 : 0;
        }
        if (a.containsKey("serve")) { serve(out); return 0; }
        if (a.containsKey("cases")) {
            String text = a.get("cases").equals("-") ? new String(System.in.readAllBytes(), StandardCharsets.UTF_8)
                    : Files.readString(Path.of(a.get("cases")));
            JSONArray cases = new JSONArray(text), res = new JSONArray();
            long t1 = System.nanoTime();
            for (int i = 0; i < cases.length(); i++) res.put(runCase(cases.getJSONObject(i)));
            long t2 = System.nanoTime();
            out.println(res.toString());
            if (a.containsKey("timing"))
                err.printf(Locale.ROOT, "timing: init+first-load %.0f ms, %d cases %.0f ms (%.1f ms/case)%n",
                        (t1 - t0) / 1e6, cases.length(), (t2 - t1) / 1e6, (t2 - t1) / 1e6 / Math.max(1, cases.length()));
            return 0;
        }
        JSONObject c = singleCase(a);
        JSONObject r = runCase(c);
        if (!a.containsKey("full")) r.remove("base");
        out.println(r.toString(2));
        return r.has("exception") ? 3 : 0;
    }

    static void serve(PrintStream out) throws IOException {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        for (String line; (line = in.readLine()) != null; ) {
            if (line.isBlank()) continue;
            JSONObject r;
            try { r = runCase(new JSONObject(line)); } catch (Exception e) { r = new JSONObject().put("exception", String.valueOf(e)); }
            out.println(r.toString());
        }
    }

    // ---------------- single-measurement CLI ----------------
    static JSONObject singleCase(Map<String, String> a) throws IOException {
        long[] x0, x1; Double w = a.containsKey("weight-kg") ? Double.valueOf(a.get("weight-kg")) : null;
        if (a.containsKey("capture")) {
            Capture cap = Capture.parse(Path.of(a.get("capture")));
            x0 = cap.df[0]; x1 = cap.df[1];
            if (w == null) w = cap.weightKg;
        } else if (a.containsKey("df")) {
            String[] p = a.get("df").split(",");
            if (p.length != 10) throw new IllegalArgumentException("--df needs 10 comma-separated values");
            x0 = new long[5]; x1 = new long[5];
            for (int i = 0; i < 5; i++) { x0[i] = Long.parseLong(p[i].trim()); x1[i] = Long.parseLong(p[i + 5].trim()); }
        } else throw new IllegalArgumentException("need --capture, --df, --cases or --serve");
        if (w == null) throw new IllegalArgumentException("need --weight-kg");
        int[] order = Arrays.stream(a.getOrDefault("order", "1,0,4,3,2").split(",")).mapToInt(Integer::parseInt).toArray();
        JSONArray z20 = new JSONArray(), z100 = new JSONArray();
        for (int i = 0; i < 5; i++) { z20.put(x0[order[i]]); z100.put(x1[order[i]]); }   // order[i] = frame position for slot i
        return new JSONObject().put("id", "cli").put("type", a.getOrDefault("variant", "PPDeviceCalcuteTypeAlternate8_4"))
                .put("weightKg", w).put("heightCm", (int) Math.round(Double.parseDouble(req(a, "height"))))
                .put("age", Integer.parseInt(req(a, "age"))).put("sex", a.getOrDefault("sex", "male"))
                .put("z20", z20).put("z100", z100).put("bodyAgeType", Integer.parseInt(a.getOrDefault("body-age-type", "0")));
    }

    static String req(Map<String, String> a, String k) {
        String v = a.get(k); if (v == null) throw new IllegalArgumentException("missing --" + k); return v;
    }

    static Map<String, String> args(String[] v) {
        Map<String, String> m = new HashMap<>();
        for (int i = 0; i < v.length; i++) {
            if (!v[i].startsWith("--")) throw new IllegalArgumentException("bad arg " + v[i]);
            String k = v[i].substring(2);
            if (i + 1 < v.length && !v[i + 1].startsWith("--")) m.put(k, v[++i]); else m.put(k, "");
        }
        return m;
    }

    static String secret(boolean is8) {
        String n = is8 ? "LEFU_SECRET_8" : "LEFU_SECRET_4";
        String s = System.getenv(n);
        if ((s == null || s.isEmpty()) && System.getenv(n + "_FILE") != null) {
            try { s = Files.readString(Path.of(System.getenv(n + "_FILE"))).trim(); } catch (IOException e) { throw new UncheckedIOException(n + "_FILE unreadable", e); }
        }
        if (s == null || s.isEmpty()) throw new IllegalStateException(n + " (or " + n + "_FILE) not set");
        return s.trim();
    }

    // ---------------- port of the harness (CalcReceiver.runCase / runDirect / scalars) ----------------
    static JSONObject runCase(JSONObject c) throws Exception {
        if (c.optBoolean("direct")) return runDirect(c);
        JSONObject r = new JSONObject();
        r.put("id", c.optString("id"));
        try {
            PPScaleDefine.PPDeviceCalcuteType type = PPScaleDefine.PPDeviceCalcuteType.valueOf(c.getString("type"));
            PPUserGender sex = "female".equals(c.optString("sex")) ? PPUserGender.PPUserGenderFemale : PPUserGender.PPUserGenderMale;
            PPUserModel user = new PPUserModel.Builder().setSex(sex).setHeight(c.getInt("heightCm")).setAge(c.getInt("age")).build();
            PPDeviceModel dev = new PPDeviceModel("", "");
            dev.setDeviceCalcuteType(type);
            PPBodyBaseModel b = new PPBodyBaseModel();
            double w = c.getDouble("weightKg");
            b.setWeight((int) ((w + 0.005) * 100));            // demo UnitUtil.getWeight
            b.setDeviceModel(dev);
            b.setUserModel(user);
            b.setUnit(PPUnitType.Unit_KG);
            JSONArray z20 = c.getJSONArray("z20"), z100 = c.getJSONArray("z100");
            b.setZ20KhzLeftArmEnCode(z20.getLong(0));  b.setZ100KhzLeftArmEnCode(z100.getLong(0));
            b.setZ20KhzRightArmEnCode(z20.getLong(1)); b.setZ100KhzRightArmEnCode(z100.getLong(1));
            b.setZ20KhzLeftLegEnCode(z20.getLong(2));  b.setZ100KhzLeftLegEnCode(z100.getLong(2));
            b.setZ20KhzRightLegEnCode(z20.getLong(3)); b.setZ100KhzRightLegEnCode(z100.getLong(3));
            b.setZ20KhzTrunkEnCode(z20.getLong(4));    b.setZ100KhzTrunkEnCode(z100.getLong(4));
            String sec = secret(PPScaleHelper.INSTANCE.isCalcute8(type.getType()));
            if (c.optBoolean("badSecret")) sec = "0000000000000000000000000000000000000000000000000000000000000000"; // control only
            b.setSecret(sec);
            b.setBodyAgeType(c.optInt("bodyAgeType", 0));
            PPBodyFatModel fat = new PPBodyFatModel(b, null);
            try { new PPBodyDetailModel(fat); r.put("detailModel", "ok"); }
            catch (Throwable t) { r.put("detailModel", "error " + t); }
            r.put("errorType", String.valueOf(fat.getErrorType()));
            JSONObject o = scalars(fat), bm = scalars(fat.getPpBodyBaseModel());
            if (c.optBoolean("slim")) {
                JSONObject so = new JSONObject(), sb = new JSONObject();
                for (String k : HEADLINE) so.put(k, o.opt(k));
                for (String k : bm.keySet()) if (k.contains("DeCode")) sb.put(k, bm.opt(k));
                o = so; bm = sb;
            }
            r.put("out", o);
            r.put("base", bm);
        } catch (Throwable t) {
            if (NativeBridge.Quiet.DEBUG) t.printStackTrace();
            r.put("exception", String.valueOf(t));
        }
        return r;
    }

    static JSONObject runDirect(JSONObject c) throws Exception {
        JSONObject r = new JSONObject();
        r.put("id", c.optString("id"));
        try {
            com.besthealth.bh5BodyComposition.BhBody270 n = new com.besthealth.bh5BodyComposition.BhBody270();
            n.secret = secret(true);
            n.bhSex = "female".equals(c.optString("sex")) ? 0 : 1;
            if (c.has("bhSex")) n.bhSex = c.getInt("bhSex");
            n.bhAge = c.getInt("age");
            n.bhHeightCm = (float) c.getDouble("heightCm");
            n.bhWeightKg = (float) c.getDouble("weightKg");
            n.bhProduct = c.getInt("product");
            n.bhTrimming = (float) c.optDouble("trimming", 1.0);
            n.bhIsHome = c.optBoolean("isHome", false);
            n.bhIsEnhancedRepeat = c.optBoolean("enhanced", false);
            n.bhSmoothlyK = c.optDouble("smoothK", 1.0);
            JSONArray z20 = c.getJSONArray("z20"), z100 = c.getJSONArray("z100");
            n.bhZ20KhzLeftArmEnCode = z20.getLong(0);  n.bhZ100KhzLeftArmEnCode = z100.getLong(0);
            n.bhZ20KhzRightArmEnCode = z20.getLong(1); n.bhZ100KhzRightArmEnCode = z100.getLong(1);
            n.bhZ20KhzLeftLegEnCode = z20.getLong(2);  n.bhZ100KhzLeftLegEnCode = z100.getLong(2);
            n.bhZ20KhzRightLegEnCode = z20.getLong(3); n.bhZ100KhzRightLegEnCode = z100.getLong(3);
            n.bhZ20KhzTrunkEnCode = z20.getLong(4);    n.bhZ100KhzTrunkEnCode = z100.getLong(4);
            int err = n.getBodyComposition();
            r.put("errorCode", err);
            r.put("errorType", String.valueOf(com.besthealth.bh5BodyComposition.BhErrorType.values()[err]));
            JSONObject o = new JSONObject();
            for (java.lang.reflect.Field f : n.getClass().getFields()) {
                if (f.getName().equals("secret") || Modifier.isStatic(f.getModifiers())) continue;
                Class<?> t = f.getType();
                if (t == float.class || t == int.class || t == double.class || t == long.class || t == boolean.class) {
                    Object v = f.get(n);
                    if (v instanceof Float && (((Float) v).isNaN() || ((Float) v).isInfinite())) v = String.valueOf(v);
                    o.put(f.getName(), v);
                } else if (t == String.class) o.put(f.getName(), f.get(n));
            }
            r.put("out", o);
        } catch (Throwable t) {
            r.put("exception", String.valueOf(t));
        }
        return r;
    }

    static JSONObject scalars(Object o) throws Exception {
        JSONObject j = new JSONObject();
        if (o == null) return j;
        for (Method m : o.getClass().getMethods()) {
            if (m.getParameterTypes().length != 0 || Modifier.isStatic(m.getModifiers())) continue;
            String n = m.getName();
            if (!(n.startsWith("get") || n.startsWith("is")) || n.equals("getClass")) continue;
            if (n.equals("getSecret")) continue;   // never export the secret
            Class<?> t = m.getReturnType();
            Object v;
            try { v = m.invoke(o); } catch (Throwable e) { continue; }
            if (v == null) { j.put(n, JSONObject.NULL); continue; }
            if (t.isPrimitive() || v instanceof Number || v instanceof Boolean || v instanceof String) {
                if (v instanceof Float && (((Float) v).isNaN() || ((Float) v).isInfinite())) j.put(n, String.valueOf(v));
                else if (v instanceof Double && (((Double) v).isNaN() || ((Double) v).isInfinite())) j.put(n, String.valueOf(v));
                else j.put(n, v);
            } else if (v instanceof Enum) {
                j.put(n, ((Enum<?>) v).name());
            } else if (v instanceof List) {
                JSONArray a = new JSONArray();
                for (Object e : (List<?>) v) a.put(e instanceof Number ? ((Number) e).doubleValue() : String.valueOf(e));
                j.put(n, a);
            }
        }
        return j;
    }
}
