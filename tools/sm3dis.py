"""Disassembler for Direct3D 9 shader bytecode (vs/ps 1.x-3.0, e.g. inside .fxo effect files), with constant names
from the embedded CTAB.

usage: python tools/sm3dis.py FILE OFFSET     disassemble the shader whose version token is at OFFSET
       python tools/sm3dis.py FILE --scan     list offsets of shader version tokens followed by a comment block
"""
import argparse
import struct

OPS = {0:'nop',1:'mov',2:'add',3:'sub',4:'mad',5:'mul',6:'rcp',7:'rsq',8:'dp3',9:'dp4',10:'min',11:'max',
       12:'slt',13:'sge',14:'exp',15:'log',16:'lit',17:'dst',18:'lrp',19:'frc',20:'m4x4',21:'m4x3',22:'m3x4',
       23:'m3x3',24:'m3x2',25:'call',26:'callnz',27:'loop',28:'ret',29:'endloop',30:'label',31:'dcl',32:'pow',
       33:'crs',34:'sgn',35:'abs',36:'nrm',37:'sincos',38:'rep',39:'endrep',40:'if',41:'ifc',42:'else',43:'endif',
       44:'break',45:'breakc',46:'mova',47:'defb',48:'defi',64:'texcoord',65:'texkill',66:'tex',80:'cnd',81:'def',
       82:'texreg2rgb',88:'cmp',89:'dp2add',90:'dsx',91:'dsy',92:'texldd',93:'setp',94:'texldl',95:'breakp'}
RT = {0:'r',1:'v',2:'c',3:'a',4:'oRast',5:'oAttr',6:'o',7:'i',8:'oC',9:'oDepth',10:'s',11:'c2k',12:'c4k',13:'c6k',
      14:'b',15:'aL',16:'half',17:'misc',18:'l',19:'p'}
SRCMOD = {0:'{}',1:'-{}',2:'{}_bias',3:'-{}_bias',4:'{}_bx2',5:'-{}_bx2',6:'1-{}',7:'{}_x2',8:'-{}_x2',9:'{}_dz',
          10:'{}_dw',11:'abs({})',12:'-abs({})',13:'!{}'}


def regtype(t):
    return ((t >> 28) & 7) | ((t >> 8) & 0x18)


def parse_ctab(words):
    raw = struct.pack('<%dI' % len(words), *words)
    if raw[:4] != b'CTAB':
        return {}
    d = raw[4:]
    size, creator, ver, n, cinfo, flags, target = struct.unpack_from('<7I', d, 0)
    out = {}
    for k in range(n):
        name_off, rset, ridx, rcnt, _res, tinfo, dflt = struct.unpack_from('<IHHHHII', d, cinfo + 20 * k)
        name = d[name_off:d.index(b'\0', name_off)].decode()
        prefix = {0: 'b', 1: 'i', 2: 'c', 3: 's'}.get(rset, '?')
        for j in range(rcnt):
            out[(prefix, ridx + j)] = name + (f'[{j}]' if rcnt > 1 else '')
    return out


