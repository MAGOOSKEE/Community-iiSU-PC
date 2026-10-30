.class public Lcom/iisupc/stub/RedirectorActivity;
.super Landroid/app/Activity;
.source "RedirectorActivity.smali"

# Stands in for this emulator on Android while the game runs on the PC.
# iiSU's launch never reaches here (the patched LaunchBridge sends it to the
# PC instead), so iiSU never left the screen and never got the return to
# the foreground that restores its music and interface after a game. The
# launch bridge therefore opens this with the HOLD extra when a PC game
# starts, covering iiSU just as a real Android emulator would, and opens it
# again without HOLD when the game ends: it closes, and Android brings iiSU
# back. Opened any other way (its icon tapped), it closes at once.

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Activity;-><init>()V
    return-void
.end method

.method protected onCreate(Landroid/os/Bundle;)V
    .locals 1
    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V
    invoke-virtual {p0}, Lcom/iisupc/stub/RedirectorActivity;->getIntent()Landroid/content/Intent;
    move-result-object v0
    invoke-direct {p0, v0}, Lcom/iisupc/stub/RedirectorActivity;->holdOrClose(Landroid/content/Intent;)V
    return-void
.end method

# singleTask: the bridge's second start reaches the open one here.
.method protected onNewIntent(Landroid/content/Intent;)V
    .locals 0
    invoke-super {p0, p1}, Landroid/app/Activity;->onNewIntent(Landroid/content/Intent;)V
    invoke-virtual {p0, p1}, Lcom/iisupc/stub/RedirectorActivity;->setIntent(Landroid/content/Intent;)V
    invoke-direct {p0, p1}, Lcom/iisupc/stub/RedirectorActivity;->holdOrClose(Landroid/content/Intent;)V
    return-void
.end method

.method private holdOrClose(Landroid/content/Intent;)V
    .locals 2
    if-eqz p1, :close
    const-string v0, "com.iisupc.stub.HOLD"
    const/4 v1, 0x0
    invoke-virtual {p1, v0, v1}, Landroid/content/Intent;->getBooleanExtra(Ljava/lang/String;Z)Z
    move-result v0
    if-eqz v0, :close
    return-void
    :close
    invoke-virtual {p0}, Lcom/iisupc/stub/RedirectorActivity;->finish()V
    return-void
.end method
