"""Disassembly helper over the cached .text listing (build/text_insns.pkl, made by build_insn_cache.py).

usage: python tools/rdis.py ADDR [END|+COUNT]     dump a range (default 60 instructions)
       python tools/rdis.py callers ADDR          call/jmp sites targeting ADDR
       python tools/rdis.py refs TEXT [LO HI]     instructions whose operands contain TEXT (optionally within LO..HI)
       python tools/rdis.py dword VA [N]          N dwords at VA, read from the original file
options: --cache PKL (default build/text_insns.pkl), --game PATH (for dword; default the EA App install)
Addresses are hex (0x prefix optional); counts are decimal (or 0x-prefixed hex).
"""
import argparse
import bisect
import struct

from common import DEFAULT_CACHE, DEFAULT_GAME, IB, find_original, load_cache


def addr(s):
    return int(s, 16)


def count(s):
    return int(s, 0)


def dump(ins, addrs, a, end=None, n_max=60):
    i = bisect.bisect_left(addrs, a)
    n = 0
    while i < len(ins):
        x = ins[i]
        if end is not None and x[0] >= end:
            break
        if end is None and n >= n_max:
            break
        print(f'{x[0]:08x}: {x[1]} {x[2]}')
        i += 1
        n += 1


def main():
    ap = argparse.ArgumentParser(
        usage='python tools/rdis.py [--cache PKL] [--game PATH] {ADDR [END|+COUNT] | callers ADDR | refs TEXT [LO HI] '
              '| dword VA [N]}', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cache', default=DEFAULT_CACHE)
    ap.add_argument('--game', default=DEFAULT_GAME)
    ap.add_argument('cmd', nargs='+', help=argparse.SUPPRESS)
    args = ap.parse_args()
    argv = args.cmd
    if argv[0] == 'dword':
        if len(argv) < 2:
            ap.error('dword needs VA')
        _, d = find_original(args.game)
        va = addr(argv[1])
        n = count(argv[2]) if len(argv) > 2 else 8
        if not (IB <= va and va - IB + 4 * n <= len(d)):
            ap.error('address outside the file image')
        print(' '.join(f'{struct.unpack_from("<I", d, va - IB + 4 * k)[0]:#x}' for k in range(n)))
        return
    ins, addrs = load_cache(args.cache)
    if argv[0] == 'callers':
        if len(argv) < 2:
            ap.error('callers needs ADDR')
        t = hex(addr(argv[1]))
        for a, m, o, s in ins:
            if m in ('call', 'jmp') and o == t:
                print(f'{a:08x}: {m} {o}')
    elif argv[0] == 'refs':
        if len(argv) < 2:
            ap.error('refs needs TEXT')
        text = argv[1].lower()
        lo = addr(argv[2]) if len(argv) > 2 else 0
        hi = addr(argv[3]) if len(argv) > 3 else 1 << 32
        for a, m, o, s in ins:
            if lo <= a < hi and text in o:
                print(f'{a:08x}: {m} {o}')
    else:
        a = addr(argv[0])
        if len(argv) > 1 and argv[1].startswith('+'):
            dump(ins, addrs, a, n_max=count(argv[1][1:]))
        elif len(argv) > 1:
            dump(ins, addrs, a, end=addr(argv[1]))
        else:
            dump(ins, addrs, a)


if __name__ == '__main__':
    main()
