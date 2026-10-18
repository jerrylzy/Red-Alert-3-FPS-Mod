r"""High-frame-rate patch for Command & Conquer: Red Alert 3 v1.12: 90 FPS (or 60 FPS) with game logic unchanged.

Target: the signed 2012 ra3_1.12.game of the EA App / Steam release (SHA-256 in ORIG_SHA256). Every build starts from
the pristine original (<game>.bak, or the game file itself while it is still unmodified) and checks its SHA-256 first.
The .bak is never modified or deleted.

Engine model (from disassembly):
  * Game logic runs at LOGIC_FPS = 15 Hz ([0xCB4098]); every logic step is split into 6 sub-frames.
  * Each drawn frame advances 6 / r sub-frames, r = FPS_CAP / LOGIC_FPS ([0xCB409C] = 30 -> r = 2).
  * TheDisplay's frame counter ([display+0x48]) advances once per drawn frame. Client code (particles, texture
    animations, drawable tints, unit suspension and wobble, UI timers, camera scrolling) counts drawn frames and
    assumes 30 per second.

Changes (applied in this order, which also fixes the layout of the code caves):
  pacing              the 6 reads of FPS_CAP that compute r -> read-only constant 90 (or 60).
  scheduler           (60 FPS only) r = 4 does not divide 6: the sub-frame scheduler at 0x65517E rounds up instead
                      of down, so 60 FPS alternates 1 and 2 sub-frames per drawn frame (logic stays at 15 Hz).
  render helpers      ms-per-frame, frames-per-ms, float fps, the 1000/fps sync step and 0x57FF09 use the new rate;
                      the FX multiplier at 0x6FFDAA stays 0.03 (GPU particle death times are in 30 Hz units).
  display timers      display-frame timers and durations that read FPS_CAP use the new rate.
  particle stepping   every particle system runs its full step at 30 Hz (per-system cave at the step call 0x7102BA).
                      Continuous emitters also run a partial step (transforms + emission, no modules/lifetime) on the
                      drawn frames in between, with emission counts divided by fps/30 (cave at 0x6FD821), so new
                      particles appear on every drawn frame. The step mode byte lives in the zero-filled tail of .data
                      (its VirtualSize is raised by 0x10).
  particle lifetimes  the GPU particle lifetime L becomes L' = n*s - u/2 in the record ctor (0x6FFDB0) and the
                      vertex writer (0x6F80C5), n = floor(L/0.99) + 1 (frames that show the particle at 30 FPS),
                      s/u = sync units per particle step / drawn frame, so every GPU particle stays visible exactly as
                      long as at 30 FPS.
  camera scrolling    edge/keyboard scrolling (0xA54CB0) and right-drag scrolling (0xA54B80) scaled by 30/fps.
  unit physics        the per-drawable suspension, tilt and wobble physics (dispatcher 0x5742B0) steps every fps/30
                      drawn frames; the frames in between re-apply the tilt of the last step, cached at the end of the
                      drawable's physics state (all four allocations of it, including save-game loading, get 0x88
                      instead of 0x70 bytes).
  position history    after logic moves a unit, its drawable re-syncs the interpolation history for 6 drawn frames
                      (0x570C2D, 0x56AD20); the window becomes 6*fps/30 frames (3 logic steps, as at 30 FPS), so the
                      history of a stopping unit settles and its suspension does not hold a braking tilt.
  particle timers     three elapsed-frame comparisons in particle modules scale elapsed drawn frames by 30/fps.
  tints               drawable tint/flash envelopes and the brightness-pulse period scaled by fps/30.

The extra code lives in the zero-filled tail of .text (its VirtualSize is raised to the raw size so it is mapped).

Usage:
  python ra3_fps_patch.py --dry-run --out build\ra3_1.12_90fps.game   build a file; the game is untouched
  python ra3_fps_patch.py --fps 60 --dry-run                          build build\ra3_1.12_60fps.game
  python ra3_fps_patch.py                                             patch the game in place (elevated prompt)
"""
import argparse
import hashlib
import os
import shutil
import struct
import sys

DEFAULT_GAME = r'C:\Program Files\EA Games\Command and Conquer Red Alert 3\Red Alert 3\Data\ra3_1.12.game'
BUILD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'build')
ORIG_SHA256 = '07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28'
IB = 0x400000
FPS_CAP, LOGIC_FPS = 0xCB409C, 0xCB4098
DISPLAY_PTR = 0xCE08F0                    # TheDisplay; frame counter at [display+0x48]
# Unreferenced aligned dwords in .rdata (read-only) holding the target value.
CONST_VA = {90: 0xBD694C, 60: 0xBD6D64}
PARTICLE_RATE = 30                        # particle steps per second (the stock rate)

# (operand file offset, expected opcode bytes before it): reads of FPS_CAP that compute r = FPS_CAP / LOGIC_FPS
PACING = [
    (0x2686F9, b'\x8b\x35'),  # 0x6686F7 frame pacer: mov esi,[fps]
    (0x23EA21, b'\xa1'),      # 0x63EA20 "first drawn frame of logic step" test
    (0x255167, b'\xa1'),      # 0x655166 sub-frames per drawn frame
    (0x24167E, b'\xa1'),      # 0x64167D engine[+0x5C] = fps/logic (per logic step)
    (0x25A45C, b'\xa1'),      # 0x65A45B engine[+0x5C] = fps/logic (game start)
    (0x26828E, b'\xa1'),      # 0x66828D engine[+0x5C] = fps/logic (engine init)
]
SCHED_OFF = 0x25517E
SCHED_ORIG = bytes.fromhex('8d4eff0fafcfb8abaaaa2af7e98bcac1e91f8d440a01')
SCHED_CEIL = bytes.fromhex('8d4eff0fafcf83c105b8abaaaa2af7e98d42010f1f00')  # +5 before /6, drop sign fix

RENDER_HELPERS = [
    (0x7B40A8, b'\xdb\x05'),  # ms_per_frame = 1000/fps
    (0x7B40D8, b'\xdb\x05'),  # frames_per_ms = fps*0.001
    (0x7B4108, b'\xdb\x05'),  # float fps (and 1/fps derived from it)
    (0x7B4C29, b'\xf7\x35'),  # 0xCE690C = 1000/fps (int ms per frame, W3D sync step)
    (0x17FF0A, b'\xa1'),      # 0x57FF09 obj[+0x1DC] = fps
]
FX_FMUL_OPERAND, FRAMES_PER_MS, CONST_0_03 = 0x2FFDAC, 0xCE0DFC, 0xBE65E8

