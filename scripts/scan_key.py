# -*- coding: utf-8 -*-
"""只读扫描 QQ 进程内存，提取数据库密钥的候选集。

不注入、不 hook、不修改 QQ 的任何文件、不联网 —— 只用 ReadProcessMemory 只读读取。
输出的是「候选」，不是密钥本身；真正的密钥由 verify_key.py 用 SQLCipher 校验筛出。

用法:
  <python> scan_key.py --out-dir <工作目录> [--process QQ.exe] [--dump-memory]

输出到 <工作目录>:
  cand_p0.bin   长度恰为 16 的可打印 ASCII 串（最像真实密钥，优先校验）
  cand_p1.bin   长度 17~40 的串滑动出的 16 位窗口，以及 UTF-16LE 变体
  cand_all.bin  全部候选（上限 400 万，兜底）
  mem_dump.bin  仅在 --dump-memory 时写出。**含整个进程的明文内存，高度敏感**，
                扫完密钥后应删除，不要留在磁盘上、不要提交进版本控制。
"""
import argparse
import ctypes
import os
import re
import sys
from ctypes import wintypes as w

k32 = ctypes.WinDLL('kernel32', use_last_error=True)

TH32CS_SNAPPROCESS = 0x2
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

NEEDLE = b'nt_db\\nt_msg.db'   # 持有消息库的进程内存里会出现这个路径
PRINT_SET = set(range(0x21, 0x7f))
MAX_CANDIDATES = 4_000_000
CHUNK = 1 << 22
UTF_TAIL = 512


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", w.DWORD),
                ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", w.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", w.DWORD), ("Protect", w.DWORD), ("Type", w.DWORD)]


k32.CreateToolhelp32Snapshot.restype = w.HANDLE
k32.OpenProcess.restype = w.HANDLE
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.restype = w.BOOL


def pids_of(name):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32()
    e.dwSize = ctypes.sizeof(e)
    out = []
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.decode('mbcs', 'ignore').lower() == name.lower():
            out.append(e.th32ProcessID)
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def iter_regions(h):
    """枚举已提交且可读的内存区域。"""
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)):
            yield base, size
        addr = base + size
        if addr > 0x7FFFFFFFFFFF:
            break


def iter_chunks(h, dump_fp=None):
    """按可读区域逐块产出内存内容；空洞跳过（不补位，候选提取不依赖地址对齐）。"""
    buf = ctypes.create_string_buffer(CHUNK)
    read = ctypes.c_size_t(0)
    for base, size in iter_regions(h):
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            ok = k32.ReadProcessMemory(h, ctypes.c_void_p(base + off),
                                       buf, n, ctypes.byref(read))
            if ok and read.value:
                d = bytes(buf.raw[:read.value])
                if dump_fp is not None:
                    dump_fp.write(d)
                yield d
                off += read.value
            else:
                off += n


def printable_runs(chunks):
    """产出完整（未被块边界切断）的可打印 ASCII 连续串。"""
    carry = b''
    for chunk in chunks:
        data = carry + chunk
        carry = b''
        i, n = 0, len(data)
        while i < n:
            if data[i] not in PRINT_SET:
                i += 1
                continue
            j = i
            while j < n and data[j] in PRINT_SET:
                j += 1
            if j == n:
                carry = data[i:]        # 尾部可能跨块，留给下一块
            else:
                yield data[i:j]
            i = j
    if carry:
        yield carry


def utf16_runs(chunks):
    """产出 UTF-16LE 编码的可打印串（部分软件把密钥按宽字符存一份）。"""
    tail = b''
    for chunk in chunks:
        data = tail + chunk
        for m in re.finditer(rb'(?:[\x21-\x7e]\x00){16,120}', data):
            try:
                yield m.group().decode('utf-16-le')
            except Exception:
                pass
        tail = data[-UTF_TAIL:]


def write_set(path, items):
    """自描述格式：每条 1 字节长度 + 内容，供 verify_key.py 流式读取。"""
    with open(path, 'wb') as f:
        for c in items:
            f.write(len(c).to_bytes(1, 'big'))
            f.write(c)


def pick_pid(process, dump_path):
    """找到真正持有数据库的进程；顺带把它的内存落一份快照（可选）。"""
    pids = pids_of(process)
    print(f'{process} PIDs: {pids}', flush=True)
    if not pids:
        raise SystemExit(f'没有找到 {process} 进程 —— 请先登录并保持 QQ 运行')
    for pid in pids:
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            print(f'  pid={pid} 打开失败（错误码 {ctypes.get_last_error()}），跳过', flush=True)
            continue
        # 先只探测 needle，避免为无关进程写快照
        hit = False
        for d in iter_chunks(h):
            if NEEDLE in d:
                hit = True
                break
        k32.CloseHandle(h)
        print(f'  pid={pid} 命中数据库路径: {hit}', flush=True)
        if hit:
            return pid
    raise SystemExit('没有 QQ 进程的内存中出现数据库路径 —— 数据库可能未打开')


def main():
    ap = argparse.ArgumentParser(description='只读扫描 QQ 进程内存，提取密钥候选')
    ap.add_argument('--out-dir', required=True, help='候选文件与（可选）内存快照的落盘目录')
    ap.add_argument('--process', default='QQ.exe', help='目标进程名，默认 QQ.exe')
    ap.add_argument('--dump-memory', action='store_true',
                    help='额外写出 mem_dump.bin（默认不写；该文件含明文内存，敏感）')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    pid = pick_pid(args.process, args.dump_memory)
    print(f'目标进程 pid={pid}', flush=True)

    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        raise SystemExit(f'OpenProcess 失败，错误码 {ctypes.get_last_error()}')

    dump_fp = None
    dump_path = os.path.join(args.out_dir, 'mem_dump.bin')
    if args.dump_memory:
        dump_fp = open(dump_path, 'wb')

    def source():
        for d in iter_chunks(h, dump_fp):
            yield d

    p0, p1, allc = set(), set(), set()
    total = 0
    try:
        for run in printable_runs(source()):
            total += len(run)
            ln = len(run)
            if ln == 16:
                p0.add(run)
            elif 17 <= ln <= 40:
                for k in range(ln - 15):
                    p1.add(run[k:k + 16])
            if 16 <= ln <= 128:
                for k in range(ln - 15):
                    allc.add(run[k:k + 16])
            if len(allc) > MAX_CANDIDATES:
                break
    finally:
        k32.CloseHandle(h)
        if dump_fp is not None:
            dump_fp.close()

    # 宽字符变体：重新读一遍内存，只跑正则
    if len(allc) <= MAX_CANDIDATES:
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        try:
            for s in utf16_runs(iter_chunks(h)):
                if len(s) == 16:
                    p0.add(s.encode('ascii'))
                else:
                    for k in range(len(s) - 15):
                        p1.add(s[k:k + 16].encode('ascii'))
                if len(allc) < MAX_CANDIDATES:
                    for k in range(len(s) - 15):
                        allc.add(s[k:k + 16].encode('ascii'))
        finally:
            k32.CloseHandle(h)

    write_set(os.path.join(args.out_dir, 'cand_p0.bin'), p0)
    write_set(os.path.join(args.out_dir, 'cand_p1.bin'), p1 - p0)
    write_set(os.path.join(args.out_dir, 'cand_all.bin'), allc)
    print(f'p0={len(p0)}  p1={len(p1) - len(p0)}  all={len(allc)}', flush=True)
    if args.dump_memory:
        print(f'内存快照已写出: {dump_path}（敏感，用完请删）', flush=True)


if __name__ == '__main__':
    sys.exit(main())
