"""Read-only particle-system monitor for a running Red Alert 3 v1.12 (ra3_1.12.game, image base 0x400000).

Samples TheParticleSystemManager's system list once per drawn frame (polls TheDisplay's frame counter) via
ReadProcessMemory and writes a per-frame log plus a summary: every system's template parameters, storage
kind, emission (particles created per frame), live and drawn (not yet dead) GPU particle counts, and system
births/deaths, grouped per drawable. Each frame also records the particle LOD thinning state and the status flags
of the drawables that own particle systems. Nothing is written to the game process. Windows only; standard library
only.

Run it while RA3 is in a game (it waits up to --timeout for the game process and for a match to start):
  python tools/psmon.py [--seconds 15] [--timeout 180] [--pid PID] [--out FILE.json.gz]
  --seconds  in-game time to capture; only frame-advancing time counts (pauses and alt-tab are excluded)
  --timeout  give up after this many wall-clock seconds (waiting for the process/game included)
  --out      default: build/psmon_<stock|patched>_<HHMMSS>.json.gz in this repository
If the game runs elevated, run the monitor from an elevated prompt too (OpenProcess needs the same level).
"""
import argparse, collections, ctypes, ctypes.wintypes as wt, gzip, json, os, struct, sys, time

from common import BUILD_DIR

MGR_PTR, DISPLAY_PTR, SYNC_MS, SHADER_MS, LOD_PTR = 0xCE9094, 0xCE08F0, 0xCE6528, 0xCD8C7C, 0xCE8128
VIS_MAX = 32  # compute drawn (not yet dead) GPU particles for systems with at most this many records
NULL_HANDLE = 0xCEA9F8
STORAGE_KIND = {0xC22660: 'GPU', 0xC22C9C: 'TerrainFire', 0xC221D4: 'CPU', 0xC21F54: 'Swarm',
                0xC22044: 'Trail', 0xC22090: 'Wave'}

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                ('szExeFile', ctypes.c_wchar * 260)]


def find_pid():
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    found = []
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower().startswith('ra3_') and e.szExeFile.lower().endswith('.game'):
            found.append((e.th32ProcessID, e.szExeFile))
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return found


class Proc:
    def __init__(self, pid):
        self.h = k32.OpenProcess(0x0010 | 0x1000, False, pid)  # VM_READ | QUERY_LIMITED_INFORMATION
        if not self.h:
            raise OSError(ctypes.get_last_error(), 'OpenProcess failed (run elevated if the game runs as admin)')
        self.n = ctypes.c_size_t()

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(self.n)):
            return None
        return buf.raw

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack('<I', b)[0] if b else None


def cstr(p, addr):
    b = p.read(addr, 64)
    if not b:
        return None
    s = b.split(b'\0')[0]
    if len(s) >= 4 and all(32 <= c < 127 for c in s):
        return s.decode()
    return None


def rv(b, off):
    t, lo, hi = struct.unpack_from('<Iff', b, off)
    return [t, round(lo, 3), round(hi, 3)]


def template_info(p, t):
    b = p.read(t, 0x100)
    if not b:
        return {'addr': hex(t)}
    names = []
    for off in range(0, 0x100, 4):
        v = struct.unpack_from('<I', b, off)[0]
        if 0x10000 <= v < 0x7FFF0000:
            s = cstr(p, v)
            if s:
                names.append((hex(off), s))
    return {'addr': hex(t), 'type': struct.unpack_from('<I', b, 0x50)[0], 'one_shot': b[0x48],
            'sys_life': struct.unpack_from('<i', b, 0x70)[0], 'lifetime': rv(b, 0x64), 'burst_delay': rv(b, 0x9C),
            'burst_count': rv(b, 0xA8), 'initial_delay': rv(b, 0xB4), 'priority': struct.unpack_from('<i', b, 0xDC)[0],
            'strings': names[:6]}


def handle_target(p, h):
    if h in (0, NULL_HANDLE):
        return 0
    v = p.u32(h)
    return v or 0


def drawable_flags(p, ids):
    """Resolve drawable IDs via TheGameClient's hash map ([client+0x88]: buckets [+4], count [+8]; node {id, drawable,
    next}) and return {id: [dword @+0x13C, dword @+0x14C]}. The particle step emits only if byte +0x13C has bit 0x10
    set and bit 8 clear, and byte +0x13E bit 0x20 is clear."""
    client = p.u32(DISPLAY_PTR)
    hb = p.read(client + 0x88 + 4, 8) if client else None
    if not hb:
        return {}
    buckets, nb = struct.unpack('<II', hb)
    out = {}
    for i in ids:
        if not nb:
            break
        node = p.u32(buckets + (i % nb) * 4)
        guard = 0
        while node and guard < 64:
            nb3 = p.read(node, 12)
            if not nb3:
                break
            key, drw, nxt = struct.unpack('<III', nb3)
            if key == i:
                fb = p.read(drw + 0x13C, 0x14)
                if fb:
                    out[i] = [struct.unpack_from('<I', fb, 0)[0], struct.unpack_from('<I', fb, 0x10)[0]]
                break
            node, guard = nxt, guard + 1
    return out


