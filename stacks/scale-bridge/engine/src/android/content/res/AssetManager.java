package android.content.res;

/** Minimal stand-in: open(name) reads from the SDK's extracted assets directory. */
public class AssetManager {
    private final java.io.File dir;
    public AssetManager(java.io.File dir) { this.dir = dir; }
    public java.io.InputStream open(String name) throws java.io.IOException {
        java.io.File f = new java.io.File(dir, name);
        if (!f.getCanonicalPath().startsWith(dir.getCanonicalPath() + java.io.File.separator)) throw new java.io.FileNotFoundException(name);
        return new java.io.FileInputStream(f);
    }
}
