"""
Checker for shared/emulator_defaults.py: actually launches every configured
PC emulator with a real ROM from your library and watches whether it exits
immediately (a sign the pre_args/rom-ordering for that profile is wrong, the
way xemu's bare trailing ROM path collided with its own persisted xemu.toml
CD drive and died with exit code 1 within a second).

Uses build_pc_launch_args() from bridge/launch_bridge.py, the exact function
the real launch path calls, so a PASS here means the argv this tool built is
identical to what a real launch would use, not a separate reimplementation
that could disagree with it.

Not part of the shipped app, just a dev tool, run it against your own
config.json/roms library before committing a change to emulator_defaults.py.

Run with (from the repo root):
    python try_launch_args.py                  # test everything it can find
    python try_launch_args.py --list            # show what it found, launch nothing
    python try_launch_args.py --only ps2,xbox   # test just these consoles
    python try_launch_args.py --timeout 10      # seconds before a still-running process counts as a PASS
    python try_launch_args.py --rom xbox="X:\\iiSU Roms\\xbox\\SSX Tricky (USA).xiso.iso"
                                                 # override auto-picked ROM for one console

A profile counts as:
    PASS  - still running after --timeout seconds (killed afterward), or
            exited with code 0 only after that long
    FAIL  - exited (any code) within --timeout seconds; stdout/stderr tail
            is printed so you can see why, same way the xemu bug was found
    SKIP  - executable or a test ROM for that console/extension isn't
            available on this machine, or (RetroArch) the core .dll isn't
            installed, nothing gets launched
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "bridge"))

from bridge_config import ConfigMissingError, load_config
from console_names import load_console_lookup, resolve_console_shortname
from launch_bridge import build_pc_launch_args, core_dll_from_pre_args, find_executable

from shared.emulator_defaults import RETROARCH_BY_EXTENSION, RETROARCH_SAFETY_NET_EXTENSIONS, STANDALONE_DEFAULTS

# GameNative/Steam launches via steam://rungameid/<id>, never a subprocess
# this tool can spawn and watch the same way, nothing to test here.
NOT_APPLICABLE_CONSOLES = {"steam"}

CREATE_NO_WINDOW = 0x08000000


def discover_folder_roms(roms_dir: Path) -> dict[str, list[Path]]:
    """console shortname -> every file under its recognized ROM folder,
    same folder-name resolution sync_library.py uses for the real library,
    so "which folder counts as which console" never has to be duplicated
    or guessed separately here."""
    exact, by_compact = load_console_lookup()
    consoles: dict[str, list[Path]] = {}
    if not roms_dir.is_dir():
        return consoles
    for folder in sorted(roms_dir.iterdir()):
        if not folder.is_dir():
            continue
        shortname = resolve_console_shortname(folder.name, exact, by_compact)
        if shortname is None:
            continue
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        if files:
            consoles.setdefault(shortname, []).extend(files)
    return consoles


def discover_files_by_extension(roms_dir: Path) -> dict[str, list[Path]]:
    """extension -> every matching file anywhere under roms_dir, for the
    RetroArch by-extension profiles, which route on extension alone
    regardless of which folder a file happens to live in (matching how
    find_emulator_for_package falls back to RETROARCH_BY_EXTENSION)."""
    by_ext: dict[str, list[Path]] = {}
    if not roms_dir.is_dir():
        return by_ext
    for path in roms_dir.rglob("*"):
        if path.is_file():
            by_ext.setdefault(path.suffix.lower(), []).append(path)
    return by_ext


def run_check(label: str, args: list[str], cwd: Path, timeout: float) -> tuple[str, str]:
    """Launches args, waits up to timeout seconds, returns (verdict, detail).
    A process still alive at the deadline is treated as a PASS and killed;
    a process that already exited (any code) is a FAIL, since no real
    emulator here should ever quit on its own within a few seconds of a
    normal launch."""
    try:
        process = subprocess.Popen(
            args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return "FAIL", f"could not start process: {exc}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            tail = "\n".join(output.strip().splitlines()[-15:])
            return "FAIL", f"exited with code {process.returncode} after launch\n{tail}"
        time.sleep(0.25)

    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    return "PASS", f"still running after {timeout:.0f}s (killed for cleanup)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="show what would be tested, launch nothing")
    parser.add_argument("--only", help="comma-separated console shortnames to test (default: all)")
    parser.add_argument("--timeout", type=float, default=8.0, help="seconds before a still-running process counts as a PASS (default: 8)")
    parser.add_argument("--rom", action="append", default=[], metavar="CONSOLE=PATH", help="override the auto-picked ROM for one console, repeatable")
    args = parser.parse_args()

    rom_overrides = {}
    for entry in args.rom:
        if "=" not in entry:
            parser.error(f"--rom must be CONSOLE=PATH, got {entry!r}")
        console, path = entry.split("=", 1)
        rom_overrides[console] = Path(path)

    only = {c.strip() for c in args.only.split(",")} if args.only else None

    try:
        config = load_config()
    except ConfigMissingError as exc:
        print(exc)
        sys.exit(1)

    search_roots = [Path(p) for p in config["search_roots"]]
    roms_dir = Path(config["roms_dir"])
    exe_cache = {"executables": {}, "roms": {}}

    folder_roms = discover_folder_roms(roms_dir)
    ext_roms = discover_files_by_extension(roms_dir)

    results = []  # (label, verdict, detail)

    for profile in STANDALONE_DEFAULTS:
        console = profile["console"]
        label = f"{profile['console_label']} [{profile['app_label']}]"

        if only is not None and console not in only:
            continue
        if console in NOT_APPLICABLE_CONSOLES:
            results.append((label, "SKIP", "not a subprocess launch (Steam URI), nothing to test"))
            continue

        rom_path = rom_overrides.get(console) or (folder_roms.get(console, [None])[0])
        if rom_path is None:
            results.append((label, "SKIP", f"no ROM found under a '{console}'-recognized folder in {roms_dir}"))
            continue

        executable = find_executable(profile["exe_names"], search_roots, exe_cache)
        if executable is None:
            results.append((label, "SKIP", f"none of {profile['exe_names']} found under {search_roots}"))
            continue

        argv = build_pc_launch_args(profile, executable, rom_path)
        if args.list:
            results.append((label, "WOULD RUN", " ".join(argv)))
            continue

        print(f"[{label}] {' '.join(argv)}")
        verdict, detail = run_check(label, argv, executable.parent, args.timeout)
        results.append((label, verdict, detail))

    for ext, entry in list(RETROARCH_SAFETY_NET_EXTENSIONS.items()) + [
        (ext, {"console": e["console"], "exe_names": ["retroarch.exe"], "pre_args": ["-L", f"cores/{e['core']}", "-f"]})
        for e in RETROARCH_BY_EXTENSION for ext in e["extensions"]
    ]:
        if isinstance(entry, tuple):
            console, exe_names, pre_args = entry
            profile = {"exe_names": exe_names, "pre_args": pre_args}
        else:
            console = entry["console"]
            profile = {"exe_names": entry["exe_names"], "pre_args": entry["pre_args"]}
        label = f"RetroArch by-extension {ext} [{console}]"

        if only is not None and console not in only:
            continue

        rom_path = rom_overrides.get(f"{console}{ext}") or (ext_roms.get(ext, [None])[0])
        if rom_path is None:
            results.append((label, "SKIP", f"no '{ext}' file found anywhere under {roms_dir}"))
            continue

        executable = find_executable(profile["exe_names"], search_roots, exe_cache)
        if executable is None:
            results.append((label, "SKIP", f"none of {profile['exe_names']} found under {search_roots}"))
            continue

        core_dll = core_dll_from_pre_args(profile["pre_args"])
        if core_dll and not (executable.parent / "cores" / core_dll).is_file():
            results.append((label, "SKIP", f"core {core_dll} not installed under {executable.parent / 'cores'} (this tool won't auto-download it)"))
            continue

        argv = build_pc_launch_args(profile, executable, rom_path)
        if args.list:
            results.append((label, "WOULD RUN", " ".join(argv)))
            continue

        print(f"[{label}] {' '.join(argv)}")
        verdict, detail = run_check(label, argv, executable.parent, args.timeout)
        results.append((label, verdict, detail))

    print()
    print(f"{'RESULT':<10} {'PROFILE':<45} DETAIL")
    print("-" * 100)
    for label, verdict, detail in results:
        first_line = detail.splitlines()[0] if detail else ""
        print(f"{verdict:<10} {label:<45} {first_line}")
        for extra_line in detail.splitlines()[1:]:
            print(" " * 56 + extra_line)

    fail_count = sum(1 for _, verdict, _ in results if verdict == "FAIL")
    if fail_count:
        print(f"\n{fail_count} profile(s) FAILED, fix pre_args/rom ordering in shared/emulator_defaults.py before committing.")
        sys.exit(1)
    print("\nNo failures.")


if __name__ == "__main__":
    main()
