package kdk.lefu;

import com.github.unidbg.linux.android.dvm.*;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;

/**
 * The fake Java side the native engine talks to.
 * - Field get/set on any object: forwarded by reflection to the real JVM object behind the DvmObject.
 * - android.app.ActivityThread.currentApplication().getPackageName(): answers LEFU_PACKAGE
 *   (default com.lefu.demo, the package the demo secret is issued for).
 * Anything else the native code asks for fails loudly (AbstractJni throws UnsupportedOperationException).
 */
public class EngineJni extends AbstractJni {
    static String packageName() { return System.getenv().getOrDefault("LEFU_PACKAGE", "com.lefu.demo"); }

    static final class App {}   // value behind the fake android.app.Application

    static Object wrap(VM vm, Object v) {
        if (v == null || v instanceof Number || v instanceof Boolean || v instanceof Character) return v;
        if (v instanceof String) return new StringObject(vm, (String) v);
        return vm.resolveClass(v.getClass().getName().replace('.', '/')).newObject(v);
    }

    // ---- package-name check ----
    private DvmObject<?> staticObj(BaseVM vm, String sig) {
        if (sig.equals("android/app/ActivityThread->currentApplication()Landroid/app/Application;"))
            return vm.resolveClass("android/app/Application").newObject(new App());
        return null;
    }
    private DvmObject<?> obj(BaseVM vm, DvmObject<?> o, String sig) {
        if (o.getValue() instanceof App && sig.endsWith("->getPackageName()Ljava/lang/String;"))
            return new StringObject(vm, packageName());
        return null;
    }
    @Override public DvmObject<?> callStaticObjectMethod(BaseVM vm, DvmClass c, String sig, VarArg a) {
        DvmObject<?> r = staticObj(vm, sig); return r != null ? r : super.callStaticObjectMethod(vm, c, sig, a); }
    @Override public DvmObject<?> callStaticObjectMethodV(BaseVM vm, DvmClass c, String sig, VaList a) {
        DvmObject<?> r = staticObj(vm, sig); return r != null ? r : super.callStaticObjectMethodV(vm, c, sig, a); }
    @Override public DvmObject<?> callObjectMethod(BaseVM vm, DvmObject<?> o, String sig, VarArg a) {
        DvmObject<?> r = obj(vm, o, sig); return r != null ? r : super.callObjectMethod(vm, o, sig, a); }
    @Override public DvmObject<?> callObjectMethodV(BaseVM vm, DvmObject<?> o, String sig, VaList a) {
        DvmObject<?> r = obj(vm, o, sig); return r != null ? r : super.callObjectMethodV(vm, o, sig, a); }

    // ---- reflective field bridge ----
    private static Field field(Object target, String sig) {
        String name = sig.substring(sig.indexOf("->") + 2, sig.indexOf(':'));
        for (Class<?> c = target.getClass(); c != null; c = c.getSuperclass()) {
            try { Field f = c.getDeclaredField(name); if (!Modifier.isStatic(f.getModifiers())) { f.setAccessible(true); return f; } }
            catch (NoSuchFieldException ignored) { }
        }
        throw new UnsupportedOperationException("no field " + sig);
    }
    private static Object real(DvmObject<?> o, String sig) {
        Object v = o.getValue();
        if (v == null || v instanceof App) throw new UnsupportedOperationException(sig);
        return v;
    }
    private static Object get(DvmObject<?> o, String sig) {
        try { Object t = real(o, sig); return field(t, sig).get(t); } catch (IllegalAccessException e) { throw new IllegalStateException(e); }
    }
    private static void set(DvmObject<?> o, String sig, Object v) {
        try { Object t = real(o, sig); field(t, sig).set(t, v); } catch (IllegalAccessException e) { throw new IllegalStateException(e); }
    }

    static double getDouble(DvmObject<?> o, String sig) { return (Double) get(o, sig); }

    @Override public int getIntField(BaseVM vm, DvmObject<?> o, String s) { return (Integer) get(o, s); }
    @Override public long getLongField(BaseVM vm, DvmObject<?> o, String s) { return (Long) get(o, s); }
    @Override public float getFloatField(BaseVM vm, DvmObject<?> o, String s) { return (Float) get(o, s); }
    @Override public boolean getBooleanField(BaseVM vm, DvmObject<?> o, String s) { return (Boolean) get(o, s); }
    @Override public byte getByteField(BaseVM vm, DvmObject<?> o, String s) { return (Byte) get(o, s); }
    @Override public DvmObject<?> getObjectField(BaseVM vm, DvmObject<?> o, String s) {
        Object v = get(o, s); return v == null ? null : (DvmObject<?>) wrap((VM) vm, v); }
    @Override public void setIntField(BaseVM vm, DvmObject<?> o, String s, int v) { set(o, s, v); }
    @Override public void setLongField(BaseVM vm, DvmObject<?> o, String s, long v) { set(o, s, v); }
    @Override public void setFloatField(BaseVM vm, DvmObject<?> o, String s, float v) { set(o, s, v); }
    @Override public void setDoubleField(BaseVM vm, DvmObject<?> o, String s, double v) { set(o, s, v); }
    @Override public void setBooleanField(BaseVM vm, DvmObject<?> o, String s, boolean v) { set(o, s, v); }
    @Override public void setObjectField(BaseVM vm, DvmObject<?> o, String s, DvmObject<?> v) { set(o, s, v == null ? null : v.getValue()); }
}
