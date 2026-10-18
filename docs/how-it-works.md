# How it works

All addresses are virtual addresses in the original `ra3_1.12.game` (image base 0x400000, PE timestamp 0x50185C01).
For `.text`, `.rdata` and the file-backed part of `.data`, file offset = VA − 0x400000; the tables in
`ra3fps/ra3_fps_patch.py` use file offsets.

## Engine timing model

| Name | Address | Stock value / notes |
|---|---|---|
| `LOGIC_FPS` | `[0xCB4098]` | 15: game logic steps per second |
| `FPS_CAP` | `[0xCB409C]` | 30: frame cap |
| `TheDisplay` | `[0xCE08F0]` | vtable 0xC11090; drawn-frame counter at `[display+0x48]` |
| `TheGameLogic` | `[0xCDDE84]` | logic frame at `+0x50` |
| `TheFXParticleSystemManager` | `[0xCE9094]` | see [particles.md](particles.md) |
| `TheGlobalData` | `[0xCEE998]` | brightness-pulse period at `+0x4C` |

- **Logic** runs at `LOGIC_FPS` = 15 Hz, and every logic step is split into **6 sub-frames**. The engine update
  0x641610 counts a sub-frame counter `[engine+0x58]` from 1 to 6 and interpolates drawables by counter/6. 0x655040(n)
  advances `TheGameLogic` (`vtbl+0x34`) per sub-frame.
- **Drawn frames:** the main loop 0x668300 paces frames from `FPS_CAP` (frame pacer at 0x6686F7). The number of drawn
  frames per logic step is **r = FPS_CAP / LOGIC_FPS** (0x63EA20; also cached in `engine[+0x5C]`), and each drawn
  frame advances 6 / r sub-frames. Stock has r = 2, so each drawn frame advances 3 sub-frames.
- **At 90 FPS** r = 6 (one sub-frame per drawn frame). **At 60 FPS** r = 4 (1.5 sub-frames, see the scheduler
  change). Logic still advances once per r drawn frames, that is 15 times per second, so game speed, build times and
  unit movement are unchanged.
- **The display frame counter** `[display+0x48]` increments once per drawn frame (0x665550). Client-side code counts
  these frames and assumes 30 per second: particles, texture animations, drawable tints, unit suspension and wobble,
  UI timers and camera scrolling. Left alone, all of it would run fps/30 times too fast.
- **Clocks:** the W3D sync accumulator `[0xCE6528]` advances by the ms-per-frame `[0xCE690C]` times the frames
  elapsed (0 while paused). It feeds `[0xCDD78C]` (0x514B70) and `[0xCD8C7C]` (0x4D4A60); shaders get
  `Time = [0xCD8C7C] × 0.001` (0x4D7BC0, called at 0x63C314). `[0xCE690C]` is `1000/fps` in whole milliseconds: 33
  at 30 FPS and 11 at 90 FPS (both 99 % of real time), 16 at 60 FPS (96 %).

### Why 90 FPS

90 FPS is the only frame rate above 30 at which every timing matches stock:

- **Sub-frame limit.** The engine update 0x641610 runs once per drawn frame and advances the sub-frame counter by at
  least one each time; the scheduler 0x655040 only adds sub-frames when r < 6. At r = 6 (90 FPS) every drawn frame
  advances exactly one sub-frame. Above 90 FPS the logic itself would run faster, at fps/6 sub-frames per second (165
  FPS ≈ 1.8×, 180 FPS = 2×). Drawn unit positions are also interpolated in sixths of a logic step, so units cannot
  look smoother than at 90 FPS.
- **Whole-millisecond pacing.** The frame pacer waits `trunc(1000 / fps)` milliseconds per frame (x87 truncation;
  the target is `(1000/15) / (r × speed factor [0xCB4644])`). 30 FPS (33 ms) and 90 FPS (11 ms) both give 66 ms per
  logic step, so logic runs at 15.15 Hz in both. 60 FPS waits 16 ms (64 ms per logic step), so the game runs about
  3 % faster than stock. Stock-equal pacing needs `fps × trunc(1000/fps) = 990`: 30, 45, 90, 165, 330, ….
