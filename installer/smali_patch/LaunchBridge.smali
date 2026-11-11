.class public final Lcom/iisulauncher/pcbridge/LaunchBridge;
.super Ljava/lang/Object;
.source "LaunchBridge.java"


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method public static launch(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 1
    .param p0, "context"    # Landroid/content/Context;
    .param p1, "intent"    # Landroid/content/Intent;

    const/4 v0, 0x0

    invoke-static {p0, p1, v0}, Lcom/iisulauncher/pcbridge/LaunchBridge;->launch(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V

    return-void
.end method

# Replaces one of iiSU's startActivity calls. The intent goes to the PC bridge
# on a background thread, and the bridge answers whether it took the launch.
# One it hands back (an Android app such as Chrome or the Play Store) is
# started here exactly as iiSU would have, options included, see
# LaunchBridge$1.
.method public static launch(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V
    .locals 2
    .param p0, "context"    # Landroid/content/Context;
    .param p1, "intent"    # Landroid/content/Intent;
    .param p2, "options"    # Landroid/os/Bundle;

    new-instance v0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;

    invoke-direct {v0, p0, p1, p2}, Lcom/iisulauncher/pcbridge/LaunchBridge$1;-><init>(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V

    new-instance v1, Ljava/lang/Thread;

    invoke-direct {v1, v0}, Ljava/lang/Thread;-><init>(Ljava/lang/Runnable;)V

    invoke-virtual {v1}, Ljava/lang/Thread;->start()V

    return-void
.end method

# Opens the connection to the PC bridge. First this device's own port 7737,
# which the bridge points at itself with "adb reverse" every time it starts:
# that works on any Android runtime and whatever port the bridge listens on.
# Then 10.0.2.2, the SDK emulator's name for the PC, for a bridge from before
# it set that route up. 10.0.2.2 exists only inside the SDK emulator, so on
# Waydroid it was the reason no launch ever reached the PC. Both attempts
# give up after a moment, so with no bridge at all the launch falls back to
# Android quickly (see LaunchBridge$1) and iiSU is never left waiting.
.method static connect()Ljava/net/Socket;
    .locals 4

    :try_start_0
    new-instance v0, Ljava/net/Socket;

    invoke-direct {v0}, Ljava/net/Socket;-><init>()V

    new-instance v1, Ljava/net/InetSocketAddress;

    const-string v2, "127.0.0.1"

    const/16 v3, 0x1e39

    invoke-direct {v1, v2, v3}, Ljava/net/InetSocketAddress;-><init>(Ljava/lang/String;I)V

    const/16 v2, 0x3e8

    invoke-virtual {v0, v1, v2}, Ljava/net/Socket;->connect(Ljava/net/SocketAddress;I)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-object v0

    :catch_0
    move-exception v0

    new-instance v0, Ljava/net/Socket;

    invoke-direct {v0}, Ljava/net/Socket;-><init>()V

    new-instance v1, Ljava/net/InetSocketAddress;

    const-string v2, "10.0.2.2"

    const/16 v3, 0x1e39

    invoke-direct {v1, v2, v3}, Ljava/net/InetSocketAddress;-><init>(Ljava/lang/String;I)V

    const/16 v2, 0xbb8

    invoke-virtual {v0, v1, v2}, Ljava/net/Socket;->connect(Ljava/net/SocketAddress;I)V

    return-object v0
.end method

# The startActivity call the patch replaced, made on the main thread.
.method static startOnAndroid(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V
    .locals 2
    .param p0, "context"    # Landroid/content/Context;
    .param p1, "intent"    # Landroid/content/Intent;
    .param p2, "options"    # Landroid/os/Bundle;

    new-instance v0, Landroid/os/Handler;

    invoke-static {}, Landroid/os/Looper;->getMainLooper()Landroid/os/Looper;

    move-result-object v1

    invoke-direct {v0, v1}, Landroid/os/Handler;-><init>(Landroid/os/Looper;)V

    new-instance v1, Lcom/iisulauncher/pcbridge/LaunchBridge$2;

    invoke-direct {v1, p0, p1, p2}, Lcom/iisulauncher/pcbridge/LaunchBridge$2;-><init>(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V

    invoke-virtual {v0, v1}, Landroid/os/Handler;->post(Ljava/lang/Runnable;)Z

    return-void
.end method
