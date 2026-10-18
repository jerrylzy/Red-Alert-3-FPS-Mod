# Particles

Particle systems step once per drawn frame. Some of their state advances per step: emission, CPU particles, update
modules and system lifetime. GPU particles instead fade and expire in real time. At 90 FPS, stepping on every drawn
frame would make emission and CPU-particle aging 3× too fast. The patch therefore:

- keeps every particle system's full step at the stock 30 Hz;
- spreads continuous emission over the drawn frames between steps;
- quantizes GPU particle lifetimes to the step boundaries, so particles are visible exactly as long as at 30 FPS;
- scales three elapsed-frame comparisons.

Addresses are VAs in the original `ra3_1.12.game`; fps is 90 or 60, and r = fps / 30 drawn frames per particle step
(3 or 2).

## Internals

### Manager and per-system step

- `TheFXParticleSystemManager` = `[0xCE9094]`, vtable 0xC118F0. Its update (`vtbl+0x14` = 0x633290) calls
  simulate-all 0x710250 on every drawn frame and then rebuilds the render buckets. The render passes
  (`vtbl+0x40`/`+0x44` = 0x5FFB00/0x5FFB70) increment a pass counter `[mgr+0x94]`.
- Simulate-all walks the system list anchored at `mgr+0x38` (node: `+0` next, `+8` system). For each system it calls
  the per-system step **0x70FE10** (`ecx` = system, one stack argument, `ret 4`, returns `al` = alive) from
  **0x7102BA**, its only caller.
- Step order in 0x70FE10:
  1. initial-delay countdown `[sys+0x3C]`;
  2. sound;
  3. drawable/bone transform tracking (`[sys+0x9C]` drawable, `[sys+0xA0]` bone);
  4. emission (0x710023–0x71003E), if the flags allow it (see the system object table) and there is no valid master
     handle `[sys+0x11C]`: `0x6FD770(&sys[+0x54], flag bit 2, &sys[+0xD4])`;
  5. update modules `[sys+0x24]`, `[sys+0x18]` and the module list `[sys+0x2C…]`, each `vtbl+0x14` (from 0x710043);
  6. storage update 0x6F5E90: life countdown `[sys+0x10C]`, storage `vtbl+0x20`, death check.

### Emission

0x6FD770 (`ret 0xC`; only caller 0x71003E):

- **Burst countdown** `[sys+0x104]`: the system emits when it reaches 0.
- **Count** = `ftol(rand(template+0xA8) × [sys+0x50])`, computed by the `call 0x4D3CF0` at **0x6FD821** and passed to
  `storage->vtbl+0x1C(count)` at 0x6FD834.
- **Next delay** = `trunc(rand(template+0x9C))`. One-shot templates (`[template+0x48]`) set the countdown to 1;
  otherwise it counts down.
- **Slaves** chained via `[sys+0x114]` loop back through the same code. At the count site, `esi` is the emitting
  system.
- **Random variables** are `{type @+0, min @+4, max @+8}`, sampled by 0x63FAC0 from the client RNG at 0xCB48B8. So
  the burst-delay maximum is at `template+0xA4`, and a **continuous emitter** (emits every step) is one with
  `[template+0xA4] < 1.0`.

### Emission requires the owning drawable to be in view

The step only emits while byte `[drawable+0x13C]` of the owning drawable has bit 0x10 set and bit 0x08 clear, and
byte `[drawable+0x13E]` has bit 0x20 clear. Bit 0x10 is the per-frame "in view" flag: the visibility pass (0x581160,
loop at 0x5812AB) sets it for drawables that pass the test at 0x561B60 and clears it for the rest. Drawables are found
by ID through TheGameClient's hash map at `[client+0x88]` (buckets `[+4]`, count `[+8]`, nodes `{id, drawable, next}`;
lookup 0x51F110). In `psmon` captures the bit is stable from frame to frame, so the 30 Hz steps sample it without
aliasing.

