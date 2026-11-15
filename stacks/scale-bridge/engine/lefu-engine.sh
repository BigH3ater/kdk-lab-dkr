#!/bin/sh
# lefu-engine: the Lefu 8-electrode body-composition engine (stacks/scale-bridge/README.md).
# The bridge runs it once per weigh-in as `lefu-engine --case -` with LEFU_SECRET_8 in this
# process's environment only. Usage of the other modes: engine/src/kdk/lefu/Main.java.
# -XX:-UsePerfData: no /tmp/hsperfdata file. java.io.tmpdir=/tmp: unidbg's guest rootfs
# (data only; /tmp is noexec). Native libraries load from /opt/lefu (read-only image), never /tmp.
exec /opt/jre/bin/java -XX:+UseSerialGC -XX:TieredStopAtLevel=1 -XX:-UsePerfData -XX:+ExitOnOutOfMemoryError \
  -Xss4m -Xmx"${LEFU_XMX:-160m}" \
  -Djava.io.tmpdir=/tmp -Djava.library.path=/opt/lefu/native -Djna.boot.library.path=/opt/lefu/native -Djna.nosys=true \
  -cp "/opt/lefu/res:/opt/lefu/lib/*" kdk.lefu.Main "$@"