# Reads of FPS_CAP used with the display (drawn-frame) counter or as drawn-frame durations.
DISPLAY_TIMERS = [
    (0x15346C, b'\x8b\x0d'),  # 0x553440 frame % (fps/2)
    (0x7B3741, b'\xa1'),      # 0xBB3740 static copy fps/2 -> 0xCE0AA0 (0x57BE40 frame % N)
    (0x628F95, b'\x8b\x0d'),  # 0xA28F80 next = frame + fps/2
    (0x15BCF6, b'\xf7\x35'),  # 0x55BCD0 blink: frame*k/fps
    (0x619EC8, b'\x8b\x35'),  # 0xA19C30 blink: frame % (2*fps)
    (0x650F18, b'\x3b\x0d'),  # 0xA50EE0 elapsed < fps
    (0x6510A8, b'\x3b\x05'),  # 0xA51080 elapsed < fps
    (0x16B3EF, b'\x8b\x0d'), (0x16B3F5, b'\xdb\x05'),  # 0x56B3C0 fps * seconds
    (0x22A756, b'\xa1'),     (0x22A75C, b'\xdb\x05'),  # 0x62A640 elapsed * 30/fps
    (0x61918D, b'\xa1'),     (0x619193, b'\xdb\x05'),  # 0xA19160 elapsed / fps
    (0x626E80, b'\xa1'),     (0x626E86, b'\xdb\x05'),  # 0xA26E50 fps * seconds
    (0x642AD6, b'\x8b\x15'), (0x642ADC, b'\xdb\x05'),  # 0xA42A80 fps * 0.5
    (0x642C50, b'\x8b\x0d'), (0x642C56, b'\xdb\x05'),  # 0xA42A80 elapsed / fps
    (0x646FFF, b'\xdb\x05'), (0x647004, b'\xa1'),      # 0xA46F80 fps * seconds
    (0x6AB97D, b'\xdb\x05'), (0x6AB983, b'\x8b\x0d'),  # 0xAAB950 fps * seconds
    (0x6ADA36, b'\x8b\x0d'),                            # 0xAADA20 fps * 10 window
    (0x7B5D91, b'\xa1'),     (0x7B5D97, b'\xdb\x05'),  # 0xBB5D90 [0xCE8CA8] = fps * 30 (static)
    (0x287EFD, b'\xdb\x05'), (0x287F05, b'\x8b\x15'),  # 0x687EC0 [0xCE8CA8] = fps * seconds (runtime)
    (0x15CB8F, b'\xdb\x05'), (0x15CB94, b'\xa1'),      # 0x55CB60 drawable duration fps * 0.75
    (0x40D076, b'\xa1'),                                # 0x80CEB0 drawable effect duration fps/2
    (0x60C1BC, b'\xdb\x05'), (0x60C1C2, b'\x8b\x15'),  # 0xA0C1A0 countdown fps * seconds
    (0x13D97B, b'\xdb\x05'), (0x13D980, b'\xa1'),      # 0x53D970 fps * duration
]

# Code caves: zero-filled tail of .text (file 0x7C641A..0x7C7000).
TEXT_VS_ORIG, TEXT_RAW_SIZE = 0x7C541A, 0x7C6000
TEXT_TAIL_FILE = 0x7C641A                 # file offset of the end of .text's original VirtualSize
CAVE_FILE_START, CAVE_FILE_END = 0x7C6420, 0x7C7000

