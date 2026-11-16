"""Where each Manager page's "?" button leads: the section of the guides in
docs/ that explains that page, on GitHub, so the help a page offers is one
click away instead of somewhere to go looking.

Each topic is "file.md#heading-anchor", GitHub's anchor for a heading (lower
case, spaces to hyphens, punctuation dropped). tests/test_help_links.py checks
every topic here still names a real heading, so renaming a heading in docs/
can't quietly break a page's help.
"""

from shared.platform_compat import open_uri

DOCS_BASE = "https://github.com/MAGOOSKEE/Community-iiSU-PC/blob/master/docs/"

# Page key (the sidebar's page or sub-page key) -> docs topic.
TOPICS = {
    "home": "using.md#starting-and-stopping",
    "roms": "using.md#your-rom-folder",
    "media_library": "using.md#backup--restore",
    "android_storage": "using.md#the-manager",
    "games_console": "using.md#console-games-and-multi-disc-playlists",
    "windows_apps": "using.md#pc-apps-and-games",
    "general": "using.md#settings",
    "settings": "using.md#display",
    "controls": "using.md#quitting-a-game-escape-and-selectstart",
    "emulator": "using.md#vm-profiles",
    "emulators": "using.md#pc-emulators",
    "storage": "troubleshooting.md#seeing-and-changing-the-vms-storage",
    "advanced": "using.md#settings",
    "backup_restore": "using.md#backup--restore",
    "diagnostics": "troubleshooting.md#start-here-diagnostics",
    "updates": "using.md#updates",
    "uninstall": "installing.md#uninstalling",
}


def url(topic: str) -> str:
    return DOCS_BASE + topic


def open_topic(topic: str) -> None:
    open_uri(url(topic))
