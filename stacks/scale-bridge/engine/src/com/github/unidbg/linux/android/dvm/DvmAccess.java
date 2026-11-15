package com.github.unidbg.linux.android.dvm;

/** Same-package accessor for unidbg's package-private DvmClass.getField(int) (used by the GetDoubleField slot). */
public final class DvmAccess {
    private DvmAccess() {}
    public static DvmField field(DvmClass c, int id) { return c.getField(id); }
}