# Per-system step call inside the simulate-all loop (0x7102BA call 0x70FE10, ecx = system, 1 stack arg).
SYS_STEP_CALL_OFF, SYS_STEP_TARGET = 0x3102BA, 0x70FE10
# Emission 0x6FD770 (only called by the step) computes count = ftol(rand(template+0xA8) * sys[+0x50]) at 0x6FD821
# with esi = emitting system (the stepped system, then its slave chain via [sys+0x114]).
EMIT_COUNT_CALL_OFF, FTOL = 0x2FD821, 0x4D3CF0
# Step block running the update modules and the storage/lifetime update (0x710043..0x71008D):
# mov ecx,[esi+0x24]; test ecx,ecx | je 0x710051. Stack there: edi, ebx, esi, 0x34 bytes of locals, ret, arg.
STEP_MODULES_OFF, STEP_MODULES_ORIG, STEP_MODULES_RESUME = 0x310043, bytes.fromhex('8b4e2485c9'), 0x710048
# Burst delay = trunc(rand(template+0x9C)), random variable {type, low, high}: high < 1.0 -> emits every step.
TMPL_DELAY_HIGH, ONE_F_BITS = 0xA4, 0x3F800000
# Step mode byte in the zero-filled tail of .data (VirtualSize raised to cover it):
# 0 = normal, 1 = partial step (transforms + emission only), 2 = full step; non-zero divides emission counts.
DATA_VA, DATA_VSIZE = 0xCAB000, 0x85BA0
STEP_MODE_VA = 0xD30BA0
# GPU particle lifetime L (int, 30 Hz steps) at [rec+0x28] is turned into float twice: the record ctor computes
# death [rec+0x48] = syncMs*0.03 + L, the vertex writer writes birth = death - L and lifetime L for the shader
# (alive while age <= L). Stock shows a particle on frames 0..L (one frame per step); with steps every few drawn
# frames it would vanish up to a step early, e.g. a sprite re-emitted every L+1 steps would blink off before its
# replacement. The patch uses L' = n*s - u/2 with n = floor(L/0.99) + 1 (frames that show the particle at 30 FPS),
# s = sync units per particle step, u = sync units per drawn frame.
GPU_LIFE_SITES = [  # (file offset, original bytes, record register modrm for [reg+0x28], bytes to keep after call)
    (0x2FFDB0, bytes.fromhex('85c0db46287d06d805b48cbe00'), 0x46, b''),          # ctor: test eax,eax; fild [esi+0x28]...
    (0x2F80C5, bytes.fromhex('8b4228db422885c0567d06d805b48cbe00'), 0x42, b'\x56'),  # vertex writer (keeps push esi)
]
UINT_FIX = 0xBE8CB4  # 2^32, the unsigned-int fixup the original code adds for negative values
NOP8, NOP3 = bytes.fromhex('0f1f840000000000'), bytes.fromhex('0f1f00')
# Camera scrolling (0xA54CB0 edge/keyboard update, 0xA54B80 right-drag update, scroll factors [0xCB4A00]/[0xCB4A04]):
# edge and keyboard scrolling move factor*100*(drawn frames since the last update) per update, right-drag moves
# (cursor - anchor)*factor per drawn frame, so both would run fps/30 times too fast. The 100.0 at 0xC7A374 is only
# used by these four scroll sites; they are redirected to 100*30/fps.
SCROLL_100_VA = 0xC7A374
SCROLL_100_OPERANDS = [  # (file offset of the absolute operand, opcode bytes before it)
    (0x6512A8, b'\xf3\x0f\x59\x0d'),  # 0xA512A4 keyboard axis: mulss xmm1,[100.0]
    (0x6512F8, b'\xf3\x0f\x59\x0d'),  # 0xA512F4 keyboard axis
    (0x651638, b'\xf3\x0f\x10\x1d'),  # 0xA51634 edge scroll while accelerating: movss xmm3,[100.0]
    (0x654EB7, b'\xf3\x0f\x10\x1d'),  # 0xA54EB3 edge scroll at full speed
]
SCROLL_FACTOR_X, SCROLL_FACTOR_Y = 0xCB4A00, 0xCB4A04
SCROLL_RMB_OFF = 0x654BDE  # 0xA54BDE movss xmm0,[factor x]; movss xmm1,[factor y] (only jump target: its start)
SCROLL_RMB_ORIG = bytes.fromhex('f30f1005004acb00f30f100d044acb00')
# Unit physics (Generals' Drawable::calcPhysicsXform): applyPhysicsXform 0x577530 gets the tilt to draw from the
# dispatcher 0x5742B0 (thiscall drawable, arg = 4-float info {pitch, roll, yaw, z}). The dispatcher steps the
# locomotor's appearance physics (suspension, pitch/roll spring-dampers, wobble 0x55BA20; 30 FPS constants) at most
# once per drawn frame per locomotor: if [locomotor+0xBC] == frame it returns false (no tilt). The patch steps it
# every fps/30 frames and returns the tilt of the last step in between, cached at the end of the drawable's physics
# state [drawable+0x208] (0x70 bytes, grown to 0x88: +0x70 step frame, +0x74 locomotor, +0x78..+0x87 tilt).
LOCO_GUARD_OFF = 0x1742F1  # 0x5742F1 cmp [esi+0xbc],ebp; je 0x57437D (esi = locomotor, ebp = frame, edi = drawable)
LOCO_GUARD_ORIG = bytes.fromhex('39aebc0000000f8480000000')
LOCO_STEP_VA, LOCO_RET_VA = 0x5742FD, 0x57437D  # step path; epilogue returning bl
LOCO_CALLS = [(0x17432E, 0x568220), (0x174346, 0x55FED0), (0x17435E, 0x568220), (0x174376, 0x567920)]
LOCO_ALLOC_CALLS = [  # push 0x70; call 0x416970 (each followed by the state ctor 0x55B5A0)
    0x15FEDE, 0x167934, 0x168233,  # lazy allocation in the three appearance functions
    0x17C72A,                      # Drawable::xfer when a saved game is loaded (xfer this = drawable + 0x60)
]
ALLOC_VA, LOCO_STATE_OFF, LOCO_CACHE_OFF, LOCO_ALLOC_SIZE = 0x416970, 0x208, 0x70, 0x88
# Position history: after logic moves an object, the drawable update 0x570C00 stores the drawn frame in
# [drawable+0x130] and, for the next 6 drawn frames, re-syncs its 4-point position history (+0xC4..+0xF3; sync
# 0x56AD00) and interpolates. 6 frames are 3 logic steps at 30 FPS but 1 at 90 FPS. The wheel physics reads the last
# history step (+0xDC - +0xD0) as the unit's motion, so with the short window a stopping unit holds a braking tilt.
INTERP_WINDOW_SITES = [0x170C2F, 0x16AD22]  # imm8 of cmp eax,6 at 0x570C2D and 0x56AD20 -> 6*fps/30
# (site file offset, original bytes, cave prologue, cave epilogue-before-ret, min1 threshold reg-form)
PARTICLE_DIFF_SITES = [
    # 0x6F9450 texture animator: sub eax,[esi+8]; cmp eax,[esi+0x18]  (delay 0 behaves like 1)
    (0x2F9450, bytes.fromhex('2b46083b4618'), bytes.fromhex('2b4608'), 'min1', bytes.fromhex('8b4e18')),
    # 0x6F4A50 delayed attach: sub eax,[edi+0x2c]; cmp eax,[esi+0x10]
    (0x2F4A50, bytes.fromhex('2b472c3b4610'), bytes.fromhex('2b472c'), 'cmp', bytes.fromhex('3b4610')),
    # 0x6FB4D7 system-age trigger: sub eax,ebp; cmp eax,[esi+0xc]
    (0x2FB4D7, bytes.fromhex('2bc53b460c'), bytes.fromhex('2bc5'), 'cmp', bytes.fromhex('3b460c')),
]
# Drawable tint envelopes are stepped once per drawn frame (TintEnvelope::update 0x56AB50, called from
# Drawable::update 0x57BE40); their frame counts assume 30 Hz. Scale the hard-coded ones by fps/30.
TINT_IMM8 = [  # (file offset of push imm8, original value)
    (0x16F7B2, 1), (0x16F7B4, 4), (0x16F7B6, 1),  # 0x56F7B1 default flash: sustain, decay, attack
    (0x16F9CA, 30), (0x16F9CC, 30),               # 0x56F9C7 dark 0.25 status tint (sustain -2 kept)
    (0x16FA0E, 30), (0x16FA10, 30),               # 0x56FA08 bright status tint
    (0x16FA6F, 30), (0x16FA71, 30),               # 0x56FA69 bright status tint (sustain 9999999 kept)
    (0x16FBA8, 30), (0x16FBAA, 30),               # 0x56FBA5 gray 0.8 status tint (sustain -2 kept)
    (0x16FFC5, 1), (0x16FFC7, 4),                 # 0x56FFC4 selection flash: sustain, decay
]
TINT_IMM32 = [(0x16FA09, 300)]                    # 0x56FA08 sustain 300 frames
# 0x56F6F0 flash(color, a, b, c) forwards caller frame counts (4; 4/4/15; ...) to the envelope.
FLASH_FWD_OFF, FLASH_FWD_RET = 0x16F79F, 0x56F7CE
FLASH_FWD_ORIG = bytes.fromhex('8b4c24248b54241c518b4c2424525150')
# 0x562C58 fld [0xBF3704] (1000.0): template tint durations -> frames
TINT_MS_OPERAND, TINT_MS_CONST = 0x162C5A, 0xBF3704
# Drawable brightness pulse (0x5740D0): sin(frame % period) and a period-long transition countdown,
# period = [[0xCEE998]+0x4C] in 30 Hz frames. Reads are scaled by fps/30 via caves.
PULSE_SITES = [  # (file offset, original bytes, cave kind)
    (0x1740EF, bytes.fromhex('8b784c85ff'), 'edi_test'),            # mov edi,[eax+0x4c]; test edi,edi
    (0x170017, bytes.fromhex('8b1598e9ce008b4a4c'), 'ecx_keepflags'),  # flags/eax live (ja, jump table)
    (0x174264, bytes.fromhex('8b1598e9ce008b424c'), 'eax'),
]
CEE998 = 0xCEE998