- **30 Hz client steps.** Particles and unit physics step at 30 Hz, which needs a frame rate that is a multiple of 30.

Going above 90 FPS would need the engine to skip logic sub-frames on some drawn frames, finer interpolation, and
fractional-millisecond pacing and sync clocks. Logic would also stay tied to drawn frames, so any frame drop would slow
the game.

## Changes

Every site is checked against the expected original bytes before it is written, and the source file must match the
original SHA-256. The fps value below is 90 or 60.

### Frame pacing

The six reads of `FPS_CAP` that compute r (or the frame time) read a `.rdata` dword that already holds the target value
instead: 90 at 0xBD694C, 60 at 0xBD6D64 (unreferenced aligned constants). `FPS_CAP` itself stays 30 for every other
reader.

| Site | Use |
|---|---|
| 0x6686F7 | frame pacer: `mov esi,[fps]` |
| 0x63EA20 | "first drawn frame of the logic step" test |
| 0x655166 | sub-frames per drawn frame |
| 0x64167D, 0x65A45B, 0x66828D | `engine[+0x5C] = fps / logic` (per logic step, game start, engine init) |

### Sub-frame scheduler (60 FPS only)

With r = 4, 6 / r is not an integer. The sub-frame scheduler at 0x65517E divides by 6 rounding down. The patch makes
it round up (`+5` before the division, sign fix dropped), so 60 FPS alternates 1 and 2 sub-frames per drawn frame and
logic stays at 15 Hz.

### Render helpers

Static initializers derive per-frame helpers from `FPS_CAP`, and these now use the target rate:

- ms per frame `1000/fps` → `[0xCE0DF4]`, frames per ms `fps × 0.001` → `[0xCE0DFC]`, float fps → `[0xCE0DF0]` (and
  `1/fps` → `[0xCE0ED4]`, derived from it);
- the integer W3D sync step `[0xCE690C] = 1000/fps`, so the sync clocks above stay real-time;
- `obj[+0x1DC] = fps` at 0x57FF09.

One user of frames-per-ms must keep 30 Hz units: the GPU particle record constructor's `fmul [0xCE0DFC]` at 0x6FFDAA,
which converts sync milliseconds into the 30 Hz units of particle death times. It is pinned to the original 0.03
(constant at 0xBE65E8).

### Display timers

35 reads of `FPS_CAP` in 21 functions that use it with the drawn-frame counter or as a drawn-frame duration are
redirected to the target rate:

| Function | Use |
|---|---|
| 0x553440 | `frame % (fps/2)` |
| 0xBB3740 | static copy `fps/2` → `[0xCE0AA0]` (used by Drawable::update 0x57BE40 as `frame % N`) |
| 0xA28F80 | `next = frame + fps/2` |
| 0x55BCD0, 0xA19C30 | blink: `frame*k/fps`, `frame % (2*fps)` |
| 0xA50EE0, 0xA51080 | `elapsed < fps` |
| 0x56B3C0, 0xA26E50, 0xA46F80, 0xAAB950, 0x53D970 | `fps × seconds` durations |
| 0x62A640, 0xA19160, 0xA42A80 | elapsed frames → time (`× 30/fps`, `/ fps`, `fps × 0.5`) |
| 0xAADA20 | `fps × 10` window |
| 0xBB5D90, 0x687EC0 | `[0xCE8CA8] = fps × 30` (static) and `fps × seconds` (runtime) |
| 0x55CB60, 0x80CEB0 | drawable effect durations `fps × 0.75`, `fps/2` |
| 0xA0C1A0 | countdown `fps × seconds` |

### Unit physics

