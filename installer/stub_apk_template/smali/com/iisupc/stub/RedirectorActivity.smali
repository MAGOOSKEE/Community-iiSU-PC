.class public Lcom/iisupc/stub/RedirectorActivity;
.super Landroid/app/Activity;
.source "RedirectorActivity.smali"

# Never actually reached in normal operation -- the patched LaunchBridge
# intercepts the ROM-launch Intent before Android would start this
# Activity at all. It exists purely so iiSU's own "is this emulator
# installed" check finds a real, installed package to resolve to (see
# shared/emulator_defaults.py). If it is ever reached (the interception
# missed, or someone taps this app's icon directly), it does nothing and
# closes immediately rather than showing a blank/crashing screen.

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Activity;-><init>()V
    return-void
.end method

.method protected onCreate(Landroid/os/Bundle;)V
    .locals 0
    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V
    invoke-virtual {p0}, Lcom/iisupc/stub/RedirectorActivity;->finish()V
    return-void
.end method