class PatchError(Exception):
    """Unexpected input bytes or an unusable file."""


def check(cond, msg):
    # Explicit checks instead of assert, so they also run under python -O.
    if not cond:
        raise PatchError(msg)


def sha256(data):
    return hashlib.sha256(data).hexdigest().upper()


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def find_original(game, create_bak=False):
    """Return (path, data) of the pristine v1.12 file belonging to `game`: game + '.bak' if it exists, otherwise the
    game file itself while it is still unmodified (copied to game + '.bak' first when create_bak is set)."""
    bak = game + '.bak'
    if os.path.exists(bak):
        data = read(bak)
        check(sha256(data) == ORIG_SHA256, f'{bak} is not the original v1.12 file (SHA-256 mismatch)')
        return bak, data
    check(os.path.exists(game), f'{game} not found (use --game to point at ra3_1.12.game)')
    data = read(game)
    check(sha256(data) == ORIG_SHA256,
          f'no {os.path.basename(bak)} next to the game, and {game} is not the original v1.12 file')
    if not create_bak:
        return game, data
    shutil.copy2(game, bak)
    check(sha256(read(bak)) == ORIG_SHA256, f'backup copy {bak} does not verify')
    return bak, data


def u32(v):
    return struct.pack('<I', v & 0xFFFFFFFF)


def rel32(site_va, target_va):
    return struct.pack('<i', target_va - (site_va + 5))


def section_header(d, name):
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    sec = pe + 24 + struct.unpack_from('<H', d, pe + 20)[0]
    for k in range(struct.unpack_from('<H', d, pe + 6)[0]):
        if d[sec + 40 * k:sec + 40 * k + 8].rstrip(b'\0') == name:
            return sec + 40 * k
    raise PatchError(f'section {name!r} not found')


class Caves:
    """Sequential allocator for code and constants in the zero-filled tail of .text."""

    def __init__(self, d):
        self.d, self.pos = d, CAVE_FILE_START
        check(set(d[TEXT_TAIL_FILE:CAVE_FILE_END]) == {0}, '.text tail is not empty')

    def put(self, blob, align=16):
        self.pos = (self.pos + align - 1) & ~(align - 1)
        check(self.pos + len(blob) <= CAVE_FILE_END, 'out of cave space')
        off = self.pos
        self.d[off:off + len(blob)] = blob
        self.pos += len(blob)
        return off + IB

    def code(self, build):
        """build(va) -> bytes; lets code embed rel32 relative to its own address."""
        self.pos = (self.pos + 15) & ~15
        va = self.pos + IB
        return self.put(build(va))


def scale_eax_by_30_over_r(r):
    # push ecx; push edx; mov ecx,30; mul ecx; mov ecx,R; div ecx   -> eax = eax*30/R
    return b'\x51\x52\xb9' + u32(30) + b'\xf7\xe1\xb9' + u32(r) + b'\xf7\xf1'


class Asm:
    """Tiny x86 byte assembler with labels for rel8 jumps and rel32 call/jmp."""

    def __init__(self, va):
        self.va, self.items = va, []

    def b(self, data):
        self.items.append(('b', bytes(data)))
        return self

    def label(self, name):
        self.items.append(('L', name))
        return self

    def j8(self, opcode, name):
        self.items.append(('j8', opcode, name))
        return self

    def call(self, target):
        self.items.append(('rel32', 0xE8, target))
        return self

    def jmp(self, target):
        self.items.append(('rel32', 0xE9, target))
        return self

    def bytes(self):
        sizes = {'b': None, 'L': 0, 'j8': 2, 'rel32': 5}
        labels, off = {}, 0
        for it in self.items:
            if it[0] == 'L':
                labels[it[1]] = off
            else:
                off += len(it[1]) if it[0] == 'b' else sizes[it[0]]
        out = bytearray()
        for it in self.items:
            if it[0] == 'b':
                out += it[1]
            elif it[0] == 'j8':
                rel = labels[it[2]] - (len(out) + 2)
                check(-128 <= rel <= 127, f'rel8 out of range to {it[2]}')
                out += bytes([it[1], rel & 0xFF])
            elif it[0] == 'rel32':
                out += bytes([it[1]]) + struct.pack('<i', it[2] - (self.va + len(out) + 5))
        return bytes(out)


JE, JNE, JB, JLE, JGE = 0x74, 0x75, 0x72, 0x7E, 0x7D


def build_step(va, rate, fps):
    """Replacement for the simulate loop's call 0x70FE10 (ecx = system, 1 unused stack arg, returns al = alive).
    A system with storage runs its full step when (frame*rate) % fps < rate. A continuous emitter also runs a partial
    step (transforms + emission) on the other frames unless its initial delay or a burst countdown is pending; a
    periodic one only steps on full frames. Systems without storage step every drawn frame."""
    a = Asm(va)
    a.b(b'\x8b\x81' + u32(0x90) + b'\x85\xc0').j8(JE, 'run')             # storage = [ecx+0x90]
    a.b(b'\x51\xa1' + u32(DISPLAY_PTR) + b'\x8b\x40\x48\xb9' + u32(rate)  # edx = (frame*rate) % fps
        + b'\xf7\xe1\xb9' + u32(fps) + b'\xf7\xf1\x59')
    a.b(b'\x8b\x41\x08\x81\xb8' + u32(TMPL_DELAY_HIGH) + u32(ONE_F_BITS)).j8(JGE, 'periodic')
    a.b(b'\xb0\x02\x83\xfa' + bytes([rate])).j8(JB, 'split')            # mode 2: full step
    a.b(b'\x83\x79\x3c\x00').j8(JNE, 'alive')                           # initial delay counts 30 Hz steps
    a.b(b'\x83\xb9' + u32(0x104) + b'\x00').j8(JNE, 'alive')             # pending burst countdown
    a.b(b'\xb0\x01')                                                     # mode 1: partial step
    a.label('split')
    a.b(b'\xa2' + u32(STEP_MODE_VA) + b'\xff\x74\x24\x04').call(SYS_STEP_TARGET)
    a.b(b'\xc6\x05' + u32(STEP_MODE_VA) + b'\x00\xc2\x04\x00')          # mode 0; ret 4 (al from the step)
    a.label('periodic')
    a.b(b'\x83\xfa' + bytes([rate])).j8(JB, 'run')
    a.label('alive')
    a.b(b'\xb0\x01\xc2\x04\x00')                                         # mov al,1; ret 4
    a.label('run')
    a.jmp(SYS_STEP_TARGET)
    return a.bytes()


