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
   插件管理 → 安装插件 → 填本仓库地址 `https://github.com/Makima04/astrbot_plugin_xianyu_autodeliver`，
   AstrBot 会自动 clone 并安装 `requirements.txt`。

   **B. 服务器上手动 clone**

   ```bash
   cd /path/to/AstrBot/data/plugins
   git clone https://github.com/Makima04/astrbot_plugin_xianyu_autodeliver.git
   pip install -r astrbot_plugin_xianyu_autodeliver/requirements.txt
   ```

   **C. Docker 部署的 AstrBot**（容器内 pip 装依赖）

   ```bash
   HOST_DATA=/home/orangepi/astrbot/data          # 宿主机上挂到容器 /AstrBot/data 的目录
   git clone https://github.com/Makima04/astrbot_plugin_xianyu_autodeliver.git \
             $HOST_DATA/plugins/astrbot_plugin_xianyu_autodeliver
   docker exec astrbot pip install -r /AstrBot/data/plugins/astrbot_plugin_xianyu_autodeliver/requirements.txt
   docker restart astrbot
   ```

   开发期想「改完立刻生效」可以用软链：`ln -s $PWD <AstrBot>/data/plugins/astrbot_plugin_xianyu_autodeliver`，
   改代码后在 WebUI 插件卡片点「重载插件」。

3. 重启 AstrBot，在 WebUI → 插件 里找到「闲鱼自动发货（AutoDeliver）」，点开配置填写（见下）。

---

## 二、配置

| 配置项 | 说明 |
|---|---|
| `base_url` | AutoDeliver 地址，如 `http://127.0.0.1:8080`；AstrBot 在别的机器上就填内网/公网地址 |
| `api_token` | 填 AutoDeliver `config.toml` 的 `[auth] session_token`，可**免登录**直接用 |
| `admin_username` / `admin_password` | 不填 `api_token` 时使用；填了之后直接发 `/闲鱼登录` 即可 |
| `admin_qq` | 允许使用指令的 QQ 白名单。**留空时只能在私聊使用**，避免群里被人删号 |
| `enable_llm_summary` | 关掉就用内置模板摘要，不消耗大模型额度 |
| `search_top_n` / `search_cooldown` | 展示条数、单人搜索冷却（防闲鱼限流） |
| `qr_poll_timeout` | 扫码等待时间，默认 180 秒 |

Token 保存在 `data/plugin_data/astrbot_plugin_xianyu_autodeliver/auth.json`（权限 600），
重启 AstrBot 后自动复用；会话过期（401）时插件会用配置的账号密码自动重登一次。

---

## 三、指令

| 指令 | 作用 |
|---|---|
| `/闲鱼` | 帮助菜单（别名 `xy`、`闲鱼帮助`） |
| `/闲鱼登录 <用户名> <密码>` | 登录 AutoDeliver，拿 token 并落盘（**仅私聊**，不带参数则用配置里的账号密码） |
| `/闲鱼状态` | 平台登录态 + 账号概览 + 订单统计 |
| `/闲鱼账号` | 已绑定的闲鱼账号列表（状态、模式、seller_id、最近校验） |
| `/闲鱼扫码` | 生成二维码发到聊天里，用闲鱼 App 扫码；插件后台轮询，成功后主动播报 |
| `/闲鱼cookie <Cookie>` | Cookie 登录/更新账号（**仅私聊**，建议登录后撤回消息） |
| `/闲鱼校验 <账号ID>` | 调远端验签，返回 `valid` 与说明 |
| `/闲鱼删除 <账号ID>` | 先回显账号信息，60 秒内再发 `/闲鱼删除 <账号ID> 确认` 才真正删除 |
| `/闲鱼商品 <账号ID>` | 该账号的闲鱼商品（价格从「分」转成元，标注已绑卡密） |
| `/闲鱼同步 <账号ID>` | 从闲鱼远端拉取商品并入库 |
| `/闲鱼搜索 <关键词> ...` | 全网搜索 + 大模型行情总结 |
| `/闲鱼发货 [账号ID]` | 立即触发一次自动发货巡检（几十秒） |
| `/闲鱼订单` | 订单统计（累计/今日/待发货/已发货/失败/成交额） |
| `/闲鱼推送 绑定\|取消\|查看\|清空` | 把摘要**额外**推送到指定会话（群指令触发、摘要回到自己 QQ 的场景） |

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

1. **权限**：`admin_qq` 为空时指令只在私聊生效；填了白名单只有名单内的人能用。
2. **敏感操作**：`/闲鱼登录` 带密码、`/闲鱼cookie` 一律**拒绝在群里执行**。
3. **删除二次确认**：`/闲鱼删除 <ID>` 只是「预演」，60 秒内补一句「确认」才落库。
4. **凭据落盘**：token 存 `data/plugin_data/.../auth.json`，`chmod 600`；Cookie 不落盘（只进 AutoDeliver 数据库）。
5. **限流**：搜索默认 3 秒冷却，避免 QQ 群刷指令把闲鱼接口打到风控。

> ⚠️ AutoDeliver 默认监听 `127.0.0.1:8080`。如果 AstrBot 不在同一台机器上，
> 请通过内网 / SSH 隧道 / 反代暴露，并务必改掉默认的 `admin123` 与静态 `session_token`。

---

## 六、自测

不需要装 AstrBot 就能验证「插件 ↔ 后端」这条线（只读，不会改任何业务数据）：

```bash
pip install httpx
python tests/smoke_api.py            # 默认搜 switch
python tests/smoke_api.py 塞尔达
AD_BASE=http://127.0.0.1:8080 AD_USER=admin AD_PASS=admin123 python tests/smoke_api.py
```

会依次跑通：登录 → 账号列表 → 订单统计 → 商品列表 → 搜索 → 摘要/明细/Prompt 预览。

---

## 七、排错

| 现象 | 排查 |
|---|---|
| `连不上 AutoDeliver` | `base_url` 写错、后端没起、端口不对；先 `curl <base_url>/` 看有没有 HTML |
| `尚未登录 AutoDeliver` | 发 `/闲鱼登录`，或在配置里填 `api_token` / 账号密码 |
| 扫码一直不动 | 插件目录装 `qrcode`；或看 AstrBot 日志里 `qrcode_poll` 的报错 |
| 搜索报 `mtop` 相关错误 | 闲鱼侧风控/未登录 Cookie，先在后台 `/闲鱼校验 <ID>` 看账号是否 valid |
| 摘要像模板不像 AI | 会话没绑大模型 provider，或 `enable_llm_summary=false`，或超时（调大 `summary_timeout`） |
| 插件加载报相对导入错误 | 确认整个目录（含 `api.py` / `summary.py`）一起拷过去了，不要只拷 `main.py` |

---

## 八、目录

```
astrbot_plugin_xianyu_autodeliver/
├── main.py              # AstrBot 入口：指令、权限、二维码轮询、主动推送
├── api.py               # AutoDeliver REST 客户端（httpx，401 自动重登）
├── summary.py           # 参数解析 + QQ 文案 + 大模型 Prompt（纯函数，可单测）
├── metadata.yaml        # AstrBot 插件元数据
├── _conf_schema.json    # 插件配置面板
├── requirements.txt
└── tests/smoke_api.py   # 脱离 AstrBot 的端到端冒烟
```
