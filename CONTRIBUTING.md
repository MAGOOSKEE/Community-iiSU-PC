# Contributing

Thanks for wanting to help. Bug reports, fixes and reports from setups that haven't been tested much (Waydroid, Steam Deck, non-KDE desktops) are all welcome.

## Reporting a bug

[Open an issue](https://github.com/MAGOOSKEE/Community-iiSU-PC/issues/new/choose) and fill in the form. The most useful thing you can attach is the log that covers the failure. [Troubleshooting](docs/troubleshooting.md#logs) says which one that is.

Please never attach or link an iiSU APK, ROMs, BIOS files or anything else you don't have the right to share.

## Making a change

1. Fork the repo and branch from **`dev`**. Pull requests go to `dev`; `master` only moves at a release.
2. Get it running: [Running from source](docs/development.md#running-from-source).
3. Make your change. [Project layout](docs/development.md#project-layout) says where things live.
4. Run the tests:

   ```bash
   python -m unittest discover -s tests
   ```

5. Open a pull request that says what changed, why, and how you tested it.

## Conventions

- **Standard library first.** The app's only third-party dependencies are PySide6 and Pillow. Please don't add another without discussing it in an issue.
- **Both platforms.** Anything OS-specific goes behind `shared/platform_compat.py` or a `_windows` / `_linux` module pair, so call sites stay the same on both.
- **Logic out of the GUI.** Code behind a Manager page that does real work belongs in `bridge/services/`, free of Qt, with a test in `tests/`.
- **Tests never touch a real install.** They use temporary folders and a temporary config, never `bridge/config.json`, and never start the Android VM or launch a game.
- **Say why.** Modules and non-obvious functions open with a docstring that explains what they are for and the reasoning behind them.
- **Plain punctuation.** No em dashes and no `--` as punctuation in comments, docs, UI text or commit messages. Real command-line flags are fine.
- **Commit messages** are one short line in the imperative that says what changed for the user, for example `Quit key: leave a tap to PC games`.
- If you change how the Manager looks, regenerate the screenshots with `python scripts/capture_screenshots.py`.

## What can't be accepted

- iiSU's APK, decompiled iiSU code, or any other third-party binaries or copyrighted game files.
- Changes that bundle or download iiSU itself. Users supply their own copy.

## License

By contributing you agree that your contribution is licensed under the project's [PolyForm Noncommercial 1.0.0](LICENSE) license.
