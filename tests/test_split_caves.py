"""Unicorn emulation tests for the particle-stepping code caves (30 Hz steps with split emission).

Runs the exact cave bytes of a patched file and checks them against a Python model:
  * per-system step cave (call at 0x7102BA): which systems get a full, partial or no step on each drawn frame;
  * emission-count cave (call at 0x6FD821): (count * j + phase) // r with the step mode set, count unchanged otherwise;
  * step-modules cave (jmp at 0x710043): a partial step returns 'alive' before the modules and lifetime update;
then simulates system lifecycles to compare emitted totals with the stock 30 Hz step.

usage: python tests/test_split_caves.py [BUILD]    (default: build/ra3_1.12_90fps.game)
r = drawn frames per particle step is read from the count cave (3 at 90 FPS, 2 at 60 FPS). Needs unicorn.
"""
import os
import struct
import sys

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, Uc
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDI,
                               UC_X86_REG_EDX, UC_X86_REG_EFLAGS, UC_X86_REG_ESI, UC_X86_REG_ESP)

IB = 0x400000
DEFAULT_BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'build',
                             'ra3_1.12_90fps.game')
DISPLAY_PTR, MODE = 0xCE08F0, 0xD30BA0
FTOL, STEP, RESUME = 0x4D3CF0, 0x70FE10, 0x710048
STORAGE_VTABLES = (0xC22660, 0xC22C9C, 0xC221D4)  # GPU, TerrainFire, CPU storage
DISP, SYS, TMPL, STORAGE, STACK_TOP, SENTINEL = 0x20000000, 0x20010000, 0x20020000, 0x20030000, 0x2F000000, 0x30000000
FTOL_VALUE = STORAGE + 0x800
COUNT_SITE, STEP_SITE, MODULES_SITE = 0x6FD821, 0x7102BA, 0x710043
CAVE_PAGE = 0xBC6000


class Build:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.img = f.read()
        if not (self.byte(MODULES_SITE) == 0xE9 and self.byte(COUNT_SITE) == 0xE8 and self.byte(STEP_SITE) == 0xE8):
            raise SystemExit(f'{path}: no particle-stepping caves (not a file built by ra3_fps_patch.py)')
        self.step_cave, self.count_cave, self.mod_cave = (self.target(s) for s in (STEP_SITE, COUNT_SITE, MODULES_SITE))
        for cave in (self.step_cave, self.count_cave, self.mod_cave):
            assert CAVE_PAGE <= cave < CAVE_PAGE + 0x1000, f'cave {cave:#x} outside the cave page'
        head = self.read(self.count_cave, 64)
        k = head.find(b'\x51\x52\xb9')          # push ecx; push edx; mov ecx, r
        assert k >= 0, 'count cave: r not found'
        self.r = struct.unpack_from('<I', head, k + 3)[0]
        assert 2 <= self.r <= 8, f'unexpected r = {self.r}'

    def byte(self, va):
        return self.img[va - IB]

    def read(self, va, n):
        return self.img[va - IB:va - IB + n]

    def target(self, site):
        return struct.unpack_from('<i', self.img, site - IB + 1)[0] + site + 5


class Emu:
    def __init__(self, build):
        u = self.u = Uc(UC_ARCH_X86, UC_MODE_32)
        page = lambda va: va & ~0xFFF
        for base, size in ((CAVE_PAGE, 0x1000), (page(FTOL), 0x1000), (page(STEP), 0x2000), (page(DISPLAY_PTR), 0x1000),
                           (page(MODE), 0x1000), (DISP, 0x1000), (SYS, 0x1000), (TMPL, 0x1000), (STORAGE, 0x1000),
                           (STACK_TOP - 0x10000, 0x10000), (SENTINEL, 0x1000)):
            u.mem_map(base, size)
        u.mem_write(CAVE_PAGE, build.read(CAVE_PAGE, 0x1000))
        u.mem_write(DISPLAY_PTR, struct.pack('<I', DISP))
        u.mem_write(STEP, b'\xb0\x5a\xc2\x04\x00')        # stub step: mov al,0x5A; ret 4
        u.mem_write(FTOL, b'\xa1' + struct.pack('<I', FTOL_VALUE) + b'\xba\x34\x12\x00\x00\xc3')  # eax=[value]; edx=0x1234
        u.mem_write(RESUME, b'\xf4')
        u.mem_write(SENTINEL, b'\xf4')
        self.step_calls = []
        u.hook_add(UC_HOOK_CODE, self._on_step, begin=STEP, end=STEP)

    def _on_step(self, u, addr, size, _):
        esp = u.reg_read(UC_X86_REG_ESP)
        self.step_calls.append((u.mem_read(MODE, 1)[0], u.reg_read(UC_X86_REG_ECX),
                                struct.unpack('<I', u.mem_read(esp + 4, 4))[0]))

    def w32(self, va, v):
        self.u.mem_write(va, struct.pack('<I', v & 0xFFFFFFFF))

    def run(self, start, stack_dwords, regs):
        u = self.u
        esp = STACK_TOP - 0x100 - 4 * len(stack_dwords)
        for i, v in enumerate(stack_dwords):
            self.w32(esp + 4 * i, v)
        for reg, v in regs.items():
            u.reg_write(reg, v)
        u.reg_write(UC_X86_REG_ESP, esp)
        u.emu_start(start, 0xFFFFFFFF, count=10000)     # stops at hlt (sentinel / resume)
        return esp


