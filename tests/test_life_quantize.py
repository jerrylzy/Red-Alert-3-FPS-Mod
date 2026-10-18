"""Unicorn emulation test for the GPU particle lifetime caves.

Runs both patched conversions of the GPU particle lifetime L ([rec+0x28], in 30 Hz steps) from the build's exact
bytes: the record constructor (call at 0x6FFDB0, death time) and the vertex writer (call at 0x6F80C5, birth and
lifetime for the shader). For each it checks that:
  * L < 2^24 gives st0 = n*s - u/2, with n = floor(100 L / 99) + 1 and s, u/2 the cave's constants;
  * larger or negative L gives float(uint32 L), as in the original code;
  * exactly one x87 register is pushed, and ecx/edx/esi/edi/ebx/ebp and the stack are preserved;
  * a particle drawn while age <= L' is visible on exactly r times the stock 30 FPS frame count, for L = 0..1000
    (r = s/u: 3 at 90 FPS, 2 at 60 FPS; age advances (1000 // fps) * 0.03 sync units per drawn frame).
It runs in x87 extended and single precision; Direct3D 9 may switch the FPU to single precision.

usage: python tests/test_life_quantize.py [BUILD]    (default: build/ra3_1.12_90fps.game)
Needs unicorn (requirements.txt).
"""
import os
import struct
import sys

from unicorn import UC_ARCH_X86, UC_MODE_32, Uc
from unicorn.x86_const import (UC_X86_REG_EBP, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDI, UC_X86_REG_EDX,
                               UC_X86_REG_ESI, UC_X86_REG_ESP, UC_X86_REG_FPCW, UC_X86_REG_FPSW)

IB = 0x400000
DEFAULT_BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'build',
                             'ra3_1.12_90fps.game')
# site -> (name, register holding the record, bytes expected after the call)
SITES = {0x6FFDB0: ('record ctor', UC_X86_REG_ESI, bytes.fromhex('0f1f840000000000')),
         0x6F80C5: ('vertex writer', UC_X86_REG_EDX, bytes.fromhex('560f1f8400000000000f1f00'))}
CAVE_PAGE, RDATA_PAGE, UINT_FIX = 0xBC6000, 0xBE8000, 0xBE8CB4
REC, HARNESS, OUT, STACK_TOP = 0x20000000, 0x30000000, 0x30000800, 0x2F000000
OTHER_REGS = {UC_X86_REG_ECX: 0x11111111, UC_X86_REG_EDX: 0x22222222, UC_X86_REG_ESI: 0x33333333,
              UC_X86_REG_EDI: 0x44444444, UC_X86_REG_EBX: 0x55555555, UC_X86_REG_EBP: 0x66666666}