def snapshot(p, mgr):
    sentinel = mgr + 0x38
    node = p.u32(sentinel)
    sync = p.u32(SHADER_MS)
    now = sync * 0.03 if sync is not None else None   # shader age = now - birth; drawn while now <= death
    out = []
    guard = 0
    while node and node != sentinel and guard < 4000:
        nb = p.read(node, 12)
        if not nb:
            break
        nxt, _, sysp = struct.unpack('<III', nb)
        sb = p.read(sysp, 0x148)
        if sb:
            tmpl, = struct.unpack_from('<I', sb, 8)
            storage, = struct.unpack_from('<I', sb, 0x90)
            vt = p.u32(storage) if storage else 0
            kind = STORAGE_KIND.get(vt, hex(vt or 0))
            count = vis = None
            if kind in ('GPU', 'TerrainFire'):
                hb = p.read(storage + 0x10, 8)
                if hb:
                    a, e = struct.unpack('<II', hb)
                    count = (e - a) // 4
                    if 0 < count <= VIS_MAX and now is not None:
                        recs = p.read(a, 4 * count)
                        if recs:
                            vis = 0
                            for r in struct.unpack(f'<{count}I', recs):
                                db = p.read(r + 0x48, 4)
                                if db and struct.unpack('<f', db)[0] >= now:
                                    vis += 1
                    elif count == 0:
                        vis = 0
            elif kind == 'CPU':
                hb = p.read(storage + 8, 8)
                if hb:
                    a, e = struct.unpack('<II', hb)
                    count = (e - a) // 4
            out.append({
                'sys': sysp, 'tmpl': tmpl, 'kind': kind, 'count': count, 'vis': vis,
                'mult': [round(x, 3) for x in struct.unpack_from('<4f', sb, 0x44)],
                'delay': struct.unpack_from('<i', sb, 0x3C)[0], 'flags': sb[0x40], 'flags2': sb[0x41],
                'pos': [round(x, 1) for x in struct.unpack_from('<fff', sb, 0x54)],
                'drawable': struct.unpack_from('<I', sb, 0x9C)[0], 'bone': struct.unpack_from('<I', sb, 0xA0)[0],
                'countdown': struct.unpack_from('<i', sb, 0x104)[0], 'start': struct.unpack_from('<I', sb, 0x108)[0],
                'life': struct.unpack_from('<i', sb, 0x10C)[0], 'created': struct.unpack_from('<I', sb, 0x110)[0],
                'slave': handle_target(p, struct.unpack_from('<I', sb, 0x114)[0]),
                'master': handle_target(p, struct.unpack_from('<I', sb, 0x11C)[0]),
            })
        node = nxt
        guard += 1
    return out


def lod_state(p):
    """Particle LOD (0x711950): particles of systems with priority < [lod+0x324] (type != 8) are thinned to the ones
    where the global counter [lod+0x30C] & mask [lod+0x310] == mask."""
    lod = p.u32(LOD_PTR)
    b = p.read(lod + 0x30C, 0x1C) if lod else None
    if not b:
        return None
    counter, mask = struct.unpack_from('<II', b, 0)
    t320, t324 = struct.unpack_from('<ii', b, 0x14)
    return [mask, t320, t324]


