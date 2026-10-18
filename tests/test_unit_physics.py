"""Unicorn emulation test for the unit-physics code caves.

Runs the build's locomotion-physics dispatcher 0x5742B0 from its exact patched bytes: the guard cave (jmp at
0x5742F1), the wrappers of the four appearance-function calls, the 0x88-byte allocation cave and the original code in
between. The appearance functions (0x568220, 0x55FED0, 0x567920) are replaced by stubs that count steps and write a
recognizable tilt. It checks that:
  * the physics steps exactly when n = fps/30 or more drawn frames have passed since the locomotor's last step
    (unsigned, so counter wrap and reset are fine), and every frame in between gets the tilt of the last step;
  * a second call in the same frame returns false without touching the tilt, as stock;
  * a second drawable of the same object gets no tilt, as stock, and ignores stale caches or caches written for
    another locomotor;
  * appearances 5 and 6 (no physics) are never tilted;
  * all four state allocations (three lazy ones and the save-game load in Drawable::xfer) request 0x88 bytes and keep
    the stack balanced.
n is read from the guard cave (3 at 90 FPS, 2 at 60 FPS).

usage: python tests/test_unit_physics.py [BUILD]    (default: build/ra3_1.12_90fps.game)
Needs unicorn (requirements.txt).
"""
import os
import struct
import sys

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, Uc
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESP

IB = 0x400000
DEFAULT_BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'build',
                             'ra3_1.12_90fps.game')
DISPATCH, GUARD_SITE, CLIENT_PTR, ALLOC = 0x5742B0, 0x5742F1, 0xCE08F0, 0x416970
CAVE_PAGE = 0xBC6000
TARGETS = {0x568220: 0x1111, 0x55FED0: 0x2222, 0x567920: 0x3333}  # appearance function -> tag written by its stub
APPEARANCE_TARGET = {1: 0x568220, 2: 0x55FED0, 3: 0x55FED0, 4: 0x55FED0, 7: 0x568220, 8: 0x567920}
ALLOC_SITES = [0x55FEDC, 0x567932, 0x568231, 0x57C728]  # push 0x70; call; add esp,4 (last: save-game load)
STATE_SIZE, CACHE = 0x88, 0x70
HEAP, STUBS, STACK_TOP, SENTINEL = 0x20000000, 0x21000000, 0x2F000000, 0x30000000
CLIENT, VT, FRAME_VA, STEPS_VA, ALLOC_SIZE_VA, INFO = (STUBS + 0x100, STUBS + 0x200, STUBS + 0x300, STUBS + 0x304,
                                                        STUBS + 0x308, STUBS + 0x400)
FRAME_STUB, ALLOC_STUB = STUBS + 0x500, STUBS + 0x600


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def read_n(img):
    off = GUARD_SITE - IB
    check(img[off] == 0xE9, f'{GUARD_SITE:#x} is not patched (not a file built by ra3_fps_patch.py)')
    cave = struct.unpack_from('<i', img, off + 1)[0] + GUARD_SITE + 5
    check(CAVE_PAGE <= cave < CAVE_PAGE + 0x1000, f'guard cave {cave:#x} outside the cave page')
    body = img[cave - IB:cave - IB + 16]
    check(body[:8] == bytes.fromhex('8bc52b86bc000000') and body[8] == 0x74 and body[10:12] == b'\x83\xf8',
          'unexpected guard cave')
    return body[12]


