# -*- coding: utf-8 -*-
"""用候选集暴力校验出真正的数据库密钥。

候选动辄数万条，逐个起 SQLCipher 连接很慢 —— 用多进程分片并行校验。
判定标准：能对 sqlite_master 执行一次 SELECT 即算通过。

用法:
  <python> verify_key.py --candidates <cand_p0.bin> --db <nt_msg.db> [--work-dir <目录>]

db 可以直接给 QQ 原始文件（带 1024 字节自定义头），脚本会自行剥离到 work-dir。
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ntdb  # noqa: E402

from pysqlcipher3 import dbapi2 as sqlcipher  # noqa: E402

_W = {}


def _init(db):
    _W['db'] = db


def load_candidates(path):
    """读取 scan_key.py 写的「1 字节长度 + 内容」自描述格式。"""
    out = []
    with open(path, 'rb') as f:
        while True:
            ln = f.read(1)
            if not ln:
                break
            out.append(f.read(ln[0]))
    return out


def try_key(kb):
    key = kb.decode('ascii', 'ignore')
    if "'" in key:          # 含单引号无法安全嵌入 PRAGMA，且真实密钥不含
        return None
    try:
        con = sqlcipher.connect(_W['db'], timeout=5)
        try:
            ntdb.apply_key(con.cursor(), key)
        finally:
            con.close()
        return key
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description='校验候选集，找出能打开数据库的密钥')
    ap.add_argument('--candidates', required=True, help='scan_key.py 产出的候选文件')
    ap.add_argument('--db', required=True, help='QQ 数据库原始文件（可带自定义头）')
    ap.add_argument('--work-dir', help='剥离自定义头后的落盘目录（db 带自定义头时必需）')
    ap.add_argument('--limit', type=int, help='只校验前 N 条候选（试探用）')
    args = ap.parse_args()

    db = ntdb.usable_db(args.db, args.work_dir) if ntdb.has_qq_header(args.db) else args.db
    cands = load_candidates(args.candidates)
    if args.limit:
        cands = cands[:args.limit]
    print(f'候选数: {len(cands)}  目标库: {db}', flush=True)

    t0 = time.time()
    found = []
    with Pool(initializer=_init, initargs=(db,)) as pool:
        for i, r in enumerate(pool.imap_unordered(try_key, cands, chunksize=64), 1):
            if r:
                found.append(r)
                print('*** 找到密钥:', r, flush=True)
            if i % 20000 == 0:
                print(f'  {i}/{len(cands)}  已用 {time.time() - t0:.0f}s', flush=True)

    print(f'完成，用时 {time.time() - t0:.0f}s，命中 {len(found)} 个: {found}', flush=True)
    if not found:
        print('未命中 —— 换 cand_p1.bin / cand_all.bin 重试，或确认 QQ 正在运行且已登录',
              flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
