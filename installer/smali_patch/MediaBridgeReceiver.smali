.class public final Lcom/iisulauncher/pcbridge/MediaBridgeReceiver;
.super Landroid/content/BroadcastReceiver;
.source "MediaBridgeReceiver.java"


# iiSU-PC MediaBridge v1.
# Explicitly invoked by Manager through adb shell am broadcast.
# Installs one ROM media file through iiSU's own qa5.k() safe-copy helper,
# then rebuilds that ROM's asset-index entry through wy6's public helpers.

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/content/BroadcastReceiver;-><init>()V
    return-void
.end method

.method private static rescan(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V
    .locals 11

    move-object v0, p0
    move-object v1, p1
    move-object v2, p2

    const-string v9, "icon"
    invoke-static {p3, v9}, Lwy6;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v9

    const-string v10, "home_icon"
    invoke-static {p3, v10}, Lwy6;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v10

    const-string v3, "title"
    invoke-static {p3, v3}, Lwy6;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v3

    const-string v4, "hero"
    invoke-static {p3, v4}, Lwy6;->m(Ljava/io/File;Ljava/lang/String;)Ljava/util/List;
    move-result-object v4

    const-string v5, "slide"
    invoke-static {p3, v5}, Lwy6;->m(Ljava/io/File;Ljava/lang/String;)Ljava/util/List;
    move-result-object v5

    const-string v6, "portrait"
    invoke-static {p3, v6}, Lwy6;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v6

    invoke-static {p3}, Lwy6;->o(Ljava/io/File;)Ljava/io/File;
    move-result-object v7

    const/4 v8, 0x0

    invoke-static {v0, v1, v2, v9}, Lwy6;->M(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V
    invoke-static {v0, v1, v2, v10}, Lwy6;->K(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V
    invoke-static/range {v0 .. v8}, Lwy6;->I(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;Ljava/util/List;Ljava/util/List;Ljava/io/File;Ljava/io/File;Ljava/lang/Long;)V

    return-void
.end method


.method private static rescanLibrary()Ljava/lang/String;
    .locals 3

    # Primary-home path. je6.c is the Hilt-created PrimaryHomeActions object
    # exposed by patch_iisu.py. je6.a() returns iiSU's own lifecycle-maintained
    # WeakReference<MainActivity>; MediaBridge does not create another one.
    sget-object v0, Lje6;->c:Lje6;
    if-eqz v0, :secondary_fallback

    invoke-virtual {v0}, Lje6;->a()Lcom/iisulauncher/launcher/MainActivity;
    move-result-object v0
    if-eqz v0, :secondary_fallback

    invoke-virtual {v0}, Landroid/app/Activity;->isFinishing()Z
    move-result v1
    if-nez v1, :main_finishing

    invoke-virtual {v0}, Landroid/app/Activity;->isDestroyed()Z
    move-result v1
    if-nez v1, :main_destroyed

    # Exact MainActivity refreshMetadata target:
    # n35 selector 3 -> MainActivity.v(MainActivity).
    invoke-static {v0}, Lcom/iisulauncher/launcher/MainActivity;->v(Lcom/iisulauncher/launcher/MainActivity;)V

    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_STARTED_V1"
    return-object v0

    # Keep the already-proven SecondaryHomeActivity path as a fallback for
    # iiSU configurations that use the secondary home.
    :secondary_fallback
    sget-object v0, Lcom/iisulauncher/launcher/SecondaryHomeActivity;->g0:Ljava/lang/ref/WeakReference;
    if-eqz v0, :no_activity

    invoke-virtual {v0}, Ljava/lang/ref/WeakReference;->get()Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Lcom/iisulauncher/launcher/SecondaryHomeActivity;
    if-eqz v0, :no_activity

    sget-boolean v1, Lcom/iisulauncher/launcher/SecondaryHomeActivity;->h0:Z
    if-eqz v1, :secondary_not_resumed

    invoke-virtual {v0}, Landroid/app/Activity;->isFinishing()Z
    move-result v1
    if-nez v1, :secondary_finishing

    invoke-virtual {v0}, Landroid/app/Activity;->isDestroyed()Z
    move-result v1
    if-nez v1, :secondary_destroyed

    # Exact compiled SecondaryHomeActivity refreshMetadata body
    # (th7 selector 1).
    iget-object v1, v0, Lcom/iisulauncher/launcher/SecondaryHomeActivity;->O:Ljv;
    invoke-virtual {v1}, Ljv;->getValue()Ljava/lang/Object;
    move-result-object v1
    check-cast v1, Lxi0;
    invoke-virtual {v1}, Lxi0;->U()V

    iget-object v1, v0, Lcom/iisulauncher/launcher/SecondaryHomeActivity;->S:Ljv;
    invoke-virtual {v1}, Ljv;->getValue()Ljava/lang/Object;
    move-result-object v1
    check-cast v1, Lxw6;
    invoke-static {v1}, Lxw6;->e(Lxw6;)V

    sget-object v1, Lwy6;->a:Lwy6;
    invoke-virtual {v0}, Landroid/content/Context;->getApplicationContext()Landroid/content/Context;
    move-result-object v1
    invoke-virtual {v1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;
    invoke-static {v1}, Lwy6;->g(Landroid/content/Context;)V

    sget-object v1, Lzj;->a:Ln91;
    invoke-virtual {v0}, Landroid/content/Context;->getApplicationContext()Landroid/content/Context;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/Object;->getClass()Ljava/lang/Class;
    invoke-static {v0}, Lzj;->b(Landroid/content/Context;)V

    invoke-static {}, Laz6;->d()V
    invoke-static {}, Lbn;->c()V
    sget-object v0, Llq;->a:Lhz7;
    invoke-static {}, Llq;->a()V

    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_STARTED_V1"
    return-object v0

    :no_activity
    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:NO_ACTIVITY"
    return-object v0

    :main_finishing
    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:ACTIVITY_FINISHING"
    return-object v0

    :main_destroyed
    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:ACTIVITY_DESTROYED"
    return-object v0

    :secondary_not_resumed
    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:ACTIVITY_NOT_RESUMED"
    return-object v0

    :secondary_finishing
    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:ACTIVITY_FINISHING"
    return-object v0

    :secondary_destroyed
    const-string v0, "IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:ACTIVITY_DESTROYED"
    return-object v0
.end method


.method public onReceive(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 12

    const-string v0, "MediaBridge"

    :try_start_0
    invoke-virtual {p2}, Landroid/content/Intent;->getAction()Ljava/lang/String;
    move-result-object v2

    # Harmless capability probe used by iiSU-PC Manager/Diagnostics.
    const-string v1, "com.iisulauncher.pcbridge.PING"
    invoke-virtual {v1, v2}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v1
    if-eqz v1, :check_rescan_action

    const/4 v1, 0x1
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultCode(I)V

    const-string v1, "IISUPC_MEDIABRIDGE_READY_V1"
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V

    const-string v1, "PING -> IISUPC_MEDIABRIDGE_READY_V1"
    invoke-static {v0, v1}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    return-void

    :check_rescan_action
    const-string v1, "com.iisulauncher.pcbridge.RESCAN_LIBRARY"
    invoke-virtual {v1, v2}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v1
    if-eqz v1, :check_asset_install_action

    invoke-static {}, Lcom/iisulauncher/pcbridge/MediaBridgeReceiver;->rescanLibrary()Ljava/lang/String;
    move-result-object v1

    const-string v2, "IISUPC_MEDIABRIDGE_RESCAN_STARTED_V1"
    invoke-virtual {v2, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :rescan_failed

    const/4 v2, 0x1
    invoke-virtual {p0, v2}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    invoke-static {v0, v1}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    return-void

    :rescan_failed
    const/4 v2, 0x2
    invoke-virtual {p0, v2}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    invoke-static {v0, v1}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I
    return-void

    :check_asset_install_action
    const-string v1, "com.iisulauncher.pcbridge.INSTALL_ROM_ASSET"
    invoke-virtual {v1, v2}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v1
    if-nez v1, :action_ok

    const-string v1, "Ignoring unknown action"
    invoke-static {v0, v1}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I
    return-void

    :action_ok
    const-string v1, "tab_id"
    invoke-virtual {p2, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v3

    const-string v1, "rom_id"
    invoke-virtual {p2, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v4

    const-string v1, "asset_dir"
    invoke-virtual {p2, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v5

    const-string v1, "source"
    invoke-virtual {p2, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v6

    const-string v1, "asset_type"
    invoke-virtual {p2, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v7

    const-string v1, "extension"
    invoke-virtual {p2, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v8

    if-eqz v3, :missing
    if-eqz v4, :missing
    if-eqz v5, :missing
    if-eqz v6, :missing
    if-eqz v7, :missing
    if-eqz v8, :missing

    # Stable Manager asset names -> iiSU's on-disk slot base names.
    const-string v1, "hero"
    invoke-virtual {v7, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :check_screenshot
    const-string v9, "hero_1"
    goto :slot_ready

    :check_screenshot
    const-string v1, "screenshot"
    invoke-virtual {v7, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :check_title
    const-string v9, "slide_1"
    goto :slot_ready

    :check_title
    const-string v1, "title"
    invoke-virtual {v7, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :check_icon
    const-string v9, "title"
    goto :slot_ready

    :check_icon
    const-string v1, "icon"
    invoke-virtual {v7, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :check_home_icon
    const-string v9, "icon"
    goto :slot_ready

    :check_home_icon
    const-string v1, "home_icon"
    invoke-virtual {v7, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :check_soundbite
    const-string v9, "home_icon"
    goto :slot_ready

    :check_soundbite
    const-string v1, "soundbite"
    invoke-virtual {v7, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :unsupported
    const-string v9, "music"

    :slot_ready
    new-instance v10, Ljava/io/File;
    invoke-direct {v10, v5}, Ljava/io/File;-><init>(Ljava/lang/String;)V

    new-instance v11, Ljava/io/File;
    invoke-direct {v11, v6}, Ljava/io/File;-><init>(Ljava/lang/String;)V
    invoke-static {v11}, Landroid/net/Uri;->fromFile(Ljava/io/File;)Landroid/net/Uri;
    move-result-object v11

    # qa5.k performs iiSU's temp-copy, non-empty verification, old-slot
    # cleanup, rename/copy fallback, final verification, and timestamp update.
    invoke-static {p1, v11, v10, v9, v8}, Lqa5;->k(Landroid/content/Context;Landroid/net/Uri;Ljava/io/File;Ljava/lang/String;Ljava/lang/String;)Ljava/io/File;
    move-result-object v11
    if-eqz v11, :copy_failed

    invoke-static {}, Ljava/lang/System;->currentTimeMillis()J
    move-result-wide v1
    invoke-virtual {v10, v1, v2}, Ljava/io/File;->setLastModified(J)Z

    invoke-static {p1, v3, v4, v10}, Lcom/iisulauncher/pcbridge/MediaBridgeReceiver;->rescan(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V

    new-instance v1, Ljava/lang/StringBuilder;
    const-string v2, "Installed "
    invoke-direct {v1, v2}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V
    invoke-virtual {v1, v7}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    const-string v2, " for "
    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1
    invoke-static {v0, v1}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v1, 0x1
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    const-string v1, "IISUPC_MEDIABRIDGE_INSTALLED_V1"
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    return-void

    :missing
    const-string v1, "Missing required extras"
    invoke-static {v0, v1}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v1, 0x2
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    const-string v1, "IISUPC_MEDIABRIDGE_ERROR_V1:MISSING_EXTRAS"
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    return-void

    :unsupported
    const-string v1, "Unsupported asset_type"
    invoke-static {v0, v1}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v1, 0x2
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    const-string v1, "IISUPC_MEDIABRIDGE_ERROR_V1:INVALID_ASSET_TYPE"
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    return-void

    :copy_failed
    const-string v1, "iiSU media copy failed"
    invoke-static {v0, v1}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v1, 0x2
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    const-string v1, "IISUPC_MEDIABRIDGE_ERROR_V1:INSTALL_FAILED"
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    return-void

    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_all

    :catch_all
    move-exception v1
    const-string v2, "MediaBridge action failed"
    invoke-static {v0, v2, v1}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I
    const/4 v1, 0x2
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultCode(I)V
    const-string v1, "IISUPC_MEDIABRIDGE_ERROR_V1:EXCEPTION"
    invoke-virtual {p0, v1}, Landroid/content/BroadcastReceiver;->setResultData(Ljava/lang/String;)V
    return-void
.end method
