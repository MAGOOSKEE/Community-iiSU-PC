"""
A fullscreen black overlay covering the primary monitor, shown during the
two moments this project's own windows would otherwise leave raw desktop
visible behind them:
  - while the AVD cold-boots (its window exists but isn't fullscreened
    yet until launch_bridge.py's show_iisu_window() runs, well after the
    process itself starts)
  - while a game hands off from iiSU to a real PC emulator (between
    iiSU's window minimizing and the emulator's own window appearing and
    taking the foreground)

Implemented as a short-lived PowerShell + WinForms process rather than a
second Tk window, since both callers here need to work regardless of
which thread calls them -- start_iisu_pc.py directly (its own process,
any thread), and launch_bridge.py, which itself sometimes runs from a
background thread of manager.py's own long-lived Tk app. Tcl/Tk's global
state isn't built for a second Tk root from a thread that isn't already
running the existing one's mainloop; a wholly separate OS process
sidesteps that class of problem entirely.

The title uses Bahnschrift SemiBold (bundled with Windows 10+) rather than
this project's usual Segoe UI -- a blockier, more technical/console-ish
face that echoes iiSU's own branding without using any of iiSU's actual
font files, which (like its icons) are its own copyrighted assets and not
ours to include.
"""

import random
import subprocess

# A random one of these accompanies the real context line every time the
# overlay shows -- some genuine-sounding, some not, same as any loading
# screen's flavor text. Purely cosmetic: nothing here reflects anything
# actually happening.
_FLAVOR_LINES = [
    "Reticulating splines...",
    "Charging the flux capacitor...",
    "Waking up the Android...",
    "Untangling controller cables...",
    "Asking nicely for more RAM...",
    "Feeding the hamsters...",
    "Aligning the pixels...",
    "Negotiating with the GPU...",
    "Warming up the emulator...",
    "Counting to infinity (almost there)...",
    "Polishing the loading bar...",
    "Convincing Windows this is normal...",
    "Summoning the boot animation...",
    "Downloading more RAM...",
    "Dusting off old save states...",
    "Herding packets...",
    "Locating the any key...",
    "Calibrating the flux...",
    "Spinning up the virtual disc drive...",
    "Reading the manual (never)...",
]

# %CONTEXT%/%FLAVOR% are plain string substitutions, not PowerShell
# interpolation -- .format()/f-strings would collide with the script's own
# literal { } (script blocks, hashtables), so this is a dumb find/replace
# instead.
_OVERLAY_SCRIPT_TEMPLATE = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$accent = [System.Drawing.Color]::FromArgb(94, 132, 255)
$flavorColor = [System.Drawing.Color]::FromArgb(120, 120, 128)
$trackColor = [System.Drawing.Color]::FromArgb(40, 40, 44)
$centerX = [int]($bounds.Width / 2)
$centerY = [int]($bounds.Height / 2)

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.Location = New-Object System.Drawing.Point($bounds.X, $bounds.Y)
$form.Size = New-Object System.Drawing.Size($bounds.Width, $bounds.Height)
$form.TopMost = $true
$form.BackColor = [System.Drawing.Color]::Black
$form.Cursor = [System.Windows.Forms.Cursors]::None

$titleFont = New-Object System.Drawing.Font("Bahnschrift SemiBold", 32)
$contextFont = New-Object System.Drawing.Font("Segoe UI Semibold", 14)
$flavorFont = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Italic)

$title = New-Object System.Windows.Forms.Label
$title.Text = "iiSU-PC"
$title.ForeColor = $accent
$title.Font = $titleFont
$title.AutoSize = $true
$form.Controls.Add($title)
$title.Location = New-Object System.Drawing.Point(($centerX - [int]($title.PreferredWidth / 2)), ($centerY - 100))

$contextLabel = New-Object System.Windows.Forms.Label
$contextLabel.Text = "%CONTEXT%"
$contextLabel.ForeColor = [System.Drawing.Color]::White
$contextLabel.Font = $contextFont
$contextLabel.AutoSize = $true
$form.Controls.Add($contextLabel)
$contextLabel.Location = New-Object System.Drawing.Point(($centerX - [int]($contextLabel.PreferredWidth / 2)), ($centerY - 36))

