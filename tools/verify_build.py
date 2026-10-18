"""Disassemble every changed region of a patched build next to the original (sanity check for patches and caves).

Changed ranges in .text are shown as original vs patched instructions; section-header changes are decoded. The cave
area (zero-filled .text tail) is listed per entry point: every rel32 call/jmp target and absolute memory operand that
lands in the cave area, found in the patched sites and (transitively) in the caves themselves, starts a block. Code blocks
are disassembled up to the next block or trailing zero padding; data blocks are shown as dwords and floats. Non-zero
cave bytes that nothing references are reported as UNREFERENCED, and the exit status is then 1.

usage: python tools/verify_build.py BUILD [--game PATH] [--cache build/text_insns.pkl]
"""
import argparse
import bisect
import collections
import re
import struct
import sys

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

from common import CAVE_END, CAVE_START, DEFAULT_CACHE, DEFAULT_GAME, IB, TEXT_END, find_original, load_cache

md = Cs(CS_ARCH_X86, CS_MODE_32)
CAVE_VA = range(CAVE_START + IB, CAVE_END + IB)
MEM = re.compile(r'\[(0x[0-9a-f]+)\]')
SECTION_FIELDS = {8: 'VirtualSize', 12: 'VirtualAddress', 16: 'SizeOfRawData', 20: 'PointerToRawData',
                  36: 'Characteristics'}


def dis(buf, va, n=None):
    out = []
    for i in md.disasm(buf, va):
        out.append(i)
        if n and len(out) >= n:
            break
    return out


def fmt(i):
    return f'    {i.address:#x}: {i.bytes.hex():<22} {i.mnemonic} {i.op_str}'


def collect_refs(insns, code, data):
    """Add cave-area rel32 branch targets and absolute memory operands of `insns` to code/data (target -> sources).
    Short (rel8) jumps are labels inside a cave and do not start a block."""
    for i in insns:
        if (i.mnemonic == 'call' or i.mnemonic.startswith('j')) and i.size >= 5 and i.op_str.startswith('0x'):
            t = int(i.op_str, 16)
            if t in CAVE_VA:
                code[t].add(i.address)
        for m in MEM.finditer(i.op_str):
            t = int(m.group(1), 16)
            if t in CAVE_VA:
                data[t].add(i.address)


def section_fields(o, d, lo, hi):
    """Describe changed section-header fields in o[lo..hi] -> d, or None if the range is not in the section table."""
    pe = struct.unpack_from('<I', o, 0x3C)[0]
    table = pe + 24 + struct.unpack_from('<H', o, pe + 20)[0]
    n = struct.unpack_from('<H', o, pe + 6)[0]
    if not table <= lo and hi < table + 40 * n:
        return None
    out = []
    for f in range(lo & ~3, hi + 1, 4):
        base = table + (f - table) // 40 * 40
        field = SECTION_FIELDS.get(f - base)
        if field is None:
            return None
        name = o[base:base + 8].rstrip(b'\0').decode(errors='replace')
        out.append(f'{name} {field} {struct.unpack_from("<I", o, f)[0]:#x} -> {struct.unpack_from("<I", d, f)[0]:#x}')
    return '; '.join(out)