def disasm(blob):
    words = struct.unpack('<%dI' % (len(blob) // 4), blob[:len(blob) // 4 * 4])
    ver = words[0]
    kind = 'vs' if (ver >> 16) == 0xFFFE else 'ps'
    lines = [f'{kind}_{(ver >> 8) & 0xff}_{ver & 0xff}']
    names = {}
    i = 1
    defs = {}
    while i < len(words):
        t = words[i]
        if t == 0x0000FFFF:
            break
        op = t & 0xFFFF
        if op == 0xFFFE:
            n = (t >> 16) & 0x7FFF
            names.update(parse_ctab(list(words[i + 1:i + 1 + n])))
            i += 1 + n
            continue
        n = (t >> 24) & 0xF
        toks = words[i + 1:i + 1 + n]
        name = OPS.get(op, f'op{op}')
        ctrl = (t >> 16) & 0xFF
        if op == 81:  # def
            reg = toks[0] & 0x7FF
            vals = struct.unpack('<4f', struct.pack('<4I', *toks[1:5]))
            defs[reg] = vals
            lines.append(f'def c{reg}, {vals[0]:g}, {vals[1]:g}, {vals[2]:g}, {vals[3]:g}')
        elif op == 48:
            lines.append(f'defi i{toks[0] & 0x7FF}, {", ".join(str(struct.unpack("<i", struct.pack("<I", x))[0]) for x in toks[1:5])}')
        elif op == 47:
            lines.append(f'defb b{toks[0] & 0x7FF}, {toks[1]}')
        elif op == 31:  # dcl
            usage = toks[0] & 0x1F
            uidx = (toks[0] >> 16) & 0xF
            lines.append(f'dcl_usage{usage}_{uidx} {fmt_dst(toks[1], names)}')
        else:
            ops = []
            k = 0
            first = True
            while k < len(toks):
                tok = toks[k]
                rel = (tok >> 13) & 1
                if first and op not in (25, 26, 27, 38, 40, 41, 45, 95, 30) and name not in ('if', 'rep', 'loop'):
                    ops.append(fmt_dst(tok, names))
                    first = False
                    k += 1
                    if rel:
                        k += 1
                    continue
                s = fmt_src(tok, names)
                if rel and k + 1 < len(toks):
                    s = s.replace(']', f'+{fmt_src(toks[k + 1], names)}]') if '[' in s else s + f'[{fmt_src(toks[k + 1], names)}]'
                    k += 1
                ops.append(s)
                first = False
                k += 1
            cmpop = {1: '_gt', 2: '_eq', 3: '_ge', 4: '_lt', 5: '_ne', 6: '_le'}.get(ctrl, '') if op in (41, 45, 93) else ''
            lines.append(f'{name}{cmpop} ' + ', '.join(ops))
        i += 1 + n
    return lines, names, defs


def fmt_reg(tok, names):
    rt = regtype(tok)
    num = tok & 0x7FF
    base = f'{RT.get(rt, "?")}{num}'
    key = {2: 'c', 7: 'i', 14: 'b', 10: 's'}.get(rt)
    if key and (key, num) in names:
        base += f'<{names[(key, num)]}>'
    return base


def fmt_dst(tok, names):
    mask = (tok >> 16) & 0xF
    m = '' if mask == 0xF else '.' + ''.join(c for b, c in zip(range(4), 'xyzw') if mask & (1 << b))
    mod = (tok >> 20) & 0xF
    sat = '_sat' if mod & 1 else ''
    return fmt_reg(tok, names) + m + sat


def fmt_src(tok, names):
    sw = (tok >> 16) & 0xFF
    comps = ''.join('xyzw'[(sw >> (2 * b)) & 3] for b in range(4))
    if comps == 'xyzw':
        comps = ''
    elif len(set(comps)) == 1:
        comps = comps[0]
    s = fmt_reg(tok, names) + ('.' + comps if comps else '')
    mod = (tok >> 24) & 0xF
    return SRCMOD.get(mod, '{}?').format(s)


def scan(data):
    """Offsets of plausible shader starts: a vs/ps version token followed by a comment (CTAB) token."""
    out = []
    for off in range(0, len(data) - 8, 4):
        ver, nxt = struct.unpack_from('<II', data, off)
        if ver >> 16 in (0xFFFE, 0xFFFF) and 1 <= (ver >> 8) & 0xFF <= 3 and ver & 0xFF <= 4 and nxt & 0xFFFF == 0xFFFE:
            out.append((off, f'{"vs" if ver >> 16 == 0xFFFE else "ps"}_{(ver >> 8) & 0xFF}_{ver & 0xFF}'))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 epilog=__doc__.split('\n\n')[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('file')
    ap.add_argument('offset', nargs='?', type=lambda s: int(s, 0), help='byte offset of the version token')
    ap.add_argument('--scan', action='store_true', help='list shader version tokens in FILE')
    args = ap.parse_args()
    with open(args.file, 'rb') as f:
        data = f.read()
    if args.scan:
        for off, kind in scan(data):
            print(f'{off:#x} {kind}')
    elif args.offset is None:
        ap.error('give OFFSET or --scan')
    else:
        lines, names, defs = disasm(data[args.offset:])
        print('\n'.join(lines))


if __name__ == '__main__':
    main()