def phase(frame, esi, r):
    return (frame + (((esi * 0x9E3779B1) & 0xFFFFFFFF) >> 16)) % r


def check_count(b):
    e, r = Emu(b), b.r
    n = 0
    for mode in (0, 1, 2):
        for count in (-1, 0, 1, 2, 3, 4, 7, 50):
            for created in (0, 9):
                for frame in range(0, 7):
                    for esi in (SYS, SYS + 0x1A0, SYS + 0x7D0):
                        e.u.mem_write(MODE, bytes([mode]))
                        e.u.mem_write(FTOL_VALUE, struct.pack('<i', count))
                        e.w32(DISP + 0x48, frame)
                        e.w32(esi + 0x110, created)
                        esp = e.run(b.count_cave, [SENTINEL], {UC_X86_REG_ECX: 0x11111111, UC_X86_REG_ESI: esi,
                                                               UC_X86_REG_EDI: 0x22222222, UC_X86_REG_EBX: 0x33333333,
                                                               UC_X86_REG_EBP: 0x44444444})
                        if mode == 0 or count <= 0:
                            want = count
                        else:
                            j = (frame - 1) % r + 1 if created == 0 else 1
                            want = (count * j + phase(frame, esi, r)) // r
                        got = struct.unpack('<i', struct.pack('<I', e.u.reg_read(UC_X86_REG_EAX)))[0]
                        assert got == want, (mode, count, created, frame, hex(esi), got, want)
                        assert e.u.reg_read(UC_X86_REG_ESP) == esp + 4
                        assert e.u.reg_read(UC_X86_REG_ECX) == 0x11111111 and e.u.reg_read(UC_X86_REG_EDX) == 0x1234
                        assert (e.u.reg_read(UC_X86_REG_ESI), e.u.reg_read(UC_X86_REG_EDI), e.u.reg_read(UC_X86_REG_EBX),
                                e.u.reg_read(UC_X86_REG_EBP)) == (esi, 0x22222222, 0x33333333, 0x44444444)
                        n += 1
    print(f'count cave: {n} cases ok')


def check_step(b):
    e, r = Emu(b), b.r
    n = 0
    for vt in (None,) + STORAGE_VTABLES:
        for high in (0.5, 0.0, -2.0, 1.0, 4.0):
            for delay in (0, 2):
                for countdown in (0, 1):
                    for frame in range(6):
                        e.step_calls.clear()
                        e.u.mem_write(MODE, b'\x00')
                        e.w32(SYS + 0x90, STORAGE if vt else 0)
                        e.w32(STORAGE, vt or 0)
                        e.w32(SYS + 8, TMPL)
                        e.u.mem_write(TMPL + 0xA4, struct.pack('<f', high))
                        e.w32(SYS + 0x3C, delay)
                        e.w32(SYS + 0x104, countdown)
                        e.w32(DISP + 0x48, frame)
                        esp = e.run(b.step_cave, [SENTINEL, 0xA5A5A5A5], {UC_X86_REG_ECX: SYS, UC_X86_REG_EAX: 0xDEAD0000,
                                                                         UC_X86_REG_ESI: 0x55, UC_X86_REG_EDI: 0x66,
                                                                         UC_X86_REG_EBX: 0x77, UC_X86_REG_EBP: 0x88})
                        cont = high < 1.0
                        full = frame % r == 0
                        if vt is None:                      # no storage: stepped every drawn frame
                            want = [(0, SYS, 0xA5A5A5A5)]
                        elif not cont:                      # periodic emitter: full steps only
                            want = [(0, SYS, 0xA5A5A5A5)] if full else []
                        elif full:
                            want = [(2, SYS, 0xA5A5A5A5)]
                        elif delay or countdown:
                            want = []
                        else:
                            want = [(1, SYS, 0xA5A5A5A5)]
                        assert e.step_calls == want, (vt, high, delay, countdown, frame, e.step_calls, want)
                        al = e.u.reg_read(UC_X86_REG_EAX) & 0xFF
                        assert al == (0x5A if want else 1), (vt, high, frame, al)
                        assert e.u.mem_read(MODE, 1)[0] == 0
                        assert e.u.reg_read(UC_X86_REG_ESP) == esp + 8          # ret 4 popped the arg
                        assert (e.u.reg_read(UC_X86_REG_ESI), e.u.reg_read(UC_X86_REG_EDI), e.u.reg_read(UC_X86_REG_EBX),
                                e.u.reg_read(UC_X86_REG_EBP)) == (0x55, 0x66, 0x77, 0x88)
                        n += 1
    print(f'step cave: {n} cases ok')


