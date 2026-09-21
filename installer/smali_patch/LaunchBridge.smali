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

    new-instance v0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;

    invoke-direct {v0, p1}, Lcom/iisulauncher/pcbridge/LaunchBridge$1;-><init>(Landroid/content/Intent;)V

    new-instance p0, Ljava/lang/Thread;

    invoke-direct {p0, v0}, Ljava/lang/Thread;-><init>(Ljava/lang/Runnable;)V

    invoke-virtual {p0}, Ljava/lang/Thread;->start()V

    return-void
.end method