Vehicle suspension, pitch/roll spring-dampers and the rudder/elevator wobble 0x55BA20 are client-side physics
(Generals' `Drawable::calcPhysicsXform`). Their constants assume one step per 30 Hz frame, but they step once per
drawn frame, so at 90 FPS a harvester's suspension oscillated three times as fast with about twice the wheel travel.

- **Call chain.** Each drawn frame, applyPhysicsXform 0x577530 (called at 0x58080F with a copy of the world matrix)
  asks the dispatcher 0x5742B0 for a tilt {pitch, roll, yaw, z} and, if it gets one, applies it to the matrix copy.
  The dispatcher finds the object's locomotor (`[[[drw+0x138]+0x374]+0x21C]`). If the locomotor already stepped this
  drawn frame (`[loco+0xBC]` == frame, test at 0x5742F1) it returns false and the drawable is drawn untilted.
  Otherwise it records the frame and calls the physics of the locomotor's appearance (`[[[loco+4]+4]+0x64]`, jump
  table 0x574388): 0x568220 (wheels, appearances 1 and 7), 0x55FED0 (2–4), 0x567920 (8). Appearances 5 and 6 have
  no tilt.
- **State.** The physics keeps its springs in a per-drawable state `[drw+0x208]` of 0x70 bytes (constructor
  0x55B5A0), allocated on first use in each appearance function (0x55FEDC, 0x567932, 0x568231) and in Drawable::xfer
  when a saved game is loaded (0x57C728).
- **Patch.**
  - The four allocations go through a cave that requests 0x88 bytes. The extra 0x18 bytes cache the last step:
    +0x70 frame, +0x74 locomotor, +0x78..+0x87 tilt.
  - The four appearance-function calls (0x57432E, 0x574346, 0x57435E, 0x574376) go through wrappers that run the
    physics and then store the cache.
  - The guard at 0x5742F1 jumps to a cave. On the frame of the last step it returns false, as stock. When fps/30 or
    more drawn frames have passed it steps. On the frames in between it returns the cached tilt if this drawable
    cached it at the locomotor's last step; otherwise (for example a second drawable of the same object) it returns
    false, as stock.

The physics therefore runs at 30 Hz with its stock constants. The rudder/elevator wobble is part of it, so it needs
no separate rescaling.

### Unit position history

After logic moves an object, the drawable update 0x570C00 stores the drawn frame in `[drw+0x130]` and, while fewer
than 6 drawn frames have passed (`cmp eax,6` at 0x570C2D), re-syncs the drawable's 4-point position history
(`+0xC4`, `+0xD0` previous and `+0xDC` current logic position, `+0xE8` extrapolated; sync 0x56AD00 with the same test
at 0x56AD20) and interpolates the drawn position. 6 drawn frames are 3 logic steps at 30 FPS but only 1 at 90 FPS.
The wheel physics reads `+0xDC − +0xD0` as the unit's motion, so with the short window a stopping vehicle kept the
velocity of its last move and held a braking tilt for as long as it stood still. Both immediates become
`6 × fps / 30` (18 at 90 FPS, 12 at 60 FPS), the same 0.2 s window as at 30 FPS.

### Tints

Drawable tint envelopes (TintEnvelope::update 0x56AB50, called from Drawable::update 0x57BE40) step once per drawn
frame. Their 30 Hz frame counts are scaled by fps/30:

- **Hard-coded frame counts** (`push imm8/imm32`): default flash 0x56F7B1 (sustain/decay/attack), status tints 0x56F9C7
  (dark 0.25), 0x56FA08 (bright, sustain 300), 0x56FA69 (bright) and 0x56FBA5 (gray 0.8), selection flash 0x56FFC4.
  The sentinel sustains −2 and 9999999 are kept.
- **Flash forwarder:** 0x56F6F0 `flash(color, a, b, c)` forwards caller frame counts (4; 4/4/15; …). Its forwarding
  block at 0x56F79F jumps to a cave that pushes the same arguments, scales the positive ones and resumes at 0x56F7CE.
- **Template tint durations:** milliseconds are converted to frames with the 1000.0 loaded at 0x562C58, which is
  replaced by `1000 × fps / 30`.
