# -*- coding: utf-8 -*-
"""用密钥把 QQ 的 SQLCipher 数据库导出为明文 SQLite。

用法:
  <python> decrypt_db.py --db <nt_msg.db> --out <明文.db> [--work-dir <目录>]
  密钥从环境变量 QQ_DB_KEY 读取（不写入命令行、不写入文件）。

产物是标准 SQLite，之后用普通 sqlite3 模块读即可，不再需要 SQLCipher。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ntdb  # noqa: E402

from pysqlcipher3 import dbapi2 as sqlcipher  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='解密 QQ 数据库为明文 SQLite')
    ap.add_argument('--db', required=True, help='QQ 数据库原始文件（可带自定义头）')
    ap.add_argument('--out', required=True, help='明文库输出路径')
    ap.add_argument('--work-dir', help='剥离自定义头后的落盘目录（db 带自定义头时必需）')
    args = ap.parse_args()

    key = ntdb.read_key()
    src = ntdb.usable_db(args.db, args.work_dir) if ntdb.has_qq_header(args.db) else args.db
    if os.path.exists(args.out):
        os.remove(args.out)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)

    con = sqlcipher.connect(src)
    cur = con.cursor()
    n = ntdb.apply_key(cur, key)
    print(f'{os.path.basename(src)}: 表数量 {n} —— 密钥正确', flush=True)

    cur.execute("ATTACH DATABASE ? AS plaintext KEY ''", (args.out,))
    cur.execute("SELECT sqlcipher_export('plaintext')")
    cur.execute('DETACH DATABASE plaintext')
    con.commit()
    con.close()
    print(f'已导出明文库: {args.out}', flush=True)


if __name__ == '__main__':
    sys.exit(main())
