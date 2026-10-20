# -*- coding: utf-8 -*-
"""从已解密的明文库导出指定群聊的聊天记录为可读 TXT。

只读明文库，不改动任何原始数据；不联网。

用法:
  # 先看有哪些群、各多少条
  <python> group_export.py --msg-db <明文 nt_msg.db> --info-db <明文 group_info.db> --list

  # 再导出指定群
  <python> group_export.py --msg-db <明文 nt_msg.db> --info-db <明文 group_info.db> \
      --out-dir <输出目录> --since 2026-08-01 --groups 123456,234567

`--msg-db` 给的是**解密后**的明文库。加密库请先用 decrypt_db.py 处理。
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

# ── group_msg_table 的列名是数字，SQL 里必须加双引号，否则会被当成整数常量 ──
C_ID = '40001'      # 消息 ID（全局唯一，用于解析引用）
C_SEQ = '40003'     # 会话内序号
C_UID = '40020'     # 发送者 UID（u_xxx）
C_GID = '40027'     # 群号
C_UIN = '40033'     # 发送者 QQ 号
C_TIME = '40050'    # Unix 秒
C_CARD = '40090'    # 群名片
C_NICK = '40093'    # 昵称
C_BODY = '40800'    # 消息体 protobuf


# ─────────────────────────── protobuf 最小解析 ───────────────────────────
def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7f) << s
        if not x & 0x80:
            return r, i
        s += 7


def fields(b):
    i, n = 0, len(b)
    while i < n:
        try:
            k, i = _varint(b, i)
        except IndexError:
            return
        f, wt = k >> 3, k & 7
        if wt == 0:
            v, i = _varint(b, i)
            yield f, wt, v
        elif wt == 2:
            ln, i = _varint(b, i)
            yield f, wt, b[i:i + ln]
            i += ln
        elif wt == 5:
            yield f, wt, b[i:i + 4]
            i += 4
        elif wt == 1:
            yield f, wt, b[i:i + 8]
            i += 8
        else:
            return


def s_of(v):
    """库里的 TEXT 可能以 str 或 bytes 返回，统一成 str。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8')
        except Exception:
            return None
    return None


def human_size(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f}{u}' if u == 'B' else f'{n:.1f}{u}'
        n /= 1024
    return f'{n:.1f}TB'


# ─────────────────────────── 消息体元素渲染 ───────────────────────────
def render_elem(sub, ctx):
    """把一个元素渲染成文本片段。ctx = {'quote': fn, 'names': {uid: 名字}}"""
    t = sub.get(45002)
    if t == 1:
        return s_of(sub.get(45101)) or ''
    if t == 2:
        tip = s_of(sub.get(45815)) or ''
        return tip if tip.startswith('[') else '[图片]'
    if t == 3:
        return f'[文件] {s_of(sub.get(45402)) or "未知文件"}（{human_size(sub.get(45405))}）'
    if t == 4:
        return f'[语音] {sub.get(45906) or 0}″'
    if t == 5:
        fn = s_of(sub.get(45402))
        return f'[视频] {fn}' if fn else '[视频]'
    if t == 6:
        return s_of(sub.get(47602)) or '[表情]'
    if t == 7:
        src = ctx['quote'](sub)
        return f'[回复 {src}]' if src else '[回复]'
    if t == 8:
        return _render_gray_tip(sub, ctx)
    if t == 9:
        return '[互动消息]'
    if t == 10:
        js = s_of(sub.get(47901))
        if js:
            try:
                d = json.loads(js)
                p = d.get('prompt') or d.get('config', {}).get('prompt')
                if p:
                    return f'[卡片] {p}'
            except Exception:
                pass
        return '[卡片消息]'
    if t == 11:
        return s_of(sub.get(80900)) or '[表情包]'
    if t == 14:
        fn = s_of(sub.get(48705))
        return f'[闪传] {fn}' if fn else '[闪传]'
    if t == 16:
        xml = s_of(sub.get(48602)) or ''
        m = re.search(r'brief="([^"]*)"', xml)
        return m.group(1) if m else '[聊天记录]'
    if t == 17:
        return '[消息]'
    if t == 21:
        return s_of(sub.get(48153)) or '[通话]'
    return f'[未知消息类型 {t}]'


