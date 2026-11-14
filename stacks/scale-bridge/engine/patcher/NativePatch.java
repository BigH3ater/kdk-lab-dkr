import org.objectweb.asm.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

/**
 * Build-time only. Rewrites a vendor jar so it runs on a plain JVM:
 *  - every {@code native} method gets a body that calls
 *    {@code kdk.lefu.NativeBridge.invoke(self|null, ownerInternalName, name, desc, Object[] args)}
 *    and unboxes the result;
 *  - every {@code System.loadLibrary(lib)} becomes {@code NativeBridge.loadLibrary(lib, ownerInternalName)}.
 * Field layout, constant pools and all other code stay byte-for-byte as the vendor compiled them.
 * Usage: java -cp asm.jar:. NativePatch in.jar out.jar
 */
public class NativePatch {
    static final String BRIDGE = "kdk/lefu/NativeBridge";

    public static void main(String[] a) throws Exception {
        int patched = 0;
        try (ZipInputStream in = new ZipInputStream(new FileInputStream(a[0]));
             ZipOutputStream out = new ZipOutputStream(new FileOutputStream(a[1]))) {
            for (ZipEntry e; (e = in.getNextEntry()) != null; ) {
                byte[] b = in.readAllBytes();
                if (e.getName().endsWith(".class")) {
                    byte[] p = patch(b);
                    if (p != b) { patched++; System.err.println("patched " + e.getName()); }
                    b = p;
                }
                ZipEntry o = new ZipEntry(e.getName());
                o.setTime(315532800000L);   // reproducible
                out.putNextEntry(o); out.write(b); out.closeEntry();
            }
        }
        System.err.println(patched + " classes patched");
    }

    static byte[] patch(byte[] src) {
        ClassReader cr = new ClassReader(src);
        boolean[] touched = {false};
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS | ClassWriter.COMPUTE_FRAMES) {
            @Override protected String getCommonSuperClass(String t1, String t2) { return "java/lang/Object"; }
        };
        String[] owner = new String[1];
        cr.accept(new ClassVisitor(Opcodes.ASM9, cw) {
            @Override public void visit(int v, int acc, String name, String sig, String sup, String[] itf) {
                owner[0] = name; super.visit(v, acc, name, sig, sup, itf);
            }
            @Override public MethodVisitor visitMethod(int acc, String name, String desc, String sig, String[] ex) {
                if ((acc & Opcodes.ACC_NATIVE) != 0) {
                    touched[0] = true;
                    MethodVisitor mv = super.visitMethod(acc & ~Opcodes.ACC_NATIVE, name, desc, sig, ex);
                    emitBridge(mv, (acc & Opcodes.ACC_STATIC) != 0, owner[0], name, desc);
                    return null;
                }
                MethodVisitor mv = super.visitMethod(acc, name, desc, sig, ex);
                return new MethodVisitor(Opcodes.ASM9, mv) {
                    @Override public void visitMethodInsn(int op, String o, String n, String d, boolean itf) {
                        if (op == Opcodes.INVOKESTATIC && o.equals("java/lang/System") && n.equals("loadLibrary")) {
                            touched[0] = true;
                            super.visitLdcInsn(owner[0]);
                            super.visitMethodInsn(Opcodes.INVOKESTATIC, BRIDGE, "loadLibrary", "(Ljava/lang/String;Ljava/lang/String;)V", false);
                        } else super.visitMethodInsn(op, o, n, d, itf);
                    }
                };
            }
        }, 0);
        return touched[0] ? cw.toByteArray() : src;
    }

    static void emitBridge(MethodVisitor mv, boolean isStatic, String owner, String name, String desc) {
        mv.visitCode();
        if (isStatic) mv.visitInsn(Opcodes.ACONST_NULL); else mv.visitVarInsn(Opcodes.ALOAD, 0);
        mv.visitLdcInsn(owner); mv.visitLdcInsn(name); mv.visitLdcInsn(desc);
        Type[] args = Type.getArgumentTypes(desc);
        mv.visitLdcInsn(args.length);
        mv.visitTypeInsn(Opcodes.ANEWARRAY, "java/lang/Object");
        int slot = isStatic ? 0 : 1;
        for (int i = 0; i < args.length; i++) {
            mv.visitInsn(Opcodes.DUP); mv.visitLdcInsn(i);
            mv.visitVarInsn(args[i].getOpcode(Opcodes.ILOAD), slot);
            box(mv, args[i]);
            mv.visitInsn(Opcodes.AASTORE);
            slot += args[i].getSize();
        }
        mv.visitMethodInsn(Opcodes.INVOKESTATIC, BRIDGE, "invoke",
                "(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;", false);
        Type r = Type.getReturnType(desc);
        switch (r.getSort()) {
            case Type.VOID: mv.visitInsn(Opcodes.POP); mv.visitInsn(Opcodes.RETURN); break;
            case Type.OBJECT: case Type.ARRAY:
                mv.visitTypeInsn(Opcodes.CHECKCAST, r.getInternalName()); mv.visitInsn(Opcodes.ARETURN); break;
            default:
                String w = wrapper(r);
                mv.visitTypeInsn(Opcodes.CHECKCAST, w);
                mv.visitMethodInsn(Opcodes.INVOKEVIRTUAL, w, r.getClassName() + "Value", "()" + r.getDescriptor(), false);
                mv.visitInsn(r.getOpcode(Opcodes.IRETURN));
        }
        mv.visitMaxs(0, 0);
        mv.visitEnd();
    }

    static String wrapper(Type t) {
        switch (t.getSort()) {
            case Type.BOOLEAN: return "java/lang/Boolean"; case Type.BYTE: return "java/lang/Byte";
            case Type.CHAR: return "java/lang/Character"; case Type.SHORT: return "java/lang/Short";
            case Type.INT: return "java/lang/Integer"; case Type.LONG: return "java/lang/Long";
            case Type.FLOAT: return "java/lang/Float"; case Type.DOUBLE: return "java/lang/Double";
            default: return null;
        }
    }

    static void box(MethodVisitor mv, Type t) {
        String w = wrapper(t);
        if (w != null) mv.visitMethodInsn(Opcodes.INVOKESTATIC, w, "valueOf", "(" + t.getDescriptor() + ")L" + w + ";", false);
    }
}
