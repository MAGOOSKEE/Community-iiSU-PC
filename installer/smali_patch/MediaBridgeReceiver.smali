.class public final Lcom/iisulauncher/pcbridge/MediaBridgeReceiver;
.super Landroid/content/BroadcastReceiver;
.source "MediaBridgeReceiver.java"


# iiSU-PC MediaBridge v2.
# Explicitly invoked by Manager through adb shell am broadcast.
# Installs one ROM media file with a self-contained safe copy (installAsset,
# no iiSU dependency), then rebuilds that ROM's asset-index entry through
# iiSU's asset-helper class.
#
# iiSU's R8-obfuscated class names change on every rebuild, so patch_iisu.py
# discovers them by structure and substitutes these tokens at patch time:
#   LIISUPC_ASSETS;  iiSU's asset-index helper class (static n/m/o/M/K/I)
#   LIISUPC_HOLDER;  iiSU's PrimaryHomeActions holder (see patch_iisu.py)
# Everything between the IISUPC_ASSET_INDEX markers is dropped when the
# asset helper can't be found, so the receiver still loads and installs
# files (iiSU just won't re-index until its next rescan).

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/content/BroadcastReceiver;-><init>()V
    return-void
.end method

.method private static rescan(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V
    .locals 11

    # IISUPC_ASSET_INDEX_BEGIN
    move-object v0, p0
    move-object v1, p1
    move-object v2, p2

    const-string v9, "icon"
    invoke-static {p3, v9}, LIISUPC_ASSETS;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v9

    const-string v10, "home_icon"
    invoke-static {p3, v10}, LIISUPC_ASSETS;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v10

    const-string v3, "title"
    invoke-static {p3, v3}, LIISUPC_ASSETS;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v3

    const-string v4, "hero"
    invoke-static {p3, v4}, LIISUPC_ASSETS;->m(Ljava/io/File;Ljava/lang/String;)Ljava/util/List;
    move-result-object v4

    const-string v5, "slide"
    invoke-static {p3, v5}, LIISUPC_ASSETS;->m(Ljava/io/File;Ljava/lang/String;)Ljava/util/List;
    move-result-object v5

    const-string v6, "portrait"
    invoke-static {p3, v6}, LIISUPC_ASSETS;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;
    move-result-object v6

    invoke-static {p3}, LIISUPC_ASSETS;->o(Ljava/io/File;)Ljava/io/File;
    move-result-object v7

    const/4 v8, 0x0

    invoke-static {v0, v1, v2, v9}, LIISUPC_ASSETS;->M(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V
    invoke-static {v0, v1, v2, v10}, LIISUPC_ASSETS;->K(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;)V
    invoke-static/range {v0 .. v8}, LIISUPC_ASSETS;->I(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/io/File;Ljava/util/List;Ljava/util/List;Ljava/io/File;Ljava/io/File;Ljava/lang/Long;)V
    # IISUPC_ASSET_INDEX_END

    return-void
.end method


.method private static rescanLibrary()Ljava/lang/String;
    .locals 3

    # LIISUPC_HOLDER;->iisupcHolder is the Hilt-created PrimaryHomeActions
    # object exposed by patch_iisu.py. Its a() returns iiSU's own
    # lifecycle-maintained WeakReference<MainActivity>; MediaBridge does not
    # create another one. The secondary-home path was dropped: its refresh
    # hooks (fields and helper classes) are renamed on every iiSU rebuild and
    # the VM is single-display.
    sget-object v0, LIISUPC_HOLDER;->iisupcHolder:LIISUPC_HOLDER;
    if-eqz v0, :no_activity

    invoke-virtual {v0}, LIISUPC_HOLDER;->a()Lcom/iisulauncher/launcher/MainActivity;
    move-result-object v0
    if-eqz v0, :no_activity

    invoke-virtual {v0}, Landroid/app/Activity;->isFinishing()Z
    move-result v1
    if-nez v1, :main_finishing

    invoke-virtual {v0}, Landroid/app/Activity;->isDestroyed()Z
    move-result v1
    if-nez v1, :main_destroyed

    # MainActivity's own refreshMetadata entry point (a static, stable across
    # every build checked so far; patch_iisu.py warns if it ever disappears).
    invoke-static {v0}, Lcom/iisulauncher/launcher/MainActivity;->v(Lcom/iisulauncher/launcher/MainActivity;)V

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
.end method


.method private static installAsset(Ljava/io/File;Ljava/io/File;Ljava/lang/String;Ljava/lang/String;)Ljava/io/File;
    .locals 8

    # p0 = source file, p1 = asset dir, p2 = slot base name, p3 = extension.
    # Copy to a temp file first, verify it is non-empty, only then delete the
    # old slot files (any extension) and rename into place, so a failed copy
    # never destroys the existing artwork. Returns the final file or null.
    invoke-virtual {p1}, Ljava/io/File;->mkdirs()Z

    new-instance v0, Ljava/io/File;
    new-instance v1, Ljava/lang/StringBuilder;
    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V
    const-string v2, "."
    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1, p2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    const-string v3, ".iisupc.tmp"
    invoke-virtual {v1, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1
    invoke-direct {v0, p1, v1}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {p0}, Ljava/io/File;->toPath()Ljava/nio/file/Path;
    move-result-object v1
    invoke-virtual {v0}, Ljava/io/File;->toPath()Ljava/nio/file/Path;
    move-result-object v3
    const/4 v4, 0x1
    new-array v4, v4, [Ljava/nio/file/CopyOption;
    sget-object v5, Ljava/nio/file/StandardCopyOption;->REPLACE_EXISTING:Ljava/nio/file/StandardCopyOption;
    const/4 v6, 0x0
    aput-object v5, v4, v6
    invoke-static {v1, v3, v4}, Ljava/nio/file/Files;->copy(Ljava/nio/file/Path;Ljava/nio/file/Path;[Ljava/nio/file/CopyOption;)Ljava/nio/file/Path;

    invoke-virtual {v0}, Ljava/io/File;->length()J
    move-result-wide v4
    const-wide/16 v6, 0x0
    cmp-long v4, v4, v6
    if-gtz v4, :copied

    invoke-virtual {v0}, Ljava/io/File;->delete()Z
    const/4 v0, 0x0
    return-object v0

    :copied
    new-instance v1, Ljava/lang/StringBuilder;
    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {v1, p2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1

    invoke-virtual {p1}, Ljava/io/File;->listFiles()[Ljava/io/File;
    move-result-object v3
    if-eqz v3, :old_done
    array-length v4, v3
    const/4 v5, 0x0

    :old_loop
    if-ge v5, v4, :old_done
    aget-object v6, v3, v5
    invoke-virtual {v6}, Ljava/io/File;->getName()Ljava/lang/String;
    move-result-object v7
    invoke-virtual {v7, v1}, Ljava/lang/String;->startsWith(Ljava/lang/String;)Z
    move-result v7
    if-eqz v7, :old_next
    invoke-virtual {v6}, Ljava/io/File;->delete()Z

    :old_next
    add-int/lit8 v5, v5, 0x1
    goto :old_loop

    :old_done
    new-instance v3, Ljava/io/File;
    new-instance v4, Ljava/lang/StringBuilder;
    invoke-direct {v4}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {v4, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v4, p3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v4}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v4
    invoke-direct {v3, p1, v4}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {v0, v3}, Ljava/io/File;->renameTo(Ljava/io/File;)Z
    move-result v4
    if-eqz v4, :rename_failed

    invoke-virtual {v3}, Ljava/io/File;->length()J
    move-result-wide v4
    const-wide/16 v6, 0x0
    cmp-long v4, v4, v6
    if-lez v4, :rename_failed

    return-object v3

    :rename_failed
    invoke-virtual {v0}, Ljava/io/File;->delete()Z
    const/4 v0, 0x0
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

    invoke-static {v11, v10, v9, v8}, Lcom/iisulauncher/pcbridge/MediaBridgeReceiver;->installAsset(Ljava/io/File;Ljava/io/File;Ljava/lang/String;Ljava/lang/String;)Ljava/io/File;
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
