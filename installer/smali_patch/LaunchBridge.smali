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
