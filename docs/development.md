# Development

## Ground rules

- **Builds start from the pristine original.** Every build is made from `ra3_1.12.game.bak`, or from
  `ra3_1.12.game` while it is still unmodified, after asserting its SHA-256
  (`07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28`). Patching an already patched file is impossible
  by design.
- **Every site is checked.** The patcher compares each site's original bytes (opcode and operand) before writing and
  aborts on any mismatch, using explicit checks that also run under `python -O`.
- **The backup stays untouched.** Never edit the live game file by hand, and never modify or delete the `.bak`.
  Build with `--dry-run --out build\...` and install with `ra3fps\install.ps1`, which verifies the `.bak` before
  copying.
- **No game data in git.** The `.gitignore` covers `*.game`, `*.bak`, `*.exe`, `*.pkl`, shader dumps and `build/`.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt     # capstone + unicorn, only needed for tools/ and tests/
```

The patcher itself needs only the standard library. The tools share paths and helpers through `tools/common.py`
(default game path, `build/` folder, original-file lookup). Run everything from the repository root. Every tool
accepts `--game <path to ra3_1.12.game>` for a non-default install.

## Verifying a build

### Reference hashes

The output is deterministic. From the original v1.12 file the patcher must produce exactly:

| Command | Output | Bytes changed | SHA-256 |
|---|---|---|---|
| `python ra3fps\ra3_fps_patch.py --dry-run` | `build\ra3_1.12_90fps.game` | 1138 | `5F4D1963C834A7B8F002F813B0184E5DDAA66120E3A62BE266D23B4202D4E3B0` |
| `python ra3fps\ra3_fps_patch.py --fps 60 --dry-run` | `build\ra3_1.12_60fps.game` | 1148 | `DEC71E822B21FF8C0C793579DED0D96D980A8AF04113664BD5B84A030EA6F1C1` |

Any change to the patch changes these hashes, so update them when that is intended.

### Emulator tests: `tests/`

```powershell
python tests\test_split_caves.py build\ra3_1.12_90fps.game     # particle-stepping caves (default file)
python tests\test_split_caves.py build\ra3_1.12_60fps.game
python tests\test_life_quantize.py build\ra3_1.12_90fps.game   # GPU particle lifetime caves (default file)
python tests\test_life_quantize.py build\ra3_1.12_60fps.game
python tests\test_unit_physics.py build\ra3_1.12_90fps.game    # unit-physics caves (default file)
python tests\test_unit_physics.py build\ra3_1.12_60fps.game
```

All three run the exact cave bytes of the build in Unicorn and compare them with a Python model.

`test_split_caves.py` covers three caves and a lifecycle simulation:

- the emission-count cave: 1008 cases, including register preservation;
- the per-system step cave: full, partial or no step for 480 combinations of storage kind, burst-delay range, initial
  delay, countdown and frame;
- the step-modules cave: stack unwinding for partial steps;
- system lifecycles, checking that finite systems emit the stock total within one particle.

r is read from the build (3 at 90 FPS, 2 at 60 FPS).

`test_life_quantize.py` runs the record-constructor and vertex-writer caves in x87 extended and single precision
(Direct3D 9 may switch the FPU to single). It checks:

- the formula `n·s − u/2` for L up to 2^24 − 1, and the original `float(uint L)` fallback above that;
- that the x87 stack, the general registers and the stack pointer are preserved;
- that for L = 0..1000 every GPU particle is visible on exactly r × its stock 30 FPS frame count.

`test_unit_physics.py` runs the patched dispatcher 0x5742B0 (original code plus the guard cave, the call wrappers and
the allocation cave) with stub appearance functions. It checks:

- that the physics steps exactly when fps/30 or more drawn frames have passed (also across counter wrap and reset),
  and that every frame in between gets the tilt of the last step;
- stock behaviour for a second call in the same frame, for a second drawable of the same object (no tilt, stale or
  foreign caches ignored) and for appearances without physics;
- that all four state allocations, including the save-game load, request 0x88 bytes with the stack balanced.

### Cave listing: `tools/verify_build.py`

```powershell
python tools\verify_build.py build\ra3_1.12_90fps.game
```

- Prints every changed range as original vs patched disassembly, and decodes section-header changes (e.g.
  `.text VirtualSize 0x7c541a -> 0x7c6000`).
- Lists the cave area per entry point. Each rel32 call/jmp target and absolute memory operand that lands in the cave
  area starts a block: code blocks are disassembled, data blocks are shown as floats, each with the sites that
  reference it.
- Exits with status 1 if any non-zero cave byte is not part of a referenced block.

## Instruction cache: `tools/build_insn_cache.py`

`rdis.py` and `verify_build.py` read a cached linear disassembly of `.text`: a pickled list of
`(address, mnemonic, op_str, size)` tuples from a linear Capstone sweep over the original binary. Bytes that Capstone
cannot decode are skipped one at a time (118 gaps, no `.byte` entries).

```powershell
python tools\build_insn_cache.py      # -> build\text_insns.pkl, all of .text (VA 0x401000-0xBC641A), about 7 s
```

The result has 2,866,851 entries, SHA-256 `BDFA21AF45D0CC5899F0E45BBD2169A76E504C4C23E5BCDEE8D778B5B1C43FFE` with
capstone 5.0.9 and pickle protocol 4.

## Disassembly helper: `tools/rdis.py`

```powershell
python tools\rdis.py 0x6FD770 +40          # 40 instructions from 0x6FD770 (addresses are hex)
python tools\rdis.py 0x6F80C0 0x6F8130     # a range
python tools\rdis.py callers 0x70FE10      # call/jmp sites targeting an address
python tools\rdis.py refs 0xce08f0         # instructions whose operands contain the text
python tools\rdis.py refs 0xce08f0 0x700000 0x720000
python tools\rdis.py dword 0xCB4098 2      # dwords from the original file -> 0xf 0x1e
```

## Particle monitor: `tools/psmon.py`

A read-only monitor for a running game (Windows only, standard library only). It opens the game process with
`PROCESS_VM_READ` and only calls `ReadProcessMemory`; it never writes to the game.

```powershell
python tools\psmon.py --seconds 15            # run while RA3 is in a game
python tools\psmon.py --seconds 30 --timeout 300 --out build\psmon_fire.json.gz
```

- It polls TheDisplay's frame counter and samples the particle manager's system list once per drawn frame. Each
  sample records template parameters, storage kind, the system multipliers, emission (particles created), and live
  and drawn (not yet dead) GPU particle counts. Drawn counts are computed for systems with at most 32 records. Every
  frame also stores the particle LOD state and the status flags of the drawables that own particle systems (including
  the in-view bit that gates emission).
- `--seconds` counts only frame-advancing time, so pauses and alt-tab do not count. `--timeout` is the wall-clock
  limit, including waiting for the game process and for a match to start. `--pid` selects a process.
- The output (`build\psmon_<stock|patched>_<time>.json.gz` by default) holds every frame. The console summary groups
  system instances per drawable, with emission totals, zero-count frames and the longest run of frames with no drawn
  GPU particle. Captures of the same scene on the stock and the patched game can be compared directly.
- If the game runs elevated, run the monitor from an elevated prompt as well.

## Shader disassembler: `tools/sm3dis.py`

Disassembles Direct3D 9 shader bytecode (vs/ps 1.x–3.0) with constant names from the CTAB. Use it on effect files
extracted from the game's `Shaders.big` (for example `gpuparticle.fxo`; extraction is not covered here):

```powershell
python tools\sm3dis.py gpuparticle.fxo --scan      # offsets of the shaders inside the effect file
python tools\sm3dis.py gpuparticle.fxo 0x53c8      # disassemble one of them
```

Keep extracted game files out of the repository (`*.fxo`, `*.bin` and `vs_*.txt` are ignored).

Optional decompiler: [REA](https://github.com/morluto/rea) with its Ghidra provider can decompile functions. Analyze
a copy of the original binary renamed to `.exe` in an ignored folder (e.g. `target\ra3_1.12.exe`).

## Changing the patch

1. Find the sites with `rdis.py` and note the exact original bytes.
2. Add a `patch_*` function to `ra3fps/ra3_fps_patch.py` and call it from `build_patch()`:
   - `check()` every original byte sequence before writing;
   - allocate caves through `Caves.put()` / `Caves.code()` and build code with `Asm`.

   Caves are allocated in call order, so adding, removing or resizing a cave shifts every cave after it.
3. Document the change in [how-it-works.md](how-it-works.md) (and [particles.md](particles.md) for particle code).
4. Build both frame rates, run `tools\verify_build.py` and the tests, update the reference hashes above, then test in
   game.