def cave_blocks(d, code, data):
    """Split the cave area at all known entries: [(va, 'code', insns) | (va, 'data', bytes)]."""
    bounds = sorted(set(code) | set(data) | {CAVE_END + IB})
    blocks = []
    for a in sorted(set(code) | set(data)):
        nxt = bounds[bisect.bisect_right(bounds, a)]
        region = d[a - IB:nxt - IB]
        used = len(region.rstrip(b'\0'))
        if a in code:
            insns = [i for i in dis(region, a) if i.address < a + max(used, 1)]
            blocks.append((a, 'code', insns))
        else:
            blocks.append((a, 'data', region[:max(4, (used + 3) & ~3)]))
    return blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('build', help='patched ra3_1.12.game to inspect')
    ap.add_argument('--game', default=DEFAULT_GAME,
                    help='ra3_1.12.game whose .bak (or itself, if unmodified) is the original (default: %(default)s)')
    ap.add_argument('--cache', default=DEFAULT_CACHE, help='instruction cache (default: %(default)s)')
    args = ap.parse_args()
    _, o = find_original(args.game)
    with open(args.build, 'rb') as f:
        d = f.read()
    if len(o) != len(d):
        raise SystemExit(f'error: {args.build} is {len(d)} bytes, the original is {len(o)}')
    ins, addrs = load_cache(args.cache)

    diffs = [i for i in range(len(o)) if o[i] != d[i]]
    ranges = []
    for i in diffs:
        if ranges and i <= ranges[-1][1] + 8:
            ranges[-1][1] = i
        else:
            ranges.append([i, i])
    print(f'{args.build}: {len(diffs)} bytes in {len(ranges)} ranges')
    code, data = collections.defaultdict(set), collections.defaultdict(set)
    for lo, hi in ranges:
        if CAVE_START <= lo < CAVE_END:
            continue
        if lo < 0x1000 or lo >= TEXT_END:
            desc = section_fields(o, d, lo, hi) if lo < 0x1000 else None
            print(f'  header/data {lo:#x}..{hi:#x}: ' + (desc or f'{o[lo:hi + 1].hex()} -> {d[lo:hi + 1].hex()}'))
            continue
        va = lo + IB
        k = bisect.bisect_right(addrs, va) - 1
        start = addrs[k]
        end = hi + IB + 1
        n_orig = 0
        while k + n_orig < len(addrs) and addrs[k + n_orig] < end:
            n_orig += 1
        size = end - start + 32
        new = dis(d[start - IB:start - IB + size], start, n_orig + 1)
        collect_refs(new, code, data)
        print(f'  --- {start:#x} (file {start - IB:#x})')
        print('   orig:')
        print('\n'.join(map(fmt, dis(o[start - IB:start - IB + size], start, n_orig + 1))))
        print('   new:')
        print('\n'.join(map(fmt, new)))

    while True:   # follow references between caves until no new entry points appear
        known = (len(code), len(data))
        for a, kind, body in cave_blocks(d, code, data):
            if kind == 'code':
                collect_refs(body, code, data)
        if (len(code), len(data)) == known:
            break
    print('  === caves')
    covered = bytearray(CAVE_END - CAVE_START)
    for a, kind, body in cave_blocks(d, code, data):
        src = ', '.join(f'{s:#x}' for s in sorted((code if kind == 'code' else data)[a]))
        if kind == 'code':
            n = body[-1].address + body[-1].size - a if body else 0
            print(f'  cave {a:#x} code, {n} bytes (from {src})')
            print('\n'.join(map(fmt, body)))
        else:
            n = len(body)
            vals = struct.unpack(f'<{n // 4}I', body)
            print(f'  cave {a:#x} data (from {src}): ' + ', '.join(
                f'{v:#010x} = {struct.unpack("<f", struct.pack("<I", v))[0]:g}' for v in vals))
        covered[a - IB - CAVE_START:a - IB - CAVE_START + n] = b'\1' * n
    stray = [CAVE_START + i for i in range(len(covered)) if d[CAVE_START + i] and not covered[i]]
    if any(d[TEXT_END:CAVE_START]):
        stray += [i for i in range(TEXT_END, CAVE_START) if d[i]]
    if stray:
        print(f'  UNREFERENCED non-zero cave bytes at file {", ".join(hex(x) for x in stray[:16])}'
              + (' ...' if len(stray) > 16 else ''))
        sys.exit(1)
    print(f'  all {sum(1 for x in d[CAVE_START:CAVE_END] if x)} non-zero cave bytes belong to referenced blocks')


if __name__ == '__main__':
    main()
