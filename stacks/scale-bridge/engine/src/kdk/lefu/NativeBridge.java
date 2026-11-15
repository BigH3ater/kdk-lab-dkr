package kdk.lefu;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.*;
import com.github.unidbg.Emulator;
import com.github.unidbg.arm.Arm64Svc;
import com.github.unidbg.arm.context.RegisterContext;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.pointer.UnidbgPointer;
import unicorn.Arm64Const;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

import java.io.File;
import java.util.HashMap;
import java.util.Map;

/**
 * Target of the patched vendor classes. Each vendor class's {@code System.loadLibrary(x)} is routed to
 * {@link #loadLibrary}; each of its {@code native} methods to {@link #invoke}. Every native library gets
 * its own emulated arm64 Android process (unidbg), so libraries cannot interpose each other's symbols.
 * The Java object the native code works on is the real vendor object: JNI field reads/writes go to it
 * by reflection (see {@link EngineJni}).
 */
public final class NativeBridge {
    private NativeBridge() {}

    static final File LIB_DIR = new File(System.getenv().getOrDefault("LEFU_LIB_DIR", "/opt/lefu/jni/arm64-v8a"));
    private static final Map<String, String> CLASS_TO_LIB = new HashMap<>();
    private static final Map<String, Proc> PROCS = new HashMap<>();

    static final class Proc {
        final AndroidEmulator emu; final VM vm;
        Proc(String lib) {
            com.github.unidbg.EmulatorBuilder<AndroidEmulator> b = AndroidEmulatorBuilder.for64Bit().setProcessName(EngineJni.packageName());
            String backend = System.getenv().getOrDefault("LEFU_BACKEND", "unicorn2");   // unicorn2 | unicorn
            if (backend.equals("unicorn2")) b.addBackendFactory(new com.github.unidbg.arm.backend.Unicorn2Factory(false));
            else if (!backend.equals("unicorn")) throw new IllegalArgumentException("LEFU_BACKEND must be unicorn2 or unicorn");
            emu = b.build();
            Memory mem = emu.getMemory();
            mem.setLibraryResolver(new AndroidResolver(23));
            vm = emu.createDalvikVM();
            vm.setVerbose(false);
            vm.setJni(new EngineJni());
            installGetDoubleField(emu, vm);
            File so = new File(LIB_DIR, "lib" + lib + ".so");
            DalvikModule dm = vm.loadLibrary(so, false);
            if (dm.getModule().findSymbolByName("JNI_OnLoad", false) != null) dm.callJNI_OnLoad(emu);
        }
    }

    /**
     * unidbg 0.9.8 leaves JNIEnv slot 103 (GetDoubleField) unimplemented (UnsupportedOperationException);
     * the engine reads one double field (bhSmoothlyK). Install a handler modelled on unidbg's own GetFloatField.
     */
    static void installGetDoubleField(AndroidEmulator emu, VM vm) {
        UnidbgPointer fn = emu.getSvcMemory().registerSvc(new Arm64Svc() {
            @Override public long handle(Emulator<?> e) {
                RegisterContext ctx = e.getContext();
                DvmObject<?> obj = ((BaseVM) vm).getObject(ctx.getPointerArg(1).toIntPeer());
                DvmClass cls = obj.getObjectType();
                DvmField f = DvmAccess.field(cls, ctx.getPointerArg(2).toIntPeer());
                double v = EngineJni.getDouble(obj, cls.getClassName() + "->" + f.getFieldName() + ":" + f.getFieldType());
                ByteBuffer b = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
                b.putDouble(v);
                e.getBackend().reg_write_vector(Arm64Const.UC_ARM64_REG_Q0, b.array());
                return ctx.getLongArg(0);
            }
        });
        vm.getJNIEnv().getPointer(0).setPointer(103L * 8, fn);
    }

    /** Called from the vendor class's static initialiser instead of System.loadLibrary. */
    public static synchronized void loadLibrary(String lib, String ownerInternalName) {
        CLASS_TO_LIB.put(ownerInternalName, lib);
    }

    private static synchronized Proc proc(String owner) {
        String lib = CLASS_TO_LIB.get(owner);
        if (lib == null) throw new UnsatisfiedLinkError("no library registered for " + owner);
        return PROCS.computeIfAbsent(lib, Proc::new);
    }

    /** Body of every patched native method. */
    public static Object invoke(Object self, String owner, String name, String desc, Object[] args) {
        Proc p = proc(owner);
        synchronized (p) {
            Quiet q = Quiet.on();
            try {
                DvmClass cls = p.vm.resolveClass(owner);
                Object[] jargs = new Object[args.length];
                for (int i = 0; i < args.length; i++) jargs[i] = EngineJni.wrap(p.vm, args[i]);
                char r = desc.charAt(desc.indexOf(')') + 1);
                String m = name + desc;
                if (self == null) {
                    switch (r) {
                        case 'V': cls.callStaticJniMethod(p.emu, m, jargs); return null;
                        case 'Z': return cls.callStaticJniMethodBoolean(p.emu, m, jargs);
                        case 'I': return cls.callStaticJniMethodInt(p.emu, m, jargs);
                        case 'J': return cls.callStaticJniMethodLong(p.emu, m, jargs);
                        case 'L': case '[': { DvmObject<?> o = cls.callStaticJniMethodObject(p.emu, m, jargs); return o == null ? null : o.getValue(); }
                        default: throw new UnsupportedOperationException("return type " + r + " of " + owner + "." + m);
                    }
                }
                DvmObject<?> obj = cls.newObject(self);
                switch (r) {
                    case 'V': obj.callJniMethod(p.emu, m, jargs); return null;
                    case 'Z': return obj.callJniMethodBoolean(p.emu, m, jargs);
                    case 'I': return obj.callJniMethodInt(p.emu, m, jargs);
                    case 'J': return obj.callJniMethodLong(p.emu, m, jargs);
                    case 'L': case '[': { DvmObject<?> o = obj.callJniMethodObject(p.emu, m, jargs); return o == null ? null : o.getValue(); }
                    default: throw new UnsupportedOperationException("return type " + r + " of " + owner + "." + m);
                }
            } finally {
                q.close();
            }
        }
    }

    /** Silences System.out/err while emulated code runs (unidbg prints guest log output there). */
    static final class Quiet implements AutoCloseable {
        static final boolean DEBUG = "1".equals(System.getenv("LEFU_DEBUG"));
        static final java.io.PrintStream NULL = new java.io.PrintStream(java.io.OutputStream.nullOutputStream());
        final java.io.PrintStream out = System.out, err = System.err;
        static Quiet on() { Quiet q = new Quiet(); if (!DEBUG) { System.setOut(NULL); System.setErr(NULL); } return q; }
        @Override public void close() { System.setOut(out); System.setErr(err); }
    }
}
