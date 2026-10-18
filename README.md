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
  `07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28`. The patcher refuses any other file.
- Tested with Community Patch 1.12.8 installed, offline (skirmish) only.
- Windows and Python 3. The patcher only uses the standard library.

## Build, install, restore

Run these from the repository folder in PowerShell:

```powershell
# 1. Build the patched file into build\ (the game folder is not touched); add --fps 60 for 60 FPS
python ra3fps\ra3_fps_patch.py --dry-run --out build\ra3_1.12_90fps.game

# 2. Install it (asks for administrator rights, verifies the backup first, prints the verified hash)
.\ra3fps\install.ps1 -Build build\ra3_1.12_90fps.game

# 3. Back to stock at any time
.\ra3fps\install.ps1 -Restore
```

- The default game folder is `C:\Program Files\EA Games\Command and Conquer Red Alert 3\Red Alert 3\Data`. For
  another install, pass `--game <path to ra3_1.12.game>` to the patcher and `-GameDir <Data folder>` to
  `install.ps1`.
- The patcher reads the original from `ra3_1.12.game.bak`, or from `ra3_1.12.game` itself while it is still
  unmodified. On first install, `install.ps1` creates the `.bak` from the verified original. Backups are never
  modified or deleted.
- If script execution is disabled, run
  `powershell -ExecutionPolicy Bypass -File .\ra3fps\install.ps1 -Build build\ra3_1.12_90fps.game`.

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
ra3fps/ra3_fps_patch.py   the patcher
ra3fps/install.ps1        installs a build or restores the original (self-elevating, hash-verified)
tools/                    developer tools (disassembly cache and helper, build verifier, shader disassembler,
                          read-only particle monitor)
tests/                    emulator tests of the particle and unit-physics code caves
docs/                     documentation
build/                    local outputs (ignored by git)
```

## Disclaimer

This modifies a game executable. Use it at your own risk, and keep `ra3_1.12.game.bak`. It is not affiliated with or
endorsed by Electronic Arts.

## License

Copyright (C) 2026 Jerry Liu. Licensed under the GNU General Public License v3.0; see [LICENSE](LICENSE). The license
covers this repository's code and documentation only, not Red Alert 3 or its files.