$barWidth = 320
$barHeight = 4
$fillWidth = 90
$track = New-Object System.Windows.Forms.Panel
$track.Size = New-Object System.Drawing.Size($barWidth, $barHeight)
$track.Location = New-Object System.Drawing.Point(($centerX - [int]($barWidth / 2)), ($centerY + 4))
$track.BackColor = $trackColor
$form.Controls.Add($track)

$fill = New-Object System.Windows.Forms.Panel
$fill.Size = New-Object System.Drawing.Size($fillWidth, $barHeight)
$fill.BackColor = $accent
$track.Controls.Add($fill)

$flavorLabel = New-Object System.Windows.Forms.Label
$flavorLabel.Text = "%FLAVOR%"
$flavorLabel.ForeColor = $flavorColor
$flavorLabel.Font = $flavorFont
$flavorLabel.AutoSize = $true
$form.Controls.Add($flavorLabel)
$flavorLabel.Location = New-Object System.Drawing.Point(($centerX - [int]($flavorLabel.PreferredWidth / 2)), ($centerY + 26))

$script:barDirection = 1
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 12
$timer.Add_Tick({
    $maxX = $track.Width - $fill.Width
    $newX = $fill.Left + (3 * $script:barDirection)
    if ($newX -le 0) { $newX = 0; $script:barDirection = 1 }
    elseif ($newX -ge $maxX) { $newX = $maxX; $script:barDirection = -1 }
    $fill.Left = $newX
})
$timer.Start()

$form.Add_Shown({ $form.Activate() })
[System.Windows.Forms.Application]::Run($form)
"""


def show(context: str) -> subprocess.Popen | None:
    """Best-effort: returns None instead of raising if PowerShell/WinForms
    aren't available for any reason -- a missing overlay is a cosmetic
    regression, never a reason to fail an actual start or game launch.

    context is a short status line (e.g. "Booting iiSU-PC..." or
    "Waiting on DuckStation...") describing what's actually happening;
    paired with a randomly-picked, purely-for-fun line underneath."""
    script = (
        _OVERLAY_SCRIPT_TEMPLATE
        .replace("%CONTEXT%", _escape_for_powershell_string(context))
        .replace("%FLAVOR%", _escape_for_powershell_string(random.choice(_FLAVOR_LINES)))
    )
    try:
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def _escape_for_powershell_string(text: str) -> str:
    """text lands inside a PowerShell double-quoted string literal, which
    (unlike a single-quoted one) interpolates $variables and treats
    backtick as an escape character -- neutralizing both, then doubling
    embedded double-quotes, makes this safe regardless of what the caller
    passes, even though today it's always our own hardcoded flavor lines
    or a short status string built from a known emulator name."""
    text = text.replace("`", "``")
    text = text.replace("$", "`$")
    text = text.replace('"', '""')
    return text


_BALLOON_SCRIPT_TEMPLATE = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Warning
$icon.Visible = $true
$icon.BalloonTipTitle = "%TITLE%"
$icon.BalloonTipText = "%MESSAGE%"
$icon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
$icon.ShowBalloonTip(8000)
Start-Sleep -Seconds 9
$icon.Dispose()
"""


def notify_error(title: str, message: str) -> None:
    """Best-effort Windows tray balloon notification -- the bridge
    normally runs with no visible window at all (see manager.py's
    debug_show_console_windows), so a launch failure (no emulator mapped,
    executable/rom not found, or an emulator exiting with a non-zero
    code) would otherwise be visible only in a log file nobody's looking
    at. Fire-and-forget and detached, same reasoning as show()'s overlay:
    a notification failing to display is never a reason to fail or delay
    the launch it's reporting on."""
    script = (
        _BALLOON_SCRIPT_TEMPLATE
        .replace("%TITLE%", _escape_for_powershell_string(title))
        .replace("%MESSAGE%", _escape_for_powershell_string(message))
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def close(overlay: subprocess.Popen | None) -> None:
    """Forcibly kills the overlay process rather than trying to close its
    window gracefully -- it has no state to lose and no user input to
    flush, so instant is strictly better here than any delay."""
    if overlay is None:
        return
    try:
        overlay.terminate()
    except OSError:
        pass
