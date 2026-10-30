.class Lcom/iisulauncher/pcbridge/LaunchBridge$1;
.super Ljava/lang/Object;
.source "LaunchBridge.java"

.implements Ljava/lang/Runnable;


# instance fields
.field final synthetic val$context:Landroid/content/Context;

.field final synthetic val$intent:Landroid/content/Intent;

.field final synthetic val$options:Landroid/os/Bundle;


# direct methods
.method constructor <init>(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V
    .locals 0

    iput-object p1, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$context:Landroid/content/Context;

    iput-object p2, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$intent:Landroid/content/Intent;

    iput-object p3, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$options:Landroid/os/Bundle;

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# virtual methods
.method public run()V
    .locals 8

    # v7: whether the intent reached the PC bridge. Until it has, a failure
    # (no bridge listening, as in a headless start) means Android launches it.
    const/4 v7, 0x0

    :try_start_0
    iget-object v0, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$intent:Landroid/content/Intent;

    new-instance v1, Ljava/lang/StringBuilder;

    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V

    invoke-virtual {v0}, Landroid/content/Intent;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Landroid/content/Intent;->getClipData()Landroid/content/ClipData;

    move-result-object v2

    if-eqz v2, :cond_1

    invoke-virtual {v2}, Landroid/content/ClipData;->getItemCount()I

    move-result v3

    const/4 v4, 0x0

    :goto_0
    if-ge v4, v3, :cond_1

    invoke-virtual {v2, v4}, Landroid/content/ClipData;->getItemAt(I)Landroid/content/ClipData$Item;

    move-result-object v5

    invoke-virtual {v5}, Landroid/content/ClipData$Item;->getUri()Landroid/net/Uri;

    move-result-object v5

    if-eqz v5, :cond_0

    const-string v6, "\nCLIPURI:"

    invoke-virtual {v1, v6}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v5}, Landroid/net/Uri;->toString()Ljava/lang/String;

    move-result-object v5

    invoke-virtual {v1, v5}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    :cond_0
    add-int/lit8 v4, v4, 0x1

    goto :goto_0

    # Every non-null Intent extra is sent over as "EXTRA:key=value", via
    # String.valueOf() rather than a String-only instanceof check, some
    # emulator front-ends (e.g. GameNative's Steam launch, which sends its
    # app_id as a plain int extra, not a String) would otherwise be
    # silently dropped here and never reach the bridge at all.
    :cond_1
    invoke-virtual {v0}, Landroid/content/Intent;->getExtras()Landroid/os/Bundle;

    move-result-object v2

    if-eqz v2, :cond_3

    invoke-virtual {v2}, Landroid/os/Bundle;->keySet()Ljava/util/Set;

    move-result-object v3

    invoke-interface {v3}, Ljava/util/Set;->iterator()Ljava/util/Iterator;

    move-result-object v3

    :goto_1
    invoke-interface {v3}, Ljava/util/Iterator;->hasNext()Z

    move-result v4

    if-eqz v4, :cond_3

    invoke-interface {v3}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v4

    check-cast v4, Ljava/lang/String;

    invoke-virtual {v2, v4}, Landroid/os/Bundle;->get(Ljava/lang/String;)Ljava/lang/Object;

    move-result-object v5

    if-eqz v5, :cond_2

    const-string v6, "\nEXTRA:"

    invoke-virtual {v1, v6}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v1, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v6, "="

    invoke-virtual {v1, v6}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-static {v5}, Ljava/lang/String;->valueOf(Ljava/lang/Object;)Ljava/lang/String;

    move-result-object v5

    invoke-virtual {v1, v5}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    :cond_2
    goto :goto_1

    # Tells the bridge this iiSU reads its answer (below). One patched before
    # then doesn't, so the bridge starts that iiSU's Android apps itself.
    :cond_3
    const-string v2, "\nBRIDGE_REPLY:1"

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v0

    new-instance v1, Ljava/net/Socket;

    const-string v2, "10.0.2.2"

    const/16 v3, 0x1e39

    invoke-direct {v1, v2, v3}, Ljava/net/Socket;-><init>(Ljava/lang/String;I)V

    invoke-virtual {v1}, Ljava/net/Socket;->getOutputStream()Ljava/io/OutputStream;

    move-result-object v2

    const-string v3, "UTF-8"

    invoke-virtual {v0, v3}, Ljava/lang/String;->getBytes(Ljava/lang/String;)[B

    move-result-object v0

    invoke-virtual {v2, v0}, Ljava/io/OutputStream;->write([B)V

    invoke-virtual {v2}, Ljava/io/OutputStream;->flush()V

    const/4 v7, 0x1

    # Half-close, so the bridge sees the end of the intent, then read its one
    # byte answer: "A" hands the launch back to Android. Anything else, or no
    # answer at all from a bridge too old to send one, means the PC took it.
    invoke-virtual {v1}, Ljava/net/Socket;->shutdownOutput()V

    const/16 v3, 0x2710

    invoke-virtual {v1, v3}, Ljava/net/Socket;->setSoTimeout(I)V

    invoke-virtual {v1}, Ljava/net/Socket;->getInputStream()Ljava/io/InputStream;

    move-result-object v2

    invoke-virtual {v2}, Ljava/io/InputStream;->read()I

    move-result v3

    invoke-virtual {v1}, Ljava/net/Socket;->close()V

    const/16 v4, 0x41

    if-ne v3, v4, :cond_4

    iget-object v4, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$context:Landroid/content/Context;

    iget-object v5, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$intent:Landroid/content/Intent;

    iget-object v6, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$options:Landroid/os/Bundle;

    invoke-static {v4, v5, v6}, Lcom/iisulauncher/pcbridge/LaunchBridge;->startOnAndroid(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V

    :cond_4
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-void

    :catch_0
    move-exception v0

    const-string v1, "LaunchBridge"

    const-string v2, "bridge failed"

    invoke-static {v1, v2, v0}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I

    # Once the intent was sent the PC owns it, so a later failure never
    # launches it a second time on Android.
    if-nez v7, :cond_5

    iget-object v1, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$context:Landroid/content/Context;

    iget-object v2, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$intent:Landroid/content/Intent;

    iget-object v3, p0, Lcom/iisulauncher/pcbridge/LaunchBridge$1;->val$options:Landroid/os/Bundle;

    invoke-static {v1, v2, v3}, Lcom/iisulauncher/pcbridge/LaunchBridge;->startOnAndroid(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V

    :cond_5
    return-void
.end method