CW_EXTENDED, CW_SINGLE = 0x037F, 0x007F    # all exceptions masked; precision control 64-bit / 24-bit mantissa


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def check_site(img, site, fps):
    name, reg, tail = SITES[site]
    off = site - IB
    assert img[off] == 0xE8 and img[off + 5:off + 5 + len(tail)] == tail, f'{name}: site {site:#x} is not patched'
    cave = struct.unpack_from('<i', img, off + 1)[0] + site + 5
    assert CAVE_PAGE <= cave < CAVE_PAGE + 0x1000, f'{name}: cave {cave:#x} outside the cave page'
    body = img[cave - IB:cave - IB + 64]
    k = body.find(b'\xd8\x0d')                 # fmul dword [s]; fsub dword [u/2] follows
    assert k >= 0 and body[k + 6:k + 8] == b'\xd8\x25', f'{name}: constants not found'
    s_va, h_va = struct.unpack_from('<I', body, k + 2)[0], struct.unpack_from('<I', body, k + 8)[0]
    s, h = (struct.unpack_from('<f', img, va - IB)[0] for va in (s_va, h_va))
    u = (1000 // fps) * 0.03
    r = round(s / (2 * h))
    assert abs(2 * h - u) < 1e-6 and abs(s - r * u) < 1e-5, f'{name}: unexpected constants s={s} u/2={h} for {fps} FPS'

    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    for base in (CAVE_PAGE, RDATA_PAGE, REC, HARNESS, STACK_TOP - 0x10000):
        uc.mem_map(base, 0x10000 if base == STACK_TOP - 0x10000 else 0x1000)
    uc.mem_write(CAVE_PAGE, img[CAVE_PAGE - IB:CAVE_PAGE - IB + 0x1000])
    uc.mem_write(RDATA_PAGE, img[RDATA_PAGE - IB:RDATA_PAGE - IB + 0x1000])
    # call cave; fstp qword [OUT]; hlt
    uc.mem_write(HARNESS, b'\xe8' + struct.pack('<i', cave - (HARNESS + 5)) + b'\xdd\x1d' + struct.pack('<I', OUT) + b'\xf4')

    def run(L, cw):
        uc.mem_write(REC + 0x28, struct.pack('<I', L & 0xFFFFFFFF))
        regs = {**OTHER_REGS, reg: REC}
        for rg, v in regs.items():
            uc.reg_write(rg, v)
        uc.reg_write(UC_X86_REG_ESP, STACK_TOP - 0x100)
        uc.reg_write(UC_X86_REG_FPCW, cw)
        uc.reg_write(UC_X86_REG_FPSW, 0)
        uc.emu_start(HARNESS, 0xFFFFFFFF, count=200)
        assert uc.reg_read(UC_X86_REG_ESP) == STACK_TOP - 0x100, f'{name}: stack not balanced'
        assert all(uc.reg_read(rg) == v for rg, v in regs.items()), f'{name}: registers not preserved'
        sw = uc.reg_read(UC_X86_REG_FPSW)
        assert (sw >> 11) & 7 == 0 and not sw & 0x41, f'{name}: x87 stack not balanced (FPSW {sw:#x})'
        return struct.unpack('<d', uc.mem_read(OUT, 8))[0]

    for cw in (CW_EXTENDED, CW_SINGLE):
        rnd = f32 if cw == CW_SINGLE else (lambda x: x)
        for L in list(range(0, 1001)) + [5000, 99999, (1 << 24) - 1]:
            n = 100 * L // 99 + 1
            got = run(L, cw)
            want = rnd(rnd(n * s) - h)
            assert abs(got - want) <= 1e-9 * max(1.0, abs(want)), (name, hex(cw), L, got, want)
            if L <= 1000:
                visible = int(got / u + 1e-9) + 1      # drawn frames k = 0.. with k * u <= L'
                assert visible == r * n, (name, hex(cw), L, got, visible, r * n)
        for L in (1 << 24, (1 << 31) - 1, -1, -(1 << 31), -12345):
            got = run(L, cw)
            want = float(L) if L >= 0 else rnd(L + 2.0 ** 32)
            assert got == want, (name, hex(cw), L, got, want)
    print(f'{name} cave {cave:#x}: s={s:.6g} u/2={h:.6g} r={r}; formula, visibility (L=0..1000), '
          f'fallback and register checks ok in extended and single precision')


def main(argv):
    if not __debug__:
        sys.exit('run without -O (the checks use assert)')
    if len(argv) > 1 or argv[:1] in (['-h'], ['--help']):
        sys.exit(__doc__)
    path = argv[0] if argv else DEFAULT_BUILD
    with open(path, 'rb') as f:
        img = f.read()
    if img[0x6FFDB0 - IB] != 0xE8:
        raise SystemExit(f'{path}: no particle-lifetime caves (not a file built by ra3_fps_patch.py)')
    pacing = struct.unpack_from('<I', img, 0x2686F9)[0]
    fps = struct.unpack_from('<I', img, pacing - IB)[0]   # the pacing operand points at the target fps constant
    print(f'{path}: {fps} FPS')
    for site in SITES:
        check_site(img, site, fps)
    print('all GPU lifetime cave checks passed')


if __name__ == '__main__':
    main(sys.argv[1:])