- **Brightness pulse:** 0x5740D0 computes `sin(frame % period)` plus a period-long transition, where period =
  `[[0xCEE998]+0x4C]` in 30 Hz frames. The reads at 0x5740EF, 0x570017 (flags and `eax` preserved) and 0x574264 go
  through caves that scale them by fps/30.

### Particles

Summary; the mechanism is explained in [particles.md](particles.md).

| Change | Sites |
|---|---|
| Every particle system runs its full step at 30 Hz | per-system cave at the step call 0x7102BA |
| Continuous emitters also run a partial step (transforms + emission) on the frames between full steps, with emission counts divided by fps/30 | step mode byte at 0xD30BA0, emission-count cave at 0x6FD821, jump at 0x710043 that ends partial steps before the modules |
| GPU particle lifetime L → `n·s − u/2`, so each GPU particle is visible exactly as long as at 30 FPS | record constructor 0x6FFDB0, vertex writer 0x6F80C5 |
| Three elapsed-frame comparisons scale elapsed drawn frames by 30/fps | texture animator 0x6F9450 (delay 0 behaves like 1), delayed attach 0x6F4A50, system-age trigger 0x6FB4D7 |

### Camera scrolling

Edge and keyboard scrolling (update 0xA54CB0) move `factor × 100 × (drawn frames since the last update)` per update.
Right-mouse-drag scrolling (update 0xA54B80) moves `(cursor − anchor) × factor` per drawn frame. The scroll factors
`[0xCB4A00]`/`[0xCB4A04]` are set from the Options "ScrollFactor". Both would run fps/30 times too fast:

- the four uses of the 100.0 constant at 0xC7A374 (0xA512A4, 0xA512F4, 0xA51634, 0xA54EB3; its only users) read a
  cave constant `100 × 30 / fps` instead;
- the two factor loads at 0xA54BDE become a call to a cave that loads both factors and scales them by 30/fps.

### Left unchanged

- Logic-side readers of `FPS_CAP`: 0x71A100, 0x77CA80, 0x7B9A60, 0x7BAFE0, 0x7F8AC0, 0x802AE0, 0x807CB0.
- Wall-clock readers: 0x9591D0, 0x959260, 0x959FA0.
- 0x70C0F0 (GPU particle expiry: compares `sync ms × 30 × 0.001` with death times kept in 30 Hz units), 0x749130 and
  0x94F4A0.
- 0x567BF8 in the vehicle tilt physics 0x567920 scales its per-step inputs by `LOGIC_FPS / FPS_CAP` (15/30). The
  unit physics steps at 30 Hz, so the stock ratio is correct.
- `[0xCE236C]`: the static `fps/2` (= 15) used to rescale logic frame counts.

## Code caves

The extra code lives in the zero-filled tail of `.text`. Its VirtualSize is 0x7C541A while its raw size is 0x7C6000, so
file 0x7C641A–0x7C7000 (VA 0xBC641A–0xBC7000) is zero padding that the loader does not map from the file. The patcher
raises the `.text` VirtualSize to 0x7C6000 so the tail is mapped. Caves are allocated sequentially from file 0x7C6420
(VA 0xBC6420): code is 16-byte aligned and float constants 4-byte aligned. Patched sites reach them with
`call rel32` / `jmp rel32`, and leftover bytes of a replaced instruction become NOPs (or `int3` after a `jmp`).

The particle step mode byte is at VA 0xD30BA0, the first byte past `.data`'s VirtualSize (VA 0xCAB000 + 0x85BA0).
That is inside the last, zero-initialized page of `.data`. The patcher raises the VirtualSize by 0x10 (0x85BA0 →
0x85BB0) to cover it and checks that the address is not referenced anywhere in the original. Values: 0 = normal step,
1 = partial step (transforms + emission only), 2 = full step. Any non-zero value divides emission counts.

`tools/verify_build.py` lists every changed site and every cave of a build, and fails if a non-zero cave byte is not
reachable from a patched site (see [development.md](development.md)).