### Storages (vtable → kind)

| Vtable | Storage | Notes |
|---|---|---|
| 0xC22660 | GPU (GPUParticleSystemStorageModule) | time-based, see below; expiry `vtbl+0x20` = 0x70C0F0 |
| 0xC22C9C | TerrainFire (GPU-derived) | |
| 0xC221D4 | CPU | `vtbl+0x20` = 0x706B00 → per-particle update 0x6FD110 every step |
| 0xC21F54 | Swarm | |
| 0xC22044 | Trail | |
| 0xC22090 | Wave | |

### System object

| Offset | Field |
|---|---|
| +0x08 | template |
| +0x0C / +0x10 / +0x14 / +0x1C / +0x20 | color / alpha / update / physics / draw module |
| +0x18, +0x24 | per-step modules |
| +0x2C | module list |
| +0x3C | initial delay (steps) |
| +0x40 | flags: 0x08 no system lifetime, 0x10 emission off, 0x20 stopped (set by stop() 0x70F880, also on slaves). Emission needs `!(0x20) && (0x08 \|\| life > 0) && !(0x10)` |
| +0x44..+0x50 | four multipliers, 1.0 at init; +0x50 scales the burst count |
| +0x54 | position (emission origin) |
| +0x90 | storage |
| +0x9C / +0xA0 | drawable / bone |
| +0x104 | burst countdown |
| +0x10C | life |
| +0x110 | particles created |
| +0x114 / +0x11C | slave / master handle |

Template fields used here: `+0x48` one-shot flag (byte), `+0x9C` burst delay and `+0xA8` burst count (random
variables). `tools/psmon.py` also decodes `+0x50` type, `+0x64` particle lifetime, `+0x70` system lifetime, `+0xB4`
initial delay and `+0xDC` priority.

### GPU particles are time-based

- **Creation:** the record constructor 0x6FFD50 (base 0x6FEB20, vtable 0xC21AC8) stores the lifetime L (an integer
  in 30 Hz steps) at `[rec+0x28]` and the death time `[rec+0x48] = syncMs × 0.03 + L`. The `× 0.03` is the `fmul` at
  0x6FFDAA, which the patch keeps at 0.03.
- **Expiry:** records sit in a min-heap ordered by death time. The expiry 0x70C0F0 pops them while
  `syncMs × 30 × 0.001 > death`.
- **Vertices:** the VB fill 0x7050B0 rewrites dirty blocks from the live records via 0x6F80C0, which writes the
  lifetime L and `birth = death − L` into the vertices.
- **Shader:** the vertex shader computes `age = c38 − birth` with `c38 = Time × 30` (preshader), where
  `Time = Sas.Time.Now = [0xCD8C7C] × 0.001` (set by 0x4D7BC0, called at 0x63C314). Particles with
  `age > lifetime` collapse to nothing (every vs_2_0/vs_3_0 variant), and the same lifetime drives the keyframe
  animation.
- **Clocks:** `[0xCD8C7C]` and `[0xCDD78C]` both come from the accumulator `[0xCE6528] += [0xCE690C] × frames
  elapsed` (0 while paused). UI rendering temporarily swaps `[0xCDD78C]` to a real-time clock (0x936200 / 0x93B700).
- **Sync units:** one sync unit (`syncMs × 0.03`) is one 30 Hz frame. A drawn frame advances 0.99 units at 30 FPS,
  0.33 at 90 FPS and 0.48 at 60 FPS (whole-millisecond steps).

A GPU particle therefore fades and expires in real time regardless of how often its system steps, but new particles
appear only when the system steps.

### CPU particles are step-based

CPU storage updates every particle once per step (0x6FD110): position, lifetime countdown `[p+0x38]`, and colour/alpha
states that are seeded from keyframe 0 at creation (0x6F6AF0, 0x6F68D0 + 0x6F39C0) and advance per step.