class Emu:
    def __init__(self, img, stubs=True):
        u = self.u = Uc(UC_ARCH_X86, UC_MODE_32)
        u.mem_map(IB, 0xD40000 - IB)
        u.mem_write(IB, img[:0x8D6000])  # headers, .text, .rdata, file-backed .data (file offset = VA - IB)
        for base, size in ((HEAP, 0x100000), (STUBS, 0x1000), (STACK_TOP - 0x10000, 0x10000), (SENTINEL, 0x1000)):
            u.mem_map(base, size)
        u.mem_write(SENTINEL, b'\xf4')
        self.w32(CLIENT_PTR, CLIENT)
        self.w32(CLIENT, VT)
        self.w32(VT + 0x74, FRAME_STUB)                                            # client vfunc +0x74: frame
        u.mem_write(FRAME_STUB, b'\xa1' + struct.pack('<I', FRAME_VA) + b'\xc3')   # mov eax,[frame]; ret
        u.mem_write(ALLOC_STUB, b'\x8b\x44\x24\x04\xa3' + struct.pack('<I', ALLOC_SIZE_VA)
                    + b'\xb8' + struct.pack('<I', HEAP + 0xF0000) + b'\xc3')       # record size; eax = buffer; ret
        self.heap = HEAP + 0x1000
        self.calls = []
        if stubs:
            for t, tag in TARGETS.items():
                # inc [steps]; eax = [steps]; edx = info; info = {steps, steps, steps, tag}; ret 8
                u.mem_write(t, b'\xff\x05' + struct.pack('<I', STEPS_VA) + b'\xa1' + struct.pack('<I', STEPS_VA)
                            + b'\x8b\x54\x24\x08\x89\x02\x89\x42\x04\x89\x42\x08\xc7\x42\x0c'
                            + struct.pack('<I', tag) + b'\xc2\x08\x00')
                u.hook_add(UC_HOOK_CODE, self._on_target, begin=t, end=t)

    def _on_target(self, u, addr, size, _):
        ecx, esp = u.reg_read(UC_X86_REG_ECX), u.reg_read(UC_X86_REG_ESP)
        if self.r32(ecx + 0x208) == 0:  # lazy state allocation, as the real functions do
            self.w32(ecx + 0x208, self.alloc(STATE_SIZE))
        self.calls.append((addr, ecx, self.r32(esp + 4), self.r32(esp + 8)))

    def alloc(self, size):
        p = self.heap
        self.heap += (size + 15) & ~15
        self.u.mem_write(p, b'\xcd' * size)  # fresh heap memory is not zeroed
        return p

    def w32(self, va, v):
        self.u.mem_write(va, struct.pack('<I', v & 0xFFFFFFFF))

    def r32(self, va):
        return struct.unpack('<I', self.u.mem_read(va, 4))[0]

    def unit(self, appearance):
        """drawable -> object -> AI -> locomotor -> template chain; returns (drawable, locomotor)."""
        drw, obj, ai, loco, t1, t2 = (self.alloc(0x400) for _ in range(6))
        self.u.mem_write(drw, bytes(0x400))
        self.w32(drw + 0x138, obj)
        self.w32(obj + 0x374, ai)
        self.w32(ai + 0x21C, loco)
        self.w32(loco + 4, t1)
        self.w32(loco + 0xBC, 0)
        self.w32(t1 + 4, t2)
        self.w32(t2 + 0x64, appearance)
        return drw, loco

    def dispatch(self, drw, frame):
        """Calls the dispatcher for `drw` on drawn frame `frame`; returns (al, info)."""
        u = self.u
        self.w32(FRAME_VA, frame)
        u.mem_write(INFO, bytes(16))
        esp = STACK_TOP - 0x100
        self.w32(esp, SENTINEL)
        self.w32(esp + 4, INFO)
        u.reg_write(UC_X86_REG_ESP, esp)
        u.reg_write(UC_X86_REG_ECX, drw)
        u.emu_start(DISPATCH, SENTINEL, count=100000)
        check(u.reg_read(UC_X86_REG_ESP) == esp + 8, 'dispatcher: stack imbalance')
        return u.reg_read(UC_X86_REG_EAX) & 0xFF, struct.unpack('<4I', u.mem_read(INFO, 16))


def test_alloc_sites(img):
    e = Emu(img, stubs=False)  # the sites are inside the appearance functions the other tests replace
    u = e.u
    u.mem_write(ALLOC, b'\xe9' + struct.pack('<i', ALLOC_STUB - (ALLOC + 5)))
    for site in ALLOC_SITES:
        end = site + 10
        u.mem_write(end, b'\xf4')
        esp = STACK_TOP - 0x100
        u.reg_write(UC_X86_REG_ESP, esp)
        e.w32(ALLOC_SIZE_VA, 0)
        u.emu_start(site, end, count=1000)
        check(u.reg_read(UC_X86_REG_ESP) == esp, f'allocation at {site:#x}: stack imbalance')
        check(e.r32(ALLOC_SIZE_VA) == STATE_SIZE, f'allocation at {site:#x}: size {e.r32(ALLOC_SIZE_VA):#x}')
        check(u.reg_read(UC_X86_REG_EAX) == HEAP + 0xF0000, f'allocation at {site:#x}: result')
        u.mem_write(end, img[end - IB:end - IB + 1])
    print(f'state allocations: {len(ALLOC_SITES)} sites request {STATE_SIZE:#x} bytes, stack balanced')