def detect_build(p):
    step_call = p.read(0x7102BA, 5)
    psim_call = p.read(0x63329D, 5)
    fps_ops = p.read(0x6686F9, 4)
    tgt = lambda b, site: struct.unpack('<i', b[1:5])[0] + site + 5 if b and b[0] == 0xE8 else None
    return {'step_call_target': hex(tgt(step_call, 0x7102BA) or 0), 'psim_call_target': hex(tgt(psim_call, 0x63329D) or 0),
            'pacing_operand': hex(struct.unpack('<I', fps_ops)[0]) if fps_ops else None,
            'split_mode_byte_mapped': p.read(0xD30BA0, 1) is not None}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--seconds', type=float, default=15, help='seconds of in-game (frame-advancing) time to capture')
    ap.add_argument('--timeout', type=float, default=180, help='give up after this many wall-clock seconds')
    ap.add_argument('--pid', type=int, help='game process id (default: first ra3_*.game process)')
    ap.add_argument('--out', help='output .json.gz (default: build/psmon_<stock|patched>_<time>.json.gz)')
    args = ap.parse_args()
    pid = args.pid
    start_wall = time.perf_counter()
    while not pid:
        found = find_pid()
        if found:
            pid = found[0][0]
            print('process:', found)
        elif time.perf_counter() - start_wall > args.timeout:
            sys.exit('RA3 game process (ra3_*.game) not found')
        else:
            time.sleep(1)
    p = Proc(pid)
    build = detect_build(p)
    print('build:', build)
    disp = mgr = None
    while not (disp and mgr):
        disp, mgr = p.u32(DISPLAY_PTR), p.u32(MGR_PTR)
        if time.perf_counter() - start_wall > args.timeout:
            sys.exit('display/particle manager not available (start a game first)')
        if not (disp and mgr):
            time.sleep(0.5)
    frames = []
    templates = {}
    last = p.u32(disp + 0x48)
    t0 = time.perf_counter()
    last_t = None
    active = 0.0
    while active < args.seconds and time.perf_counter() - start_wall < args.timeout:
        f = p.u32(disp + 0x48)
        if f is None:
            break
        if f == last:
            time.sleep(0.0005)
            continue
        last = f
        now_t = time.perf_counter()
        if last_t is not None and now_t - last_t < 0.25:   # paused/alt-tabbed time does not count
            active += now_t - last_t
        last_t = now_t
        snap = snapshot(p, mgr)
        for s in snap:
            if s['tmpl'] not in templates:
                templates[s['tmpl']] = template_info(p, s['tmpl'])
        frames.append({'t': round(time.perf_counter() - t0, 4), 'frame': f, 'sync_ms': p.u32(SYNC_MS), 'lod': lod_state(p),
                       'drawables': {hex(k): v for k, v in drawable_flags(p, {s['drawable'] for s in snap if s['drawable']}).items()},
                       'systems': snap})
    tag = 'stock' if build['step_call_target'] == '0x70fe10' and build['psim_call_target'] == '0x710250' else 'patched'
    out = args.out or os.path.join(BUILD_DIR, f'psmon_{tag}_{time.strftime("%H%M%S")}.json.gz')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with gzip.open(out, 'wt') as fh:
        json.dump({'build': build, 'templates': {hex(k): v for k, v in templates.items()}, 'frames': frames}, fh)
    print(f'{len(frames)} frames sampled -> {out}')
    summarize(frames, templates)


def summarize(frames, templates):
    if not frames:
        return
    dt = frames[-1]['t'] - frames[0]['t']
    nfr = frames[-1]['frame'] - frames[0]['frame']
    print(f'duration {dt:.2f}s, drawn frames {nfr} ({nfr / max(dt, 1e-6):.1f} fps), samples {len(frames)} '
          f'(missed {nfr + 1 - len(frames)})')
    life = collections.OrderedDict()
    for fr in frames:
        for s in fr['systems']:
            key = (s['sys'], s['start'])
            e = life.setdefault(key, {'first': fr['t'], 'last': fr['t'], 's0': s, 'counts': [], 'created': [], 'vis': []})
            e['last'] = fr['t']
            e['counts'].append(s['count'] if s['count'] is not None else -1)
            e['created'].append(s['created'])
            e['vis'].append(s.get('vis'))
            e['s1'] = s
    groups = collections.defaultdict(list)
    for key, e in life.items():
        groups[e['s0']['drawable']].append((key, e))
    print(f'{len(life)} system instances; drawable groups: {len(groups)}')
    for drw, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f'\n== drawable {drw:#x}: {len(items)} system instances')
        for (sysp, start), e in items:
            s0, s1 = e['s0'], e['s1']
            tm = templates.get(s0['tmpl'], {})
            name = tm.get('strings', [[None, '?']])[0][1] if tm.get('strings') else '?'
            c = [x for x in e['counts'] if x >= 0]
            emitted = e['created'][-1] - e['created'][0]
            zero = sum(1 for x in c if x == 0)
            v = [x for x in e['vis'] if x is not None]
            vz = sum(1 for x in v if x == 0)
            run = best = 0
            for x in v:
                run = run + 1 if x == 0 else 0
                best = max(best, run)
            vis_txt = f' drawn-zero {vz}/{len(v)} (longest run {best})' if v else ''
            print(f"  sys {sysp:#x} {s0['kind']:<5} tmpl {s0['tmpl']:#x} {name[:40]:<40} seen {e['first']:6.2f}-{e['last']:6.2f}s "
                  f"life {s0['life']}->{s1['life']} flags {s0['flags']:#04x} cnt[min/avg/max] "
                  f"{min(c) if c else '-'}/{(sum(c) / len(c)) if c else 0:.1f}/{max(c) if c else '-'} zero-frames {zero} "
                  f"emitted {emitted}{vis_txt} slave {s0['slave']:#x} master {s0['master']:#x} pos {s0['pos']}")
            print(f"      tmpl: type {tm.get('type')} one_shot {tm.get('one_shot')} sys_life {tm.get('sys_life')} "
                  f"lifetime {tm.get('lifetime')} burst_delay {tm.get('burst_delay')} burst_count {tm.get('burst_count')} "
                  f"init_delay {tm.get('initial_delay')}")


if __name__ == '__main__':
    main()
