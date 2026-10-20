# QQ Chat Export

从本机 NTQQ（新版 QQ）的加密本地库导出指定群聊的聊天记录为可读文本，覆盖「定位数据 → 只读提取数据库密钥 → 解密 SQLCipher 库 → 选群 → 解析消息体 → 生成 TXT」全链路。

> An agent skill that exports NTQQ group chat history from local SQLCipher databases into readable text, using a read-only key scan of the running process.

## 功能

- 只读扫描持有数据库的 QQ 进程内存，产出密钥候选集，不注入、不 hook、不需要管理员权限
- 多进程并行校验候选，判定标准是能成功 `SELECT` 一次 `sqlite_master`
- 剥离库文件前置的 1024 字节自定义头后解密为**标准 SQLite**，之后用普通 `sqlite3` 即可读取
- 按群号与起始日期筛选，输出按自然日分段的 UTF-8 文本
- 非文本消息保留占位（图片、文件、语音、卡片），引用消息回溯还原被引内容

## 依赖

| 类别 | 要求 |
|---|---|
| 运行环境 | 名为 `skill-qq-chat-export` 的 Python **3.11** 环境，SQLCipher 的 Windows 预编译 wheel 只发布 cp311 |
| 依赖包 | 见 `scripts/requirements.txt`；环境创建与登记见 `references/environment-setup.md` |
| 运行时前提 | 目标机器上运行着已登录的 NTQQ 桌面版，且目标记录已同步到本地 |

密钥只经环境变量 `QQ_DB_KEY` 传递，不写入命令行、不提交进版本控制、不写入日志。

## 快速开始

```bash
<python> scripts/scan_key.py --out-dir <工作目录>
<python> scripts/verify_key.py --candidates <工作目录>/cand_p0.bin \
    --db "<Tencent Files>/<QQ号>/nt_qq/nt_db/nt_msg.db" --work-dir <工作目录>

export QQ_DB_KEY='<校验得到的 16 位密钥>'
<python> scripts/decrypt_db.py --db "…/nt_msg.db" --out <工作目录>/nt_msg_plain.db --work-dir <工作目录>

<python> scripts/group_export.py --msg-db <工作目录>/nt_msg_plain.db \
    --info-db <工作目录>/group_info_plain.db --list --since 2026-08-01
```

QQ 必须保持运行：密钥不在任何文件里，关掉进程就取不到。同一账号的全部 `nt_db` 库共用同一密钥，且不随重启变化，解一次可解全部。

## 产物

| 文件 | 内容 |
|---|---|
| `cand_p0.bin`、`cand_p1.bin`、`cand_all.bin` | 密钥候选集，按命中优先级排列 |
| `<工作目录>/nt_msg_plain.db` | 解密后的消息库（标准 SQLite） |
| `<工作目录>/group_info_plain.db` | 解密后的群信息库 |
| `<输出目录>/<群名>_<起始日期>.txt` | 导出文本，UTF-8 BOM + CRLF，按自然日分段 |

每条消息格式为 `[HH:MM:SS] 发送者` 加正文；非文本消息以方括号占位符表示。

## 适用范围与代价

**官方已移除导出入口。** 新版 NTQQ 取消「消息管理器 → 导出消息记录」，因此只能走本地库路线。

**密钥提取属于客户端逆向。** 读进程内存取密钥、解密本地库都不是 QQ 官方支持的手段。自用归档与再分发是两件事，公开或商用前需自行确认当地法律与平台协议。

**导出内容的隐私权不属于导出者。** 聊天记录含其他成员的发言，默认应作私有数据处理，不公开、不外传。这也是本 skill 把密钥限定在环境变量、把产物全部落在工作目录的原因。

## 已知限制

- 仅支持 NTQQ；旧版 PCQQ 的库结构与本流程不兼容。
- `--since` 之前的历史若未同步到本地，无法导出，只能从源设备补同步。
- 「已撤回」等系统提示的文案未必落库，会渲染成 `[系统消息]`。
- 群名与昵称依赖 `group_info.db`；该库未解密或成员不在本地群成员表时，回退显示 QQ 号。
- 密钥随 QQ 版本更新可能改变内存特征，`scan_key.py` 的扫描模式需同步调整。

## 项目结构

```
.
├── SKILL.md                        agent 入口：四步流程与边界
├── README.md                       本文件
├── LICENSE                         MIT
├── references/
│   ├── environment-setup.md        Python 3.11 环境与依赖
│   └── nt-db-format.md             库头结构、消息元素类型、引用字段
└── scripts/
    ├── scan_key.py                 只读扫描进程内存取密钥候选
    ├── verify_key.py               并行校验候选
    ├── decrypt_db.py               剥头并解密为标准 SQLite
    ├── ntdb.py                     库读取的公共封装
    ├── group_export.py             选群与导出
    └── requirements.txt            依赖清单
```

整个目录放入 skills 目录即可，`SKILL.md` 是入口。

## License

[MIT](LICENSE) © 2026 zhmge
