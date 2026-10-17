# 闲鱼自动发货 × AstrBot 插件

把 [AutoDeliver](https://github.com/Makima04/AutoDeliver)（Rust 写的一体化闲鱼自动发货平台）接到 AstrBot，
在 QQ 里用指令完成「登录平台 → 管理闲鱼账号 → 搜行情 → 大模型总结回传」。

```
QQ 用户 ──指令──▶ AstrBot ──▶ 本插件 ──HTTP/Bearer──▶ AutoDeliver(axum)
                    ▲                                      │
                    └────────── 摘要 / 二维码 ──────────────┘
```

插件本身**不碰闲鱼协议**，所有能力都走 AutoDeliver 现成的管理端 REST API，
所以登录态、验签、卡密扣减、发货幂等这些逻辑只有一份，插件只负责「对话包装」。

---

## 一、安装

> 插件只依赖 `httpx` + `qrcode`（纯 Python，**不需要 Pillow**），ARM 小主机 / Docker 容器里都能直接装。

1. 确认 AutoDeliver 已经跑起来（并记下它的地址）：

   ```bash
   cd AutoDeliver && cargo run
   curl -s http://127.0.0.1:8080/ -o /dev/null -w "%{http_code}\n"   # 200 就对了
   ```

2. 装进 AstrBot，三种方式任选：

   **A. WebUI 从仓库安装（推荐）**
   插件管理 → 安装插件 → 填本仓库地址 `https://github.com/Makima04/autodeliver_astrbot`，
   AstrBot 会自动 clone 并安装 `requirements.txt`。

   **B. 服务器上手动 clone**

   ```bash
   cd /path/to/AstrBot/data/plugins
   git clone https://github.com/Makima04/autodeliver_astrbot.git
   pip install -r autodeliver_astrbot/requirements.txt
   ```

   **C. Docker 部署的 AstrBot**（容器内 pip 装依赖）

   ```bash
   HOST_DATA=/home/orangepi/astrbot/data          # 宿主机上挂到容器 /AstrBot/data 的目录
   git clone https://github.com/Makima04/autodeliver_astrbot.git \
             $HOST_DATA/plugins/autodeliver_astrbot
   docker exec astrbot pip install -r /AstrBot/data/plugins/autodeliver_astrbot/requirements.txt
   docker restart astrbot
   ```

   开发期想「改完立刻生效」可以用软链：`ln -s $PWD <AstrBot>/data/plugins/autodeliver_astrbot`，
   改代码后在 WebUI 插件卡片点「重载插件」。

3. 重启 AstrBot，在 WebUI → 插件 里找到「闲鱼自动发货（AutoDeliver）」，点开配置填写（见下）。

> ⚠️ **`/` 必须出现在 AstrBot 的「唤醒前缀」里，否则指令永远不会触发。**
> AstrBot 的指令过滤器要求消息先成为「唤醒命令」。如果唤醒前缀被改成了别的词
> （比如某些人格配置把 `wake_prefix` 改成 `["浅野堇"]`），`/闲鱼扫码` 会被当成普通聊天
> 丢给大模型，插件一条都收不到。
>
> - 在 WebUI（配置 → 其他配置 → 唤醒前缀）里把它改回 `["/"]`，或
>   `["浅野堇", "/"]` 这样保留人格唤醒词、同时把 `/` 加回去。
> - 插件自带兜底：`/` 开头但没有任何指令认领的消息会被直接吞掉（不会触发大模型），
>   只有 `/闲鱼xxx` 这种明显是手误的才会回一句「没有这个指令」。

---

## 二、配置

| 配置项 | 说明 |
|---|---|
| `base_url` | AutoDeliver 地址，如 `http://127.0.0.1:8080`；AstrBot 在别的机器上就填内网/公网地址 |
| `api_token` | 填 AutoDeliver `config.toml` 的 `[auth] session_token`，可**免登录**直接用 |
| `admin_username` / `admin_password` | 不填 `api_token` 时使用；填了之后直接发 `/闲鱼登录` 即可 |
| `admin_qq` | **管理员 QQ 号列表**。敏感指令（登录/扫码/账号/商品/发货…）只允许名单内的人执行；留空时退化为「仅私聊可用」 |
| `search_access_mode` | 搜索访问模式：`all`（任何人）/ `blacklist`（黑名单外）/ `whitelist`（仅白名单） |
| `search_blacklist` / `search_whitelist` | 对应模式下的 QQ 号列表 |
| `enable_llm_summary` | 关掉就用内置模板摘要，不消耗大模型额度 |
| `search_top_n` / `search_cooldown` | 展示条数、单人搜索冷却（防闲鱼限流） |
| `qr_poll_timeout` | 扫码等待时间，默认 180 秒 |

### 权限模型

两套规则彼此独立：

| 类别 | 指令 | 准入 |
|---|---|---|
| **管理员指令** | 登录、状态、订单、账号、扫码、Cookie、校验、商品、同步、删除、发货、推送、权限 | 只认 `admin_qq`；**管理员在群里也能用**（带密码/Cookie 的仍强制私聊） |
| **公开指令** | `/闲鱼搜索`、`/闲鱼`（菜单）、`/闲鱼 列表`（指令详解） | 菜单与清单**任何人**都能看；搜索由 `search_access_mode` 决定，管理员永远放行 |

`search_access_mode` 三种模式：

| 值 | 行为 |
|---|---|
| `all` | 任何人都能搜索（默认） |
| `blacklist` | `search_blacklist` 里的人不能搜，其他人可以 |
| `whitelist` | 只有 `search_whitelist` 里的人能搜 |

也接受中文写法：`任何人` / `黑名单` / `白名单`；填错会退回 `all`。
`/闲鱼权限` 可以随时自查当前生效的配置（管理员指令）。

Token 保存在 `data/plugin_data/autodeliver_astrbot/auth.json`（权限 600），
重启 AstrBot 后自动复用；会话过期（401）时插件会用配置的账号密码自动重登一次。

---

## 三、指令

| 指令 | 作用 | 权限 |
|---|---|---|
| `/闲鱼` | 帮助菜单（别名 `xy`、`闲鱼帮助`、`闲鱼菜单`） | 公开 |
| `/闲鱼 列表` | **全部指令 + 功能说明**：每条带别名、权限、使用限制，并回显你自己能不能用（别名 `闲鱼列表`、`闲鱼指令`、`闲鱼清单`） | 公开 |
| `/闲鱼搜索 <关键词> ...` | 全网搜索 + 大模型行情总结 | 受 `search_access_mode` 控制 |
| `/闲鱼登录 <用户名> <密码>` | 登录 AutoDeliver，拿 token 并落盘（**仅私聊**，不带参数则用配置里的账号密码） | 管理员 |
| `/闲鱼状态` | 平台登录态 + 账号概览 + 订单统计 | 管理员 |
| `/闲鱼账号` | 已绑定的闲鱼账号列表（状态、模式、seller_id、最近校验） | 管理员 |
| `/闲鱼扫码` | 生成二维码发到聊天里，用闲鱼 App 扫码；插件后台轮询，成功后主动播报 | 管理员 |
| `/闲鱼cookie <Cookie>` | Cookie 登录/更新账号（**仅私聊**，建议登录后撤回消息） | 管理员 |
| `/闲鱼校验 <账号ID>` | 调远端验签，返回 `valid` 与说明 | 管理员 |
| `/闲鱼删除 <账号ID>` | 先回显账号信息，60 秒内再发 `/闲鱼删除 <账号ID> 确认` 才真正删除 | 管理员 |
| `/闲鱼商品 <账号ID>` | 该账号的闲鱼商品（价格从「分」转成元，标注已绑卡密） | 管理员 |
| `/闲鱼同步 <账号ID>` | 从闲鱼远端拉取商品并入库 | 管理员 |
| `/闲鱼发货 [账号ID]` | 立即触发一次自动发货巡检（几十秒） | 管理员 |
| `/闲鱼订单` | 订单统计（累计/今日/待发货/已发货/失败/成交额） | 管理员 |
| `/闲鱼推送 绑定\|取消\|查看\|清空` | 把摘要**额外**推送到指定会话（群指令触发、摘要回到自己 QQ 的场景） | 管理员 |
| `/闲鱼权限` | 查看当前生效的权限配置（管理员名单、搜索模式） | 管理员 |

> `/闲鱼` 是精简菜单（一行一条，适合群里刷），`/闲鱼 列表` 是逐条详解（功能 + 别名 + 权限 + 限制）。
> 两者都由 `main.py` 里的 `COMMAND_SPECS` 生成：新增指令只改这张表，菜单、清单和测试就不会各说各话。

### 搜索用法

```
/闲鱼搜索 switch
/闲鱼搜索 塞尔达 王国之泪
/闲鱼搜索 switch --max 300
/闲鱼搜索 switch --min 200 --max 500 --sort 价格升序 --top 3
```

支持的参数：`--min`、`--max`（元）、`--sort`（`价格升序` / `价格降序` / `性价比` / `默认`）、
`--top`（展示条数）、`--account <ID>`（用哪个闲鱼账号的 Cookie 去搜）。

---

## 四、搜索 → 总结 → 回传的完整链路

```
/闲鱼搜索 switch
   │
   ├─ POST /api/xianyu/search {keyword:"switch"}          ← 后端调闲鱼 MTOP 实时搜索
   │     返回 stats(中位价/P25/P75/定价建议) + 30 条 items(value_score/value_grade)
   │
   ├─ 先回一条「🔍 正在搜…」
   ├─ 调 AstrBot 当前会话的大模型（enable_llm_summary=true 时）
   │     输入：紧凑的结构化行情（最多 12 条样本）+ 系统提示词
   │     输出：300 字以内的中文行情小结（强行去掉 Markdown 记号，适配 QQ）
   │     失败/超时/无 provider → 自动降级为内置模板摘要
   │
   ├─ 再回一条「价格最优 N 个 + 性价比榜」带跳转链接
   └─ 若有 /闲鱼推送 绑定的会话，把摘要主动推过去
```

大模型不可用也不会哑火：`summary.py` 里的 `build_fallback_summary()` 用中位价、
四分位区间和性价比评级生成一段确定性摘要，保证任何情况下都有结果回。

---

## 五、安全设计

1. **管理员隔离**：登录 / 扫码 / 账号 / 商品 / 发货等敏感指令只认 `admin_qq` 里的 QQ；
   未配置名单时退化为「仅私聊」，避免群里被陌生人删号或触发发货。
2. **搜索分级**：`search_access_mode` 支持 `all` / `blacklist` / `whitelist`，
   让行情查询可以开给群友，而业务操作始终捏在自己手里；管理员始终能搜。
3. **敏感操作**：`/闲鱼登录` 带密码、`/闲鱼cookie` 一律**拒绝在群里执行**。
4. **删除二次确认**：`/闲鱼删除 <ID>` 只是「预演」，60 秒内补一句「确认」才落库。
5. **凭据落盘**：token 存 `data/plugin_data/.../auth.json`，`chmod 600`；Cookie 不落盘（只进 AutoDeliver 数据库）。
6. **限流**：搜索默认 3 秒冷却，避免 QQ 群刷指令把闲鱼接口打到风控。

> ⚠️ AutoDeliver 默认监听 `127.0.0.1:8080`。如果 AstrBot 不在同一台机器上，
> 请通过内网 / SSH 隧道 / 反代暴露，并务必改掉默认的 `admin123` 与静态 `session_token`。

---

## 六、自测

不需要装 AstrBot 就能跑，分成两组：

```bash
pip install httpx

# 1) 权限矩阵（纯离线，不连后端、不改数据）
python tests/test_permissions.py

# 2) 指令全链路（会真的请求后端与闲鱼，只读）
python tests/test_plugin_local.py
AUTODELIVER_BASE_URL=http://192.168.1.161:18080 \
AUTODELIVER_TOKEN=<config.toml 的 session_token> python tests/test_plugin_local.py

# 3) 只测 HTTP 客户端
python tests/smoke_api.py            # 默认搜 switch
AD_BASE=http://127.0.0.1:8080 AD_USER=admin AD_PASS=... python tests/smoke_api.py

# 4) 斜杠守卫（离线，验证 /未知指令 不会落到大模型）
python tests/test_slash_guard.py

# 5) 指令清单（离线，验证 /闲鱼 与 /闲鱼 列表 的分支和覆盖度）
python tests/test_command_list.py
```

`test_permissions.py` 覆盖：管理员/非管理员在私聊与群聊的判定、搜索三种模式、
管理员始终可搜、`admin_qq` 逗号与 UMO 写法、模式填错时的回退。

`test_command_list.py` 会把 `COMMAND_SPECS` 和真实注册的 handler 对一遍：
每条指令的用法是否出现在菜单、别名是否真的挂了装饰器，防止改了指令忘了改文案。

`test_plugin_local.py` 会依次跑通：登录 → 账号列表 → 订单统计 → 商品列表 → 搜索 → 摘要/明细。

---

## 七、排错

| 现象 | 排查 |
|---|---|
| 发 `/闲鱼xxx` 没反应，反而是大模型在回你 | **唤醒前缀里没有 `/`**。AstrBot 指挥令先成为唤醒命令，`wake_prefix` 被改成 `["浅野堇"]` 时 `/闲鱼x` 只会走大模型 |
| `连不上 AutoDeliver` | `base_url` 写错、后端没起、端口不对；先 `curl <base_url>/` 看有没有 HTML |
| `尚未登录 AutoDeliver` | 发 `/闲鱼登录`，或在配置里填 `api_token` / 账号密码 |
| 扫码一直不动 | 插件目录装 `qrcode`；或看 AstrBot 日志里 `qrcode_poll` 的报错 |
| 搜索报 `mtop` 相关错误 | 闲鱼侧风控/未登录 Cookie，先在后台 `/闲鱼校验 <ID>` 看账号是否 valid |
| 摘要像模板不像 AI | 会话没绑大模型 provider，或 `enable_llm_summary=false`，或超时（调大 `summary_timeout`） |
| 插件加载报相对导入错误 | 确认整个目录（含 `api.py` / `summary.py`）一起拷过去了，不要只拷 `main.py` |

---

## 八、目录

```
autodeliver_astrbot/
├── main.py              # AstrBot 入口：指令、权限、二维码轮询、主动推送
├── api.py               # AutoDeliver REST 客户端（httpx，401 自动重登）
├── summary.py           # 参数解析 + QQ 文案 + 大模型 Prompt（纯函数，可单测）
├── metadata.yaml        # AstrBot 插件元数据
├── _conf_schema.json    # 插件配置面板
├── requirements.txt
└── tests/                # 离线自测：权限 / 斜杠守卫 / 指令清单 + 端到端冒烟
    ├── test_permissions.py
    ├── test_slash_guard.py
    ├── test_command_list.py
    ├── test_plugin_local.py
    └── smoke_api.py
```