def test_stepping(img, n, appearance):
    e = Emu(img)
    drw, loco = e.unit(appearance)
    target, tag = APPEARANCE_TARGET[appearance], TARGETS[APPEARANCE_TARGET[appearance]]
    last_step, steps_before = None, 0
    for f in range(100, 131):
        al, info = e.dispatch(drw, f)
        stepped = len(e.calls) > steps_before
        steps_before = len(e.calls)
        if last_step is None or f - last_step >= n:
            check(stepped, f'appearance {appearance}, frame {f}: expected a physics step')
            check(e.calls[-1] == (target, drw, loco, INFO), f'appearance {appearance}, frame {f}: step arguments')
            check(e.r32(loco + 0xBC) == f, 'locomotor step frame')
            st = e.r32(drw + 0x208)
            check(e.r32(st + CACHE) == f and e.r32(st + CACHE + 4) == loco, 'cached frame/locomotor')
            last_step = f
        else:
            check(not stepped, f'appearance {appearance}, frame {f}: unexpected step')
        check(al == 1, f'appearance {appearance}, frame {f}: no tilt')
        steps = len(e.calls)
        check(info == (steps, steps, steps, tag), f'appearance {appearance}, frame {f}: tilt {info}')
    check(len(e.calls) == len(range(100, 131, n)), f'{len(e.calls)} steps')
    al, info = e.dispatch(drw, 130)  # second call in the same frame
    check(al == 0 and info == (0, 0, 0, 0), 'same-frame call should return false')
    print(f'appearance {appearance}: {len(e.calls)} steps in 31 frames, cached tilt in between, '
          f'same-frame call -> no tilt')


def test_two_drawables(img):
    e = Emu(img)
    a, loco = e.unit(1)
    b, _ = e.unit(1)
    e.w32(b + 0x138, e.r32(a + 0x138))  # same object, so the same locomotor
    for f in range(10, 30):
        al_a, _ = e.dispatch(a, f)
        al_b, info_b = e.dispatch(b, f)
        check(al_a == 1, f'frame {f}: first drawable should be tilted')
        check(al_b == 0 and info_b == (0, 0, 0, 0), f'frame {f}: second drawable should get no tilt')
    check(all(c[1] == a for c in e.calls), 'only the first drawable steps')
    check(e.r32(b + 0x208) == 0, 'the second drawable never allocates')
    st = e.alloc(STATE_SIZE)  # stale cache from an older step
    e.w32(b + 0x208, st)
    e.w32(st + CACHE, 5)
    for f in range(30, 40):
        e.dispatch(a, f)
        al_b, info_b = e.dispatch(b, f)
        check(al_b == 0 and info_b == (0, 0, 0, 0), f'frame {f}: stale cache used')
    other = e.alloc(0x400)  # cache with the current step frame, but written for another locomotor
    for f in range(40, 50):
        e.dispatch(a, f)
        e.w32(st + CACHE, e.r32(loco + 0xBC))
        e.w32(st + CACHE + 4, other)
        al_b, info_b = e.dispatch(b, f)
        check(al_b == 0 and info_b == (0, 0, 0, 0), f'frame {f}: cache of another locomotor used')
    print('two drawables of one object: the first steps, the second gets no tilt (as stock) and ignores '
          'stale or foreign caches')


def test_no_physics(img):
    for appearance in (5, 6):
        e = Emu(img)
        drw, _ = e.unit(appearance)
        st = e.alloc(STATE_SIZE)
        e.w32(drw + 0x208, st)
        e.w32(st + CACHE, 0)
        for f in range(1, 20):
            al, info = e.dispatch(drw, f)
            check(al == 0 and info == (0, 0, 0, 0), f'appearance {appearance}, frame {f}: tilted')
        check(not e.calls, f'appearance {appearance}: physics called')
    print('appearances 5 and 6: never tilted, no physics calls')


def test_gaps(img, n):
    e = Emu(img)
    drw, _ = e.unit(8)
    last = 0
    for f in (200, 205, 206, 207, 209, 210, 211, 212, 1000, 1001, 0x7FFFFFFF, 0x80000000, 0x80000003, 50):
        expect = (f - last) & 0xFFFFFFFF >= n
        before = len(e.calls)
        al, info = e.dispatch(drw, f)
        check((len(e.calls) > before) == expect, f'frame {f}: step expected {expect}')
        check(al == 1 and info[3] == TARGETS[0x567920], f'frame {f}: no tilt')
        if expect:
            last = f
    print('frame gaps, counter wrap and reset: steps exactly when n or more frames have passed')


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BUILD
    with open(path, 'rb') as f:
        img = f.read()
    n = read_n(img)
    print(f'{path}: n = {n} drawn frames per physics step')
    test_alloc_sites(img)
    for appearance in sorted(APPEARANCE_TARGET):
        test_stepping(img, n, appearance)
    test_two_drawables(img)
    test_no_physics(img)
    test_gaps(img, n)
    print('ALL OK')


if __name__ == '__main__':
    main()