def build_step_modules(va):
    """Jumped to from 0x710043: a partial step returns 'alive' before the modules and storage/lifetime update."""
    a = Asm(va)
    a.b(b'\x80\x3d' + u32(STEP_MODE_VA) + b'\x01').j8(JE, 'partial')
    a.b(STEP_MODULES_ORIG).jmp(STEP_MODULES_RESUME)                      # flags from test ecx,ecx feed the je
    a.label('partial')
    a.b(b'\xb0\x01\x5f\x5b\x5e\x83\xc4\x34\xc2\x04\x00')               # mov al,1; pop edi/ebx/esi; ret 4
    return a.bytes()


def build_emit_count(va, r):
    """Replaces call ftol at the emission count; r = drawn frames per particle step. With the mode byte set, every
    drawn frame emits (count * j + phase) / r: phase = (frame + hash(system)) % r spreads the rounding, j = 1 except
    on an element's first emission (no particles created yet, [sys+0x110] == 0), where j = drawn frames of the current
    step period so far. Periods end with the full step (frame % r == 0), which also runs the life countdown, so a
    system that starts mid-period still emits its whole first step."""
    a = Asm(va)
    a.call(FTOL).b(b'\x80\x3d' + u32(STEP_MODE_VA) + b'\x00').j8(JE, 'ret')
    a.b(b'\x85\xc0').j8(JLE, 'ret')
    a.b(b'\x51\x52\xb9' + u32(r))                                        # push ecx; push edx; ecx = r
    a.b(b'\x83\xbe' + u32(0x110) + b'\x00').j8(JNE, 'divide')            # particles already created
    a.b(b'\x50\xa1' + u32(DISPLAY_PTR) + b'\x8b\x40\x48\x83\xc0' + bytes([r - 1])  # j = (frame + r-1) % r + 1
        + b'\x33\xd2\xf7\xf1\x42')
    a.b(b'\x58\x0f\xaf\xc2')                                             # eax = count * j
    a.label('divide')
    a.b(b'\x50\xa1' + u32(DISPLAY_PTR) + b'\x8b\x40\x48')                # push eax; eax = frame
    a.b(b'\x69\xd6\xb1\x79\x37\x9e\xc1\xea\x10\x03\xc2')                 # eax += (esi * 0x9E3779B1) >> 16
    a.b(b'\x33\xd2\xf7\xf1')                                             # edx = phase
    a.b(b'\x58\x03\xc2\x33\xd2\xf7\xf1\x5a\x59')                         # eax = (eax + phase) / r
    a.label('ret')
    a.b(b'\xc3')
    return a.bytes()


def build_gpu_life(va, modrm, k_va):
    """Replaces the float(uint L) conversion of [reg+0x28] at a GPU_LIFE_SITES site.
    st0 = n*s - u/2 with n = floor(L/0.99) + 1 = the number of 30 FPS stock frames (0.99 sync units each) that show
    a particle of lifetime L; huge/negative L keeps the original float(uint L). s and u/2 are floats at k_va."""
    a = Asm(va)
    a.b(b'\x51\x52\x8b' + bytes([modrm & ~0x38, 0x28]))           # push ecx; push edx; mov eax,[reg+0x28]
    a.b(b'\x3d' + u32(0x1000000)).j8(0x73, 'orig')                 # cmp eax,2^24; jae orig
    a.b(b'\x6b\xc0\x64\x33\xd2\xb9' + u32(99) + b'\xf7\xf1\x40')   # imul eax,100; xor edx,edx; div 99; inc eax
    a.b(b'\x50\xdb\x04\x24\x58')                                    # push eax; fild [esp]; pop eax
    a.b(b'\xd8\x0d' + u32(k_va) + b'\xd8\x25' + u32(k_va + 4))     # fmul s; fsub u/2
    a.b(b'\x5a\x59\xc3')                                            # pop edx; pop ecx; ret
    a.label('orig')
    a.b(b'\x5a\x59\xdb' + bytes([modrm, 0x28]) + b'\x83' + bytes([modrm | 0x38, 0x28, 0x00])).j8(JGE, 'pos')
    a.b(b'\xd8\x05' + u32(UINT_FIX))
    a.label('pos')
    a.b(b'\xc3')
    return a.bytes()


def build_flash_forwarder(va, r):
    """Detour of 0x56F6F0's forwarding block; pushes the caller's 3 frame counts as before, then scales the positive
    ones in place by r/30 and resumes at 0x56F7CE."""
    body = (b'\xff\x74\x24\x24' b'\xff\x74\x24\x20' b'\xff\x74\x24\x28' b'\x50'  # arg4, arg2, arg3, color
            + b'\xbb' + u32(30))                                                  # mov ebx,30 (ebx restored by caller epilogue)
    for k in (4, 8, 12):  # mov eax,[esp+k]; test eax,eax; jle +13; imul eax,eax,R; cdq; idiv ebx; mov [esp+k],eax
        body += (b'\x8b\x44\x24' + bytes([k]) + b'\x85\xc0\x7e\x0d' + b'\x69\xc0' + u32(r) + b'\x99\xf7\xfb'
                 + b'\x89\x44\x24' + bytes([k]))
    return body + b'\xe9' + rel32(va + len(body), FLASH_FWD_RET)


def build_loco_alloc(va):
    """Replaces call 0x416970 after push 0x70 (the caller still pops its argument): allocates LOCO_ALLOC_SIZE."""
    return Asm(va).b(b'\x68' + u32(LOCO_ALLOC_SIZE)).call(ALLOC_VA).b(b'\x83\xc4\x04\xc3').bytes()


