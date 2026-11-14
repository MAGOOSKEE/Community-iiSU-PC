# Troubleshooting

[Back to the README](../README.md) · See also: [Using it](using.md)

- [Start here: Diagnostics](#start-here-diagnostics)
- [Logs](#logs)
- [Common problems](#common-problems)
- [If the settings file is damaged](#if-the-settings-file-is-damaged)
- [If an iiSU update breaks the patch](#if-an-iisu-update-breaks-the-patch)
- [Seeing and changing the VM's storage](#seeing-and-changing-the-vms-storage)
- [If the VM runs out of internal storage](#if-the-vm-runs-out-of-internal-storage)
- [Reporting a problem](#reporting-a-problem)

## Start here: Diagnostics

Run the Manager's **Backup & Diagnostics > Diagnostics** page first. It is a non-destructive check of the installation, configuration, Android VM/ADB, bridge, PC apps, Steam integration, and logs, and its findings usually say what to do next.

## Logs

Open them from the Manager's Home page: **Show Logs** for the drawer, or the ▾ next to Start for the logs folder. They all live in `bridge/`.

| File | Covers |
|---|---|
| `start.log` | The last start, however it was begun (including from the desktop shortcut, which has no window to show it in) |
| `emulator.log` | The Android VM |
| `bridge.log` | The launch bridge |
| `stop.log` | The last shutdown |
| `setup.log` | Setup, kept even if the window is closed partway |
| `manager_debug.log`, `bridge_debug.log` | Manager and launch-bridge diagnostics, including uncaught Python exceptions that might otherwise disappear when a console closes |

The start and Setup logs open with the PC's details (Windows or Linux version, processors, memory, free space on the install's drive). Both report what hardware acceleration is available and in use: the processor, whether virtualization and a hypervisor are active, the emulator's own acceleration check, and from the start the accelerator, graphics card and memory the emulator actually used and whether it resumed its snapshot. Setup's log also says how long each step took, and every line of `start.log`, `bridge.log` and `stop.log` carries the time it was written.

## Common problems

| Symptom | Try |
|---|---|
| Setup fails early, or the VM never boots | Check CPU virtualization is turned on (see [Requirements](installing.md#requirements)); `setup.log` and `start.log` report what acceleration they found |
| A game doesn't launch | Check the console's mapping in **Settings > PC Emulators** and that the emulator is inside one of your search folders; `bridge.log` shows what the bridge looked for |
| New games don't show up in iiSU | Stop and Start (the library syncs on every start), then "Rescan full library" in iiSU's Library settings |
| Crackling or missing audio | Try other audio and GPU options in a duplicate [VM profile](using.md#vm-profiles) |
| iiSU crashes at startup or while scanning | See [If the VM runs out of internal storage](#if-the-vm-runs-out-of-internal-storage) |
| The Manager says the settings are damaged | See [If the settings file is damaged](#if-the-settings-file-is-damaged) |

Re-running Setup is safe: it skips anything already done and won't overwrite an existing `config.json`'s settings.

## If the settings file is damaged

If `bridge/config.json` gets damaged (a power cut or a full drive while saving, or a hand edit gone wrong), the Manager's Home page says so and shows **Repair Settings**: restore the settings from your last successful save (kept as `config.json.bak`), or reset to defaults, after which you choose your ROM folder and emulator folders again. The damaged file is kept next to it as `config.damaged-<date>.json` either way, and your Android VM, iiSU and games are untouched.

## If an iiSU update breaks the patch

The patch is anchored on specific log strings in iiSU's code. If iiSU updates and changes them, the patch fails loudly instead of silently producing a broken build. Use **Updates > Check an APK...** to see which hooks a new APK still has before you update (see [Updates](using.md#updates)).

The Media Library's MediaBridge hooks find iiSU's obfuscated helper classes by method signature at patch time; if that fails the patch still completes with a warning and media is installed without re-indexing.

## Seeing and changing the VM's storage

**Settings > Storage** shows what is using the VM's internal storage as a colour-coded bar (iiSU's title art and media, iiSU itself, Google Play and Chrome, other apps, other files, Android itself, and free space), how big the VM's file is on your drive, and any old copies left by earlier resizes (with a button to delete them). The VM has to be running for the breakdown.

**Recreate VM at this size** changes the internal storage size. It:

1. backs up iiSU's title art and media (and checks the backup) before touching anything
2. moves the old storage aside
3. builds a fresh VM at the new size
4. reinstalls iiSU and restores the backup
5. puts the old VM back if the reinstall fails

iiSU's own settings and favorites, and the folder access you granted it, are not restored (you'll pick your ROM folder again). The storage file never shrinks when you delete things inside the VM, so recreating it is also the way to get drive space back. When it finishes it offers to delete the old storage (it can be most of a drive's worth of space) and to start the VM, so you can pick your ROM folder again in iiSU.

## If the VM runs out of internal storage

**Symptom:** iiSU crashes at startup or while scanning, and the system log shows `SQLiteFullException: database or disk is full` or `ENOSPC (No space left on device)`. Diagnostics shows **Android VM storage** as an error.

**Cause:** the Android data partition, which AVDs created before v0.5.5 size at 6 GB. A Play-image Android plus iiSU's downloaded artwork (about 2 GB for a 2,000-game library) fills it. New installs get 16 GB by default, and Setup's **Android VM** card lets you choose 8 to 128 GB (a maximum: the file on your drive only grows as the VM fills).

**Fix:** Android can't grow an existing data partition, so the VM's data image has to be recreated. The easy way is **Settings > Storage > Recreate VM at this size**, described above.

<details>
<summary>Doing it by hand</summary>

1. **Back up** iiSU's artwork: Backup & Restore > Back Up iiSU Data. Note that iiSU's private in-app data (favorites, play history, settings) can't be backed up and will be reset.
2. **Stop** Community-iiSU-PC.
3. In `bridge/android-sdk-portable/avd-home/iisuwin.avd/`, move (don't delete, so you can roll back) `userdata-qemu.img.qcow2`, `userdata-qemu.img`, `encryptionkey.img(.qcow2)`, `cache.img(.qcow2)` and the `snapshots` folder somewhere else, and set `disk.dataPartition.size=16G` in `config.ini`.
4. **Start** it (it creates a fresh, bigger data image), then reinstall iiSU: Updates > iiSU App Updates > Use a Different APK..., pick your iiSU APK. This also reinstalls the redirector apps.
5. **Restore** the artwork: Backup & Restore > Restore iiSU Data, then Stop and Start once more.

</details>

Freeing space without rebuilding is rarely enough: clearing Android's caches frees little, and the bulk of the space is app data Android keeps private.

## Reporting a problem

[Open an issue](https://github.com/MAGOOSKEE/Community-iiSU-PC/issues/new/choose) and include:

- what you did and what happened instead
- your Community-iiSU-PC version (bottom of the Manager's sidebar) and your iiSU version
- `start.log` or `setup.log`, whichever covers the failure, plus `bridge.log` for a game that won't launch

The logs start with your PC's details, so you don't need to describe your hardware separately. They include folder paths from your PC, such as your ROM folder, so glance through them before posting.
