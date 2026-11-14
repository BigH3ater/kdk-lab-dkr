package android.content;

/** Minimal stand-in for Android's Context: the Lefu jars only call getAssets(). */
public class Context {
    private final android.content.res.AssetManager assets;
    public Context(java.io.File assetDir) { this.assets = new android.content.res.AssetManager(assetDir); }
    public android.content.res.AssetManager getAssets() { return assets; }
    public String getPackageName() { return System.getenv().getOrDefault("LEFU_PACKAGE", "com.lefu.demo"); }
}
