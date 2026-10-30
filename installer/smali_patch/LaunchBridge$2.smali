.class final Lcom/iisulauncher/pcbridge/LaunchBridge$2;
.super Ljava/lang/Object;
.source "LaunchBridge.java"

# interfaces
.implements Ljava/lang/Runnable;


# instance fields
.field final synthetic val$context:Landroid/content/Context;

.field final synthetic val$intent:Landroid/content/Intent;

.field final synthetic val$options:Landroid/os/Bundle;


# direct methods
.method constructor <init>(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$2;->val$context:Landroid/content/Context;

    iput-object p2, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$2;->val$intent:Landroid/content/Intent;

    iput-object p3, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$2;->val$options:Landroid/os/Bundle;

    return-void
.end method


# virtual methods
.method public run()V
    .locals 4

    iget-object v0, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$2;->val$context:Landroid/content/Context;

    iget-object v1, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$2;->val$intent:Landroid/content/Intent;

    iget-object v2, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$2;->val$options:Landroid/os/Bundle;

    :try_start_0
    if-eqz v2, :cond_0

    invoke-virtual {v0, v1, v2}, Landroid/content/Context;->startActivity(Landroid/content/Intent;Landroid/os/Bundle;)V

    goto :goto_0

    :cond_0
    invoke-virtual {v0, v1}, Landroid/content/Context;->startActivity(Landroid/content/Intent;)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    :goto_0
    return-void

    :catch_0
    move-exception v3

    const-string v0, "LaunchBridge"

    const-string v1, "Android launch failed"

    invoke-static {v0, v1, v3}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I

    return-void
.end method