## Changes

### 30 Hz steps for every system

The call at 0x7102BA goes to a cave instead of 0x70FE10. A system with storage runs its full step when
`(frame × 30) % fps < 30`: every 3rd drawn frame at 90 FPS, every 2nd at 60 FPS. On the other frames it returns
"alive" without stepping, unless it gets a partial step (below). Systems without storage step every drawn frame.

Emission, CPU particles, update modules, system lifetime and initial delays therefore keep the stock 30 Hz rate.

### Split emission

Stepping at 30 Hz alone would add a whole step's particles in one burst every r-th drawn frame. Continuous emitters
instead spread them over the r drawn frames of each step:

- **Step mode** (byte at 0xD30BA0):
  - mode 2 on full-step frames;
  - mode 1, a partial step, on the frames in between, unless an initial delay `[sys+0x3C]` or a burst countdown
    `[sys+0x104]` is pending;
  - periodic emitters (`[template+0xA4] >= 1.0`) only step on full-step frames (mode 0).
- **Partial step:** runs the step's transform tracking and emission. At 0x710043 a jump to a cave returns "alive"
  before the update modules and the storage/lifetime update, so those still advance only on full steps.
- **Emission count** (cave replacing the `ftol` call at 0x6FD821): with a non-zero mode, each frame emits
  `(count × j + phase) / r`.
  - `phase = (frame + hash(system)) % r` with `hash = (system × 0x9E3779B1) >> 16`, which spreads the rounding across
    frames and systems.
  - `j` = 1, except on a system's first emission (`[sys+0x110] == 0`). There `j` is the number of drawn frames of the
    current step so far, so a system that starts mid-step still emits its whole first step.

New particles appear on every drawn frame. A finite system emits the stock total (within one particle; checked by
`tests/test_split_caves.py`).

### GPU particle lifetimes

A GPU particle is drawn while `age <= L`. At 30 FPS, frames are 0.99 units apart, so it is visible on
`n = floor(L / 0.99) + 1` frames. A sprite re-emitted every L + 1 steps is therefore seamless at 30 FPS; the burning
Soviet power plant has such a template, with lifetime 15 and burst delay 15. With 30 Hz steps at 90 FPS, the particle
would expire on an 11 ms frame (`45 × 0.33 <= 15`, so frames 0..45), while its replacement only arrives with the next
step on frame 48. The sprite would be missing for 2 drawn frames every 0.53 s.

The record constructor (death time, site 0x6FFDB0) and the vertex writer (birth and lifetime for the shader, site
0x6F80C5) call a cave instead of converting L to float. The cave returns

`L' = n × s − u / 2`, with `n = floor(100 L / 99) + 1`,

where s is the sync units per particle step (0.99 at 90 FPS, 0.96 at 60 FPS) and u the sync units per drawn frame
(0.33 / 0.48). A GPU particle is then visible on exactly r × n drawn frames, the same duration as at 30 FPS. For
L = 15: L' = 16 × 0.99 − 0.165 = 15.675, so frames 0..47 show it and the replacement arrives on frame 48. Lifetimes
of 2^24 or more (or negative) keep the original `float(uint L)`. `tests/test_life_quantize.py` checks this for
L = 0..1000.

### Particle timers

Three particle modules compare elapsed drawn frames with 30 Hz frame counts. Caves scale the elapsed frames by 30/fps:

- texture animator 0x6F9450 (a delay of 0 behaves like 1);
- delayed attach 0x6F4A50;
- system-age trigger 0x6FB4D7.

## Remaining differences from stock

- CPU particles move in 30 Hz steps, while everything else is drawn at the full frame rate.
- The shader uses one lifetime for visibility and keyframe animation, so GPU particles animate over L' instead of L:
  at `L / L'` of the stock rate. That is about 16% slower for a 4-step lifetime, about 9% for 8 steps, and less for
  longer ones. Their visible duration is the same as stock.
