# Red Alert 3 FPS Mod

A binary patch for **Command & Conquer: Red Alert 3 v1.12** that renders at **90 FPS** (or 60 FPS) instead of the
stock 30. Game logic keeps its 15 Hz tick, so game speed, build times and unit speeds are unchanged. Client-side code
that counted drawn frames is re-timed to look and behave as at 30 FPS.

90 FPS is the engine's ceiling: each logic tick is split into 6 sub-frames and every drawn frame processes at least
one, so 15 × 6 = 90. Its 11 ms frame time also keeps the game's timing identical to stock. See
[Why 90 FPS](docs/how-it-works.md#why-90-fps).

## What it changes

- **Frame pacing:** the renderer draws 90 (or 60) frames per second, and logic still advances 15 times per second.
- **60 FPS scheduling:** the sub-frame scheduler rounds up, alternating 1 and 2 logic sub-frames per drawn frame.
- **Render clocks:** per-frame helpers (ms per frame, frames per ms, the sync-clock step) follow the new frame rate.
- **Display timers:** timers and durations counted in drawn frames last as long as at 30 FPS.
- **Unit physics:** vehicle suspension, tilt and wobble step at the stock 30 Hz instead of once per drawn frame, so
  units shake and sway as in stock.
- **Unit position history:** after a unit stops, its interpolation history keeps updating for as long as at 30 FPS,
  so a stopping vehicle settles instead of holding a braking tilt.
- **Tints:** flashes, status tints and the brightness pulse last as long as at 30 FPS.
- **Particles:** every particle system steps at 30 Hz, and continuous emitters spread their emission over every
  drawn frame.
- **Particle lifetimes:** GPU particles stay visible exactly as long as at 30 FPS.
- **Particle timers:** three frame-count comparisons in particle modules run at stock speed.
- **Camera scrolling:** edge, keyboard and right-drag scrolling move at stock speed.

Details are in [docs/how-it-works.md](docs/how-it-works.md) and [docs/particles.md](docs/particles.md).

## Requirements

- Red Alert 3 v1.12: `Data\ra3_1.12.game` of the EA App / Steam release, SHA-256
  `07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28`. The installer and the patcher refuse any other
  file.
- Red Alert 3: Uprising is not supported yet; support for it is being explored.
- Tested with Community Patch 1.12.8 installed, offline (skirmish) only.
- Windows 10 or 11. Python 3.8 or newer is used if it is installed; otherwise the installer fetches a temporary
  portable Python (see below).

## Install

1. Download `RA3-FPS-Mod-<version>.zip` from the
   [latest release](https://github.com/jerrylzy/Red-Alert-3-FPS-Mod/releases/latest) and extract it anywhere.
2. Exit Red Alert 3 and double-click `Install.cmd`.
3. Choose `1` (90 FPS) and approve the administrator prompt.
4. To go back to stock, run `Install.cmd` again and choose `3`.

What the installer does:

- **Builds on your PC.** It finds the game (through the registry or the EA App default folder, otherwise it asks),
  then builds the patched file from your own game file with `ra3fps\ra3_fps_patch.py`. No game files are downloaded
  or included.
- **Needs no setup.** The patcher runs on Python 3.8 or newer. If none is installed, the installer downloads the
  official portable Python 3.14.8 from python.org into a temporary folder (about 12 MB), checks it against its
  published SHA-256 and deletes it afterwards. Nothing is installed on the system.
- **Keeps the original safe.** It only accepts the original v1.12 file, keeps it as `ra3_1.12.game.bak` (never
  modified or deleted), refuses to run while the game is running, and prints the verified SHA-256 of the result.
- If Windows warns about the downloaded `Install.cmd`, right-click the zip, choose **Properties**, tick **Unblock**,
  and extract it again.

### From the repository

Run these from the repository folder in PowerShell:

```powershell
.\Install.cmd                                    # the same menu
.\ra3fps\install.ps1 -Fps 90                     # or without the menu (-Fps 60 for 60 FPS)
.\ra3fps\install.ps1 -Restore                    # back to stock

# Build only (the game folder is not touched), then install that file
python ra3fps\ra3_fps_patch.py --dry-run --out build\ra3_1.12_90fps.game
.\ra3fps\install.ps1 -Build build\ra3_1.12_90fps.game
```

- For another install, pass `-GameDir <Data folder or game folder>` to `install.ps1` and
  `--game <path to ra3_1.12.game>` to the patcher.
- The patcher reads the original from `ra3_1.12.game.bak`, or from `ra3_1.12.game` itself while it is still
  unmodified. On first install, `install.ps1` creates the `.bak` from the verified original. Backups are never
  modified or deleted.
- If script execution is disabled, run
  `powershell -ExecutionPolicy Bypass -File .\ra3fps\install.ps1 -Fps 90`, or use `Install.cmd`.

## Known limitations

- CPU particles move in 30 Hz steps; everything else is drawn at the full frame rate.
- Unit suspension, tilt and wobble also change in 30 Hz steps, as in stock; unit positions and facing are drawn at
  the full frame rate.
- At 60 FPS the frame pacer rounds the frame time down to 16 ms, so the game runs about 3 % faster than stock
  (calculated from the pacer; 90 FPS matches stock).
- GPU particles with very short lifetimes (4–8 steps) animate up to ~16% slower than stock, while staying visible
  exactly as long.
- Not tested online.

## Credits

The original Red Alert 3 60FPS patch was made by **HaoJun0823 (Randerion)**:

- Bilibili: https://space.bilibili.com/92473742
- Patch video: https://www.bilibili.com/video/BV1GK4y1b7B4/
- GitHub: https://github.com/HaoJun0823

His patched binaries carry the signature "60FPS PATCH BY HAOJUN0823". This project is an independent
re-implementation inspired by studying his patch. It takes a different approach: the 15 Hz logic tick is left
unchanged, and only render pacing plus client-side timers are re-timed. It contains no files from his or anyone
else's redistribution.

All rights to Red Alert 3 belong to Electronic Arts. This repository ships no game files.

## Documentation and layout

- [docs/how-it-works.md](docs/how-it-works.md): engine timing model and every change, with addresses.
- [docs/particles.md](docs/particles.md): particle system internals and the particle changes.
- [docs/development.md](docs/development.md): tools, verification, reference hashes, particle monitor.

```
Install.cmd               double-click installer: menu with 90 FPS, 60 FPS and restore (runs ra3fps/install.ps1)
ra3fps/ra3_fps_patch.py   the patcher
ra3fps/install.ps1        builds and installs the patch or a given build, or restores the original (finds the game
                          and Python, self-elevating, hash-verified)
tools/                    developer tools (disassembly cache and helper, build verifier, shader disassembler,
                          read-only particle monitor)
tests/                    emulator tests of the particle and unit-physics code caves, end-to-end installer test
docs/                     documentation
build/                    local outputs (ignored by git)
```

## Disclaimer

This modifies a game executable. Use it at your own risk, and keep `ra3_1.12.game.bak`. It is not affiliated with or
endorsed by Electronic Arts.

## License

Copyright (C) 2026 Jerry Liu. Licensed under the GNU General Public License v3.0; see [LICENSE](LICENSE). The license
covers this repository's code and documentation only, not Red Alert 3 or its files.