def build_loco_wrapper(va, target):
    """Replaces the dispatcher's call of an appearance function (thiscall drawable; args locomotor, info; ret 8):
    runs it, then caches {frame (ebp), locomotor, info[4]} in the drawable's physics state."""
    c = LOCO_CACHE_OFF
    a = Asm(va)
    a.b(b'\x51' b'\xff\x74\x24\x0c' b'\xff\x74\x24\x0c')            # push ecx; push info; push locomotor
    a.call(target)
    a.b(b'\x59' b'\x8b\x81' + u32(LOCO_STATE_OFF) + b'\x85\xc0')      # pop ecx; eax = state; test eax,eax
    a.j8(JE, 'done')
    a.b(b'\x89\x68' + bytes([c]))                                    # [eax+c] = frame
    a.b(b'\x8b\x54\x24\x04' b'\x89\x50' + bytes([c + 4]))            # [eax+c+4] = locomotor
    a.b(b'\x8b\x54\x24\x08')                                         # edx = info
    for k in range(4):                                               # [eax+c+8+4k] = info[k]
        a.b(b'\x8b\x4a' + bytes([4 * k]) + b'\x89\x88' + u32(c + 8 + 4 * k))
    a.label('done').b(b'\xc2\x08\x00')                               # ret 8
    return a.bytes()


def build_loco_guard(va, n):
    """Replaces the dispatcher's once-per-frame guard (esi = locomotor, ebp = frame, edi = drawable, bl = 0, info at
    [esp+0x14]). Same frame as the locomotor's last step: no tilt, as stock. n or more frames since that step: step.
    Otherwise copy the tilt this drawable cached at that step into the info and return true (bl = 1), or no tilt if
    it has no such cache (a second drawable of the same object, as stock)."""
    c = LOCO_CACHE_OFF
    a = Asm(va)
    a.b(b'\x8b\xc5' b'\x2b\x86\xbc\x00\x00\x00')                     # eax = frame - [locomotor+0xBC]
    a.j8(JE, 'false')
    a.b(b'\x83\xf8' + bytes([n]))
    a.j8(0x73, 'step')                                               # jae step
    a.b(b'\x8b\x87' + u32(LOCO_STATE_OFF) + b'\x85\xc0')             # eax = state
    a.j8(JE, 'false')
    a.b(b'\x3b\x70' + bytes([c + 4]))                                # cached by this locomotor...
    a.j8(JNE, 'false')
    a.b(b'\x8b\x96\xbc\x00\x00\x00' b'\x3b\x50' + bytes([c]))         # ...at its last step?
    a.j8(JNE, 'false')
    a.b(b'\x8b\x4c\x24\x14')                                         # ecx = info
    for k in range(4):                                               # info[k] = [eax+c+8+4k]
        a.b(b'\x8b\x90' + u32(c + 8 + 4 * k) + b'\x89\x51' + bytes([4 * k]))
    a.b(b'\xb3\x01')                                                 # bl = 1: draw with the cached tilt
    a.label('false').jmp(LOCO_RET_VA)
    a.label('step').jmp(LOCO_STEP_VA)
    return a.bytes()


def patch_operands(d, sites, target, label):
    for off, opc in sites:
        check(d[off - len(opc):off] == opc, f'{label}: opcode mismatch at {off:#x}')
        check(struct.unpack_from('<I', d, off)[0] == FPS_CAP, f'{label}: operand mismatch at {off:#x}')
        struct.pack_into('<I', d, off, target)


def patch_scheduler(d):
    check(d[SCHED_OFF:SCHED_OFF + len(SCHED_ORIG)] == SCHED_ORIG, 'scheduler bytes mismatch')
    d[SCHED_OFF:SCHED_OFF + len(SCHED_CEIL)] = SCHED_CEIL


def patch_render_helpers(d, const_va):
    patch_operands(d, RENDER_HELPERS, const_va, 'render helpers')
    check(d[FX_FMUL_OPERAND - 2:FX_FMUL_OPERAND] == b'\xd8\x0d', 'FX multiplier opcode mismatch')
    check(struct.unpack_from('<I', d, FX_FMUL_OPERAND)[0] == FRAMES_PER_MS, 'FX multiplier operand mismatch')
    struct.pack_into('<I', d, FX_FMUL_OPERAND, CONST_0_03)