def _render_gray_tip(sub, ctx):
    """灰条：撤回提示、拍一拍、系统动作。文案不一定存在库里，拿不到就降级。"""
    txt = (s_of(sub.get(47713)) or '').strip()
    if txt:
        who = s_of(sub.get(47705)) or s_of(sub.get(47714)) or ''
        return f'[系统] {who} {txt}'.replace('  ', ' ').strip()
    js = s_of(sub.get(48271))          # 拍一拍：JSON 描述序列
    if js:
        try:
            buf = []
            for it in json.loads(js).get('items', []):
                if it.get('type') == 'qq':
                    nm = it.get('nm') or ctx['names'].get(it.get('uid')) or ''
                    if nm:
                        buf.append(nm)
                elif it.get('type') == 'nor':
                    buf.append(it.get('txt', ''))
            s = ''.join(buf).strip()
            if s:
                return f'[系统] {s}'
        except Exception:
            pass
    if sub.get(45003) == 4 and sub.get(48504) and sub.get(48507):
        return f'[系统] {s_of(sub[48504])} 拍了拍 {s_of(sub[48507])}'
    return '[系统消息]'


def decode_body(body, ctx):
    """把 40800 blob 解析成正文文本。"""
    if not body:
        return ''
    parts = []
    for f, wt, v in fields(body):
        if f == 40800 and wt == 2:
            seg = render_elem({k: vv for k, _, vv in fields(v)}, ctx)
            if seg:
                parts.append(seg)
    # 方括号占位符后补空格，避免与正文粘连
    return ''.join(s + ' ' if s.startswith('[') and s.endswith(']') else s
                   for s in parts).strip()


# ─────────────────────────── 群名 / 成员名 ───────────────────────────
def load_info(path):
    con = sqlite3.connect(path)
    cur = con.cursor()
    gname = {str(code): (s_of(nm) or str(code))
             for code, nm in cur.execute('select "60001","60007" from group_list')}
    uid2name, uin2name = {}, {}
    for gid, uid, uin, card, nick in cur.execute(
            'select "60001","1000","1002","64003","20002" from group_member3'):
        nm = (s_of(card) or '').strip() or (s_of(nick) or '').strip()
        if not nm:
            continue
        uid2name.setdefault(str(gid), {})[uid] = nm
        if uin:
            uin2name.setdefault(str(gid), {})[uin] = nm
    con.close()
    return gname, uid2name, uin2name


def list_groups(msg_db, info_db, cut, until):
    gname, _, _ = load_info(info_db) if info_db and os.path.exists(info_db) else ({}, {}, {})
    con = sqlite3.connect(msg_db)
    q = (f'select "{C_GID}", count(*), max("{C_TIME}") from group_msg_table '
         f'where "{C_TIME}" >= ? and "{C_TIME}" <= ? group by 1 order by 2 desc')
    rows = con.execute(q, (cut, until)).fetchall()
    con.close()
    print(f'{"群名":<34}{"群号":>14}{"条数":>9}   最后消息')
    for gid, c, mx in rows:
        nm = gname.get(str(gid), '(不在群列表中)')
        if len(nm) > 16:
            nm = nm[:16] + '…'
        last = datetime.datetime.fromtimestamp(mx).strftime('%Y-%m-%d %H:%M')
        print(f'{nm:<34}{gid:>14}{c:>9}   {last}')
    print(f'\n合计 {len(rows)} 个群。用 --groups <群号,群号> 指定要导出的群。')


