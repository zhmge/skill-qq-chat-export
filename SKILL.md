---
name: qq-chat-export
description: 从本机 NTQQ（新版 QQ）的本地数据库导出指定群聊的聊天记录为可读文本，覆盖「定位数据 → 只读提取数据库密钥 → 解密 SQLCipher 库 → 选群 → 解析消息体 → 生成 TXT」全链路。适用于需要把 QQ 聊天记录落成文件做归档、检索或统计的场景。不适用于：旧版 QQ（非 NTQQ）的数据、微信等其它 IM、以及「换机搬家」这类 QQ 自带聊天记录备份/迁移就能完成的需求。
---

# QQ 聊天记录导出（NTQQ 本地库）

QQ 官方在新版 NTQQ 中**移除了**旧版 PCQQ 的「消息管理器 → 导出消息记录」。本 skill 走
本地数据库路线：QQ 把消息存在 SQLCipher 加密的 SQLite 库里，密钥只存在于运行中的
QQ 进程内存里。据此四步可完整还原为可读文本。

## 前提与边界

- **目标机器上必须运行着已登录的 NTQQ 桌面版**，且目标聊天记录已同步到本地。
- 全程**只读**：只读进程内存、只读数据库文件。不注入、不 hook、不改 QQ 的任何文件、不联网。
- 「读进程内存取密钥 + 解密本地库」**不是 QQ 官方支持的手段**，属于客户端逆向范畴。
  公开或商用前自行确认当地法律与平台协议；导出的聊天记录含**第三人隐私**，
  默认应当作私有数据处理、不公开、不外传。
- 密钥是**凭据**：只经环境变量传递，不要写进命令行、不要提交进版本控制、不要写进日志。

## 工作流

四步串行，任一步失败就不要往后走。

### 1. 定位数据

从 QQ 设置里找到数据目录（默认在「文档」下），结构固定为：

```text
<Tencent Files>/<QQ号>/nt_qq/nt_db/
    nt_msg.db          消息主库（群聊 + 私聊 + 频道）
    group_info.db      群信息：群名、群成员
    profile_info.db    资料
    nt_msg.db-wal      未落盘的增量（有就一起处理）
```

库文件前 1024 字节是 QQ 的自定义头，其后才是 SQLCipher 内容 —— 详见
[`references/nt-db-format.md`](references/nt-db-format.md)。

### 2. 只读提取密钥

```bash
<python> scripts/scan_key.py --out-dir <工作目录>
```

扫描持有数据库的 QQ 进程内存，产出候选集 `cand_p0/p1/all.bin`。
**这是只读扫描**，靠 `ReadProcessMemory` 完成，不需要管理员权限、不需要重启 QQ、
不会触发登录风控。（加 `--dump-memory` 会额外落一份完整内存快照，敏感，默认不开。）

QQ 必须保持运行 —— 密钥不在任何文件里，关掉 QQ 就取不到了。

### 3. 校验密钥并解密

```bash
<python> scripts/verify_key.py --candidates <工作目录>/cand_p0.bin --db <…>/nt_msg.db \
    --work-dir <工作目录>
```

候选动辄数万条，脚本用多进程并行做 SQLCipher 校验，判定标准是「能 SELECT 一次
`sqlite_master`」。命中后会打印 16 位密钥。p0 未命中就换 p1、再换 all。

拿到密钥后解密：

```bash
# 密钥只经环境变量传入
export QQ_DB_KEY='<上一步得到的密钥>'
<python> scripts/decrypt_db.py --db <…>/nt_msg.db --out <工作目录>/nt_msg_plain.db \
    --work-dir <工作目录>
<python> scripts/decrypt_db.py --db <…>/group_info.db --out <工作目录>/group_info_plain.db \
    --work-dir <工作目录>
```

同一账号的所有 `nt_db` 库共用同一密钥，且不随重启变化 —— 解一次可以解全部。

产物是**标准 SQLite**，之后用普通 `sqlite3` 模块读即可，不再需要 SQLCipher。

### 4. 选群并导出

```bash
# 先列候选群
<python> scripts/group_export.py --msg-db <工作目录>/nt_msg_plain.db \
    --info-db <工作目录>/group_info_plain.db --list --since 2026-08-01

# 再导出
<python> scripts/group_export.py --msg-db <工作目录>/nt_msg_plain.db \
    --info-db <工作目录>/group_info_plain.db --out-dir <输出目录> \
    --since 2026-08-01 --groups <群号,群号> --self-uin <本人QQ号>
```

输出 UTF-8 BOM + CRLF 的 TXT，按自然日分段，每条为 `[HH:MM:SS] 发送者` 加正文，
非文本消息留占位（`[图片]` `[文件] xxx.zip（10.5MB）` `[语音] 50″` `[卡片]` …），
引用解析为 `[回复 某人：被引内容]`（正文从同一个库里回溯还原）。

消息体元素类型、发送者字段、引用字段的完整对照表在
[`references/nt-db-format.md`](references/nt-db-format.md) —— 要扩展消息类型或写自己的
解析器时读它。

## 排错

| 现象 | 原因与处置 |
| --- | --- |
| `没有找到 QQ.exe 进程` | QQ 没运行。密钥只在内存里，必须先启动并登录 |
| 进程里搜不到 `nt_db\nt_msg.db` | 数据库未打开。点开要导出的群聊让 QQ 加载，或确认用的是 NTQQ 而非旧版 |
| 所有候选都校验失败 | 多半是数据库没剥掉 1024 字节自定义头；或换 `cand_p1.bin`/`cand_all.bin` 重试 |
| `file is not a database` | 同上：自定义头未剥离，或密钥错误 |
| 导出条数明显偏少 | `--since` 之前的历史消息未被 QQ 同步到本地，只能从源设备补同步 |
| 群里看到的消息没导出来 | 「已撤回」等系统提示的文案 QQ 未必落库，会渲染成 `[系统消息]` |
| 群名/昵称解析不出，回退成 QQ 号 | `--info-db` 没给，或该成员不在本地群成员表里 |

## 环境与依赖

需要名为 `skill-qq-chat-export` 的 Python 环境（**Python 3.11**，因为 SQLCipher 的
Windows 预编译 wheel 只发布 cp311）。依赖清单见
[`scripts/requirements.txt`](scripts/requirements.txt)，环境的创建与登记规则见
[`references/environment-setup.md`](references/environment-setup.md)。