def patch_particle_stepping(d, caves, R):
    N = PARTICLE_RATE
    check(R % N == 0 and R // N >= 2, 'fps must be a multiple of the particle rate')
    site_va = SYS_STEP_CALL_OFF + IB
    check(d[SYS_STEP_CALL_OFF] == 0xE8, 'system step call mismatch')
    check(struct.unpack_from('<i', d, SYS_STEP_CALL_OFF + 1)[0] + site_va + 5 == SYS_STEP_TARGET,
          'system step call target mismatch')
    # step mode byte: first bytes past .data's VirtualSize, never referenced by the original code
    hdr = section_header(d, b'.data')
    check(struct.unpack_from('<II', d, hdr + 8) == (DATA_VSIZE, DATA_VA - IB), '.data header mismatch')
    check(DATA_VA + DATA_VSIZE <= STEP_MODE_VA
          and STEP_MODE_VA + 0x10 <= (DATA_VA + DATA_VSIZE + 0xFFF) & ~0xFFF, 'mode byte outside .data tail')
    check(all(d.find(u32(STEP_MODE_VA + i)) < 0 for i in range(0x10)), 'mode byte address already referenced')
    struct.pack_into('<I', d, hdr + 8, STEP_MODE_VA + 0x10 - DATA_VA)
    count_va = EMIT_COUNT_CALL_OFF + IB
    check(d[EMIT_COUNT_CALL_OFF] == 0xE8, 'emission count call mismatch')
    check(struct.unpack_from('<i', d, EMIT_COUNT_CALL_OFF + 1)[0] + count_va + 5 == FTOL,
          'emission count call target mismatch')
    count_cave = caves.code(lambda va: build_emit_count(va, R // N))
    d[EMIT_COUNT_CALL_OFF:EMIT_COUNT_CALL_OFF + 5] = b'\xe8' + rel32(count_va, count_cave)
    check(d[STEP_MODULES_OFF:STEP_MODULES_OFF + 5] == STEP_MODULES_ORIG, 'step modules bytes mismatch')
    check(d[STEP_MODULES_OFF + 5:STEP_MODULES_OFF + 7] == b'\x74\x07', 'step modules branch mismatch')
    mod_cave = caves.code(build_step_modules)
    d[STEP_MODULES_OFF:STEP_MODULES_OFF + 5] = b'\xe9' + rel32(STEP_MODULES_OFF + IB, mod_cave)
    cave = caves.code(lambda va: build_step(va, N, R))
    d[SYS_STEP_CALL_OFF:SYS_STEP_CALL_OFF + 5] = b'\xe8' + rel32(site_va, cave)


def patch_particle_lifetimes(d, caves, R):
    u = (1000 // R) * 0.03                     # sync units per drawn frame (the W3D sync step is whole ms)
    s = u * R / PARTICLE_RATE                  # sync units per particle step
    k_va = caves.put(struct.pack('<ff', s, u / 2), align=4)
    for off, orig, modrm, keep in GPU_LIFE_SITES:
        check(d[off:off + len(orig)] == orig, f'gpu life site mismatch at {off:#x}')
        cave = caves.code(lambda va, modrm=modrm: build_gpu_life(va, modrm, k_va))
        pad = len(orig) - 5 - len(keep)
        filler = NOP8 + NOP3 if pad == 11 else NOP8 if pad == 8 else None
        check(filler is not None and len(filler) == pad, f'no filler for {pad} bytes at {off:#x}')
        d[off:off + len(orig)] = b'\xe8' + rel32(off + IB, cave) + keep + filler


def patch_scroll(d, caves, R):
    c100 = caves.put(struct.pack('<f', 100.0 * 30 / R), align=4)
    k = caves.put(struct.pack('<f', 30.0 / R), align=4)
    for off, opc in SCROLL_100_OPERANDS:
        check(d[off - len(opc):off] == opc, f'scroll: opcode mismatch at {off:#x}')
        check(struct.unpack_from('<I', d, off)[0] == SCROLL_100_VA, f'scroll: operand mismatch at {off:#x}')
        struct.pack_into('<I', d, off, c100)
    check(d[SCROLL_RMB_OFF:SCROLL_RMB_OFF + 16] == SCROLL_RMB_ORIG, 'scroll: right-drag factor loads mismatch')
    # movss xmm0,[factor x]; movss xmm1,[factor y]; mulss xmm0,[k]; mulss xmm1,[k]; ret
    rmb = caves.put(b'\xf3\x0f\x10\x05' + u32(SCROLL_FACTOR_X) + b'\xf3\x0f\x10\x0d' + u32(SCROLL_FACTOR_Y)
                    + b'\xf3\x0f\x59\x05' + u32(k) + b'\xf3\x0f\x59\x0d' + u32(k) + b'\xc3')
    d[SCROLL_RMB_OFF:SCROLL_RMB_OFF + 16] = (b'\xe8' + rel32(SCROLL_RMB_OFF + IB, rmb)
                                             + bytes.fromhex('0f1f8400000000000f1f00'))


def patch_unit_physics(d, caves, R):
    n = R // 30
    check(d[LOCO_GUARD_OFF:LOCO_GUARD_OFF + len(LOCO_GUARD_ORIG)] == LOCO_GUARD_ORIG, 'unit physics guard mismatch')
    alloc_cave = caves.code(build_loco_alloc)
    for off in LOCO_ALLOC_CALLS:
        check(d[off - 2:off] == b'\x6a\x70' and d[off + 5:off + 8] == b'\x83\xc4\x04',
              f'unit physics state allocation mismatch at {off:#x}')
        check(d[off] == 0xE8 and struct.unpack_from('<i', d, off + 1)[0] + off + IB + 5 == ALLOC_VA,
              f'unit physics state allocation target mismatch at {off:#x}')
        d[off:off + 5] = b'\xe8' + rel32(off + IB, alloc_cave)
    wrappers = {t: caves.code(lambda va, t=t: build_loco_wrapper(va, t)) for t in sorted({t for _, t in LOCO_CALLS})}
    for off, target in LOCO_CALLS:
        check(d[off] == 0xE8 and struct.unpack_from('<i', d, off + 1)[0] + off + IB + 5 == target,
              f'unit physics call mismatch at {off:#x}')
        d[off:off + 5] = b'\xe8' + rel32(off + IB, wrappers[target])
    cave = caves.code(lambda va: build_loco_guard(va, n))
    d[LOCO_GUARD_OFF:LOCO_GUARD_OFF + len(LOCO_GUARD_ORIG)] = (b'\xe9' + rel32(LOCO_GUARD_OFF + IB, cave)
                                                               + bytes.fromhex('0f1f8000000000'))


def patch_position_history(d, R):
    for off in INTERP_WINDOW_SITES:
        check(d[off - 2:off + 1] == b'\x83\xf8\x06', f'position history window mismatch at {off:#x}')
        d[off] = 6 * R // 30


def patch_particle_timers(d, caves, R):
    for off, orig, sub_insn, mode, tail in PARTICLE_DIFF_SITES:
        check(d[off:off + len(orig)] == orig, f'particle site mismatch at {off:#x}')
        if mode == 'min1':
            # ...; mov ecx,[thr]; test ecx,ecx; jnz +1; inc ecx; cmp eax,ecx; pop edx; pop ecx; ret
            body = sub_insn + scale_eax_by_30_over_r(R) + tail + b'\x85\xc9\x75\x01\x41\x3b\xc1\x5a\x59\xc3'
        else:
            # ...; pop edx; pop ecx; cmp eax,[thr]; ret
            body = sub_insn + scale_eax_by_30_over_r(R) + b'\x5a\x59' + tail + b'\xc3'
        cave = caves.put(body)
        d[off:off + len(orig)] = b'\xe8' + rel32(off + IB, cave) + b'\x90' * (len(orig) - 5)


def patch_tints(d, caves, R):
    def scaled(v):
        return v * R // 30
    for off, v in TINT_IMM8:
        check(d[off - 1] == 0x6A and d[off] == v, f'tint imm8 mismatch at {off:#x}')
        check(scaled(v) <= 127, f'scaled tint frame count {scaled(v)} does not fit imm8')
        d[off] = scaled(v)
    for off, v in TINT_IMM32:
        check(d[off - 1] == 0x68 and struct.unpack_from('<I', d, off)[0] == v, f'tint imm32 mismatch at {off:#x}')
        struct.pack_into('<I', d, off, scaled(v))
    check(d[FLASH_FWD_OFF:FLASH_FWD_OFF + len(FLASH_FWD_ORIG)] == FLASH_FWD_ORIG, 'flash forwarder mismatch')
    cave = caves.code(lambda va: build_flash_forwarder(va, R))
    d[FLASH_FWD_OFF:FLASH_FWD_OFF + len(FLASH_FWD_ORIG)] = (
        b'\xe9' + rel32(FLASH_FWD_OFF + IB, cave) + b'\xcc' * (len(FLASH_FWD_ORIG) - 5))
    # template tint ms -> frames factor 1000 -> 1000*fps/30
    check(d[TINT_MS_OPERAND - 2:TINT_MS_OPERAND] == b'\xd9\x05', 'tint ms opcode mismatch')
    check(struct.unpack_from('<I', d, TINT_MS_OPERAND)[0] == TINT_MS_CONST, 'tint ms operand mismatch')
    k_va = caves.put(struct.pack('<f', 1000.0 * R / 30), align=4)
    struct.pack_into('<I', d, TINT_MS_OPERAND, k_va)
    # brightness pulse period reads
    div30_eax = b'\x99\xb9' + u32(30) + b'\xf7\xf9'          # cdq; mov ecx,30; idiv ecx
    load_period = b'\x8b\x15' + u32(CEE998) + b'\x8b\x42\x4c' + b'\x69\xc0' + u32(R)  # edx=[g]; eax=[edx+4c]*R
    for off, orig, kind in PULSE_SITES:
        check(d[off:off + len(orig)] == orig, f'pulse site mismatch at {off:#x}')
        if kind == 'edi_test':   # mov edi,[eax+4c]; imul edi,edi,R; mov eax,edi; cdq; mov edi,30; idiv edi; mov edi,eax; test edi,edi
            body = (b'\x8b\x78\x4c' + b'\x69\xff' + u32(R) + b'\x8b\xc7\x99\xbf' + u32(30) + b'\xf7\xff'
                    + b'\x8b\xf8\x85\xff\xc3')
        elif kind == 'ecx_keepflags':  # pushfd; push eax; ...; mov ecx,eax; pop eax; popfd
            body = b'\x9c\x50' + load_period + div30_eax + b'\x8b\xc8\x58\x9d\xc3'
        else:
            body = load_period + div30_eax + b'\xc3'
        cave = caves.put(body)
        d[off:off + len(orig)] = b'\xe8' + rel32(off + IB, cave) + (b'\x0f\x1f\x40\x00' if len(orig) == 9 else b'')


def build_patch(src, fps):
    """Return the patched image (bytearray) of the original file contents `src` for 90 or 60 FPS."""
    check(fps in CONST_VA, f'unsupported fps {fps}')
    d = bytearray(src)
    R = fps
    const_va = CONST_VA[R]
    check(0x7C7000 <= const_va - IB < 0x8AB000, 'constant must be in .rdata')
    check(struct.unpack_from('<I', d, const_va - IB)[0] == R, f'constant {R} not found at {const_va:#x}')
    check(struct.unpack_from('<I', d, LOGIC_FPS - IB)[0] == 15, 'LOGIC_FPS is not 15')
    check(struct.unpack_from('<I', d, FPS_CAP - IB)[0] == 30, 'FPS_CAP is not 30')

    patch_operands(d, PACING, const_va, 'pacing')
    if 6 % (R // 15):
        patch_scheduler(d)
    patch_render_helpers(d, const_va)
    patch_operands(d, DISPLAY_TIMERS, const_va, 'display timers')
    # The order of the cave users below determines the cave layout (and so the output bytes).
    caves = Caves(d)
    patch_particle_stepping(d, caves, R)
    patch_particle_lifetimes(d, caves, R)
    patch_scroll(d, caves, R)
    patch_unit_physics(d, caves, R)
    patch_position_history(d, R)
    patch_particle_timers(d, caves, R)
    patch_tints(d, caves, R)
    sec = section_header(d, b'.text')
    check(struct.unpack_from('<I', d, sec + 8)[0] == TEXT_VS_ORIG, '.text VirtualSize mismatch')
    struct.pack_into('<I', d, sec + 8, TEXT_RAW_SIZE)
    return d


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog='ra3_fps_patch.py',
        description='Patch Red Alert 3 v1.12 (ra3_1.12.game) to render at 90 or 60 FPS; game logic stays at 15 Hz.')
    ap.add_argument('--fps', type=int, default=90, choices=sorted(CONST_VA, reverse=True),
                    help='target frame rate (default 90)')
    ap.add_argument('--game', default=DEFAULT_GAME,
                    help='path to ra3_1.12.game; the pristine original is read from GAME.bak, or from GAME itself '
                         'while it is unmodified (default: %(default)s)')
    ap.add_argument('--dry-run', action='store_true', help='write the result to --out; the game folder is not touched')
    ap.add_argument('--out', help='output file (implies --dry-run; default: build/ra3_1.12_<fps>fps.game)')
    return ap.parse_args(argv)


def same_file(a, b):
    a, b = os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b))
    return a == b or (os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b))


def main(argv=None):
    args = parse_args(argv)
    game = os.path.abspath(args.game)
    bak = game + '.bak'
    dry = args.dry_run or args.out is not None
    if dry:
        out = os.path.abspath(args.out or os.path.join(BUILD_DIR, f'ra3_1.12_{args.fps}fps.game'))
        if out.lower().endswith('.bak') or same_file(out, game) or same_file(out, bak):
            sys.exit(f'error: refusing to write {out}; --out must not be the game file or a backup '
                     '(omit --dry-run/--out to patch the game in place)')
    elif game.lower().endswith('.bak'):
        sys.exit('error: refusing to patch a .bak file in place')
    try:
        src_path, src = find_original(game, create_bak=not dry)
        d = build_patch(src, args.fps)
        changed = sum(x != y for x, y in zip(src, d))
        if dry:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as f:
                f.write(d)
            print(f'dry run -> {out}')
            print(f'source  : {src_path} (original v1.12, SHA-256 verified)')
            print(f'sha256  : {sha256(d)}')
            print(f'{args.fps} FPS | bytes changed: {changed}')
            return
        with open(game, 'wb') as f:
            f.write(d)
    except PatchError as e:
        sys.exit(f'error: {e}')
    except PermissionError as e:
        sys.exit(f'error: {e}\nWriting to the game folder needs an elevated prompt. Alternatively build with '
                 '--dry-run --out FILE and install it with install.ps1.')
    print('bak sha256 :', sha256(read(bak)))
    print('live sha256:', sha256(read(game)))
    print(f'{args.fps} FPS | bytes changed vs original: {changed}')


if __name__ == '__main__':
    main()