# ─────────────────────────── 导出 ───────────────────────────
def export(gid, msg_db, info_db, out_dir, since, until, self_uin, members, uins, gname):
    con = sqlite3.connect(msg_db)
    cur = con.cursor()
    m2n = members.get(str(gid), {})
    m2q = uins.get(str(gid), {})

    # 引用解析：先建一份「消息 ID -> 正文」索引（引用内的引用不再递归）
    no_quote = {'quote': lambda s: '[引用]', 'names': m2n}
    idx = {mid: decode_body(body, no_quote)
           for mid, body in cur.execute(
               f'select "{C_ID}","{C_BODY}" from group_msg_table where "{C_GID}" = ?', (gid,))}

    def resolve_name(uid, uin, card, nick):
        if uid and m2n.get(uid):
            return m2n[uid]
        if uin and m2q.get(uin):
            return m2q[uin]
        return (card or '').strip() or (nick or '').strip() or (f'QQ{uin}' if uin else '系统')

    def quote_resolver(sub):
        qid, qu = sub.get(47422), sub.get(47403)
        qname = '我' if qu == self_uin else (
            m2q.get(qu) or (f'QQ{qu}' if qu else '某人'))
        t = idx.get(qid, '').replace('\n', ' ')
        if len(t) > 40:
            t = t[:40] + '…'
        return f'{qname}：{t}' if t else qname

    ctx = {'quote': quote_resolver, 'names': m2n}
    rows = cur.execute(
        f'''select "{C_TIME}","{C_UIN}","{C_UID}","{C_CARD}","{C_NICK}","{C_BODY}"
            from group_msg_table
            where "{C_GID}" = ? and "{C_TIME}" >= ? and "{C_TIME}" <= ?
            order by "{C_TIME}" asc, "{C_SEQ}" asc''', (gid, since, until)).fetchall()
    con.close()

    name = gname.get(str(gid), str(gid))
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', name).strip()[:24] or str(gid)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'{safe}_{gid}.txt')

    n_text = n_media = n_other = 0
    with open(path, 'w', encoding='utf-8-sig', newline='\r\n') as fp:
        fp.write('=' * 60 + '\n')
        fp.write(f'群名称：{name}\n群号：{gid}\n')
        fp.write('时间范围：%s ~ %s（本地时间）\n' % (
            datetime.datetime.fromtimestamp(since).strftime('%Y-%m-%d %H:%M:%S'),
            datetime.datetime.fromtimestamp(until).strftime('%Y-%m-%d %H:%M:%S')))
        fp.write(f'消息条数：{len(rows)}\n')
        fp.write('=' * 60 + '\n\n')
        last_day = None
        for ts, uin, uid, card, nick, body in rows:
            dt = datetime.datetime.fromtimestamp(ts)
            day = dt.strftime('%Y-%m-%d')
            if day != last_day:
                fp.write(f'\n{"-" * 20} {day} {"-" * 20}\n\n')
                last_day = day
            who = resolve_name(uid, uin, s_of(card), s_of(nick))
            if self_uin is not None and uin == self_uin:
                who += '（我）'
            content = decode_body(body, ctx) or '[空消息]'
            if content.startswith('['):
                if content.startswith(('[图片', '[视频', '[表情')):
                    n_media += 1
                elif not content.startswith(('[回复', '[系统', '[空')):
                    n_other += 1
            else:
                n_text += 1
            fp.write(f'[{dt.strftime("%H:%M:%S")}] {who}\n{content}\n\n')
    return path, len(rows), (n_text, n_media, n_other)


def main():
    ap = argparse.ArgumentParser(description='从明文库导出 QQ 群聊记录')
    ap.add_argument('--msg-db', required=True, help='明文 nt_msg.db')
    ap.add_argument('--info-db', help='明文 group_info.db（用于解析群名与成员名）')
    ap.add_argument('--out-dir', default='.', help='TXT 输出目录')
    ap.add_argument('--since', default='1970-01-01', help='起始日期 YYYY-MM-DD（含当天 00:00:00）')
    ap.add_argument('--until', help='截止日期 YYYY-MM-DD（默认到现在）')
    ap.add_argument('--groups', help='要导出的群号，逗号分隔')
    ap.add_argument('--self-uin', type=int, help='本人 QQ 号，用于标注「（我）」')
    ap.add_argument('--list', action='store_true', help='只列出候选群，不导出')
    args = ap.parse_args()

    since = int(datetime.datetime.strptime(args.since, '%Y-%m-%d').timestamp())
    if args.until:
        until = int((datetime.datetime.strptime(args.until, '%Y-%m-%d')
                     + datetime.timedelta(days=1)).timestamp()) - 1
    else:
        until = int(datetime.datetime.now().timestamp())

    if args.list:
        list_groups(args.msg_db, args.info_db, since, until)
        return 0
    if not args.groups:
        raise SystemExit('要么加 --list 先看候选群，要么用 --groups 指定要导出的群号')
    if not args.info_db:
        raise SystemExit('导出需要 --info-db，否则解析不出群名与成员名')

    gname, members, uins = load_info(args.info_db)
    gids = [int(x) for x in args.groups.replace(' ', '').split(',') if x]
    for gid in gids:
        path, n, kinds = export(gid, args.msg_db, args.info_db, args.out_dir,
                                since, until, args.self_uin, members, uins, gname)
        print(f'{gname.get(str(gid), gid)}（{gid}） {n} 条 → {path}')
        print(f'   文本 {kinds[0]} / 媒体占位 {kinds[1]} / 其他 {kinds[2]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