def check_modules(b):
    e = Emu(b)
    for mode in (0, 1, 2):
        for mod24 in (0, 0x1234):
            e.u.mem_write(MODE, bytes([mode]))
            e.w32(SYS + 0x24, mod24)
            frame = [0xED1, 0xEB1, 0xE51] + [0x10C0 + i for i in range(13)] + [SENTINEL, 0xA5A5A5A5]
            esp = e.run(b.mod_cave, frame, {UC_X86_REG_ESI: SYS, UC_X86_REG_EAX: 0, UC_X86_REG_ECX: 0x99})
            pc_esp = e.u.reg_read(UC_X86_REG_ESP)
            if mode == 1:
                assert pc_esp == esp + 4 * len(frame), hex(pc_esp - esp)
                assert e.u.reg_read(UC_X86_REG_EAX) & 0xFF == 1
                assert (e.u.reg_read(UC_X86_REG_EDI), e.u.reg_read(UC_X86_REG_EBX),
                        e.u.reg_read(UC_X86_REG_ESI)) == (0xED1, 0xEB1, 0xE51)
            else:
                assert pc_esp == esp and e.u.reg_read(UC_X86_REG_ECX) == mod24
                zf = (e.u.reg_read(UC_X86_REG_EFLAGS) >> 6) & 1
                assert zf == (mod24 == 0)
    print('modules cave: ok')


def lifecycle(r, L, c, start, h, forever=False, frames=None):
    """Split emission over drawn frames (full step at frame % r == 0 runs the life countdown after emitting)."""
    life, created, total, per = L, 0, 0, []
    for f in range(start, start + (frames or r * (L + 3))):
        n = 0
        if forever or life > 0:
            j = (f - 1) % r + 1 if created == 0 else 1
            n = (c * j + (f + h) % r) // r
        total, created = total + n, created + n
        per.append(n)
        if f % r == 0 and not forever and life > 0:
            life -= 1
    return total, per


def check_lifecycle(r):
    worst = 0
    for L in range(1, 8):
        for c in range(1, 25):
            for start in range(r):
                for h in range(r):
                    total, _ = lifecycle(r, L, c, 30 + start, h)
                    worst = max(worst, abs(total - L * c))
                    assert abs(total - L * c) <= 1, (L, c, start, h, total)
    # forever systems: steady state c per period, every frame gets ~c/r
    for c in (1, 2, 3, 6, 10):
        total, per = lifecycle(r, 0, c, 31, 1, forever=True, frames=r * 40)
        assert abs(total - 40 * c) <= c
    print(f'lifecycle: finite systems emit L*c (max deviation {worst} particle); steady state ok')


def main(argv):
    if not __debug__:
        sys.exit('run without -O (the checks use assert)')
    if len(argv) > 1 or argv[:1] in (['-h'], ['--help']):
        sys.exit(__doc__)
    path = argv[0] if argv else DEFAULT_BUILD
    b = Build(path)
    print(f'{path}: r = {b.r} drawn frames per particle step')
    check_count(b)
    check_step(b)
    check_modules(b)
    check_lifecycle(b.r)
    print('all particle-stepping cave checks passed')


if __name__ == '__main__':
    main(sys.argv[1:])
