# -*- coding: utf-8 -*-
"""NTQQ 本地库的通用处理：自定义头识别、SQLCipher 参数、明文导出。

QQ（NTQQ）的 nt_db/*.db 不是标准 SQLite 文件：正文之前有 1024 字节自定义头
（含 "QQ_NT DB" 标记），其后才是完整的一页页 SQLCipher 内容。SQLCipher 打开前
必须先剥掉这 1024 字节，否则一律报 "file is not a database"。
"""
import os
import shutil

HEADER_SIZE = 1024
SQLITE_MAGIC = b'SQLite format 3\x00'
QQ_MARK = b'QQ_NT DB'

# 参数顺序敏感：page_size 必须在 key 之前设置。
CIPHER_PRAGMAS = (
    'PRAGMA cipher_page_size = 4096',
    'PRAGMA kdf_iter = 4000',
    'PRAGMA cipher_hmac_algorithm = HMAC_SHA1',
    'PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512',
)


def has_qq_header(path):
    """判断文件是否带 QQ 的 1024 字节自定义头。"""
    with open(path, 'rb') as f:
        head = f.read(HEADER_SIZE)
    return not head.startswith(SQLITE_MAGIC)


def strip_header(src, dst):
    """把 src 的第 1024 字节之后的内容写入 dst。"""
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        fi.seek(HEADER_SIZE)
        shutil.copyfileobj(fi, fo, 1 << 20)
    return dst


def usable_db(path, work_dir=None):
    """返回一个可交给 SQLCipher 的库路径：带自定义头则先剥离到 work_dir。"""
    if not has_qq_header(path):
        return path
    if work_dir is None:
        raise SystemExit(
            f'{path} 带 QQ 自定义头（{HEADER_SIZE} 字节），需要 --work-dir 指定剥离后的落盘目录')
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.basename(path)
    out = os.path.join(work_dir, base.replace('.db', '') + '_stripped.db')
    return strip_header(path, out)


def apply_key(cur, key):
    """按 NTQQ 的加密参数套用密钥，顺序不可调整。

    校验阶段会有海量错误密钥，SQLCipher 默认把每次 HMAC 失败打到 stderr；
    先把日志级别压到 NONE，否则输出会被刷屏。
    """
    try:
        cur.execute('PRAGMA cipher_log_level = NONE')
    except Exception:
        pass          # 老版本没有这个 pragma，忽略
    cur.execute(CIPHER_PRAGMAS[0])
    cur.execute("PRAGMA key = '%s'" % key.replace("'", "''"))
    for p in CIPHER_PRAGMAS[1:]:
        cur.execute(p)
    cur.execute('SELECT count(*) FROM sqlite_master')
    return cur.fetchone()[0]


def read_key(env_var='QQ_DB_KEY'):
    """密钥只从环境变量读，不落命令行、不落日志。"""
    key = os.environ.get(env_var)
    if not key:
        raise SystemExit(
            f'缺少环境变量 {env_var}。用法：设置该变量为数据库密钥后再运行，'
            '不要把密钥写进命令行或提交进版本控制。')
    return key
