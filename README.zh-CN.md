# KB Steward —— 把知识库治理放进团队群聊

[English](README.md) · **简体中文**

> Flip the Switch 黑客松 · Track 01（最佳业务流程）· 基于 [Switch](https://github.com/sandbox-quantum/switch) 构建

**影响分析和多文件修订交给 AI，审批权和审计留给人。**

几乎每个团队的知识库都在悄悄过时。新人在群里提问，机器人（或者热心同事）照着旧文档作答，懂行的人补一句"这个早就改了，现在是 ¥120"——然后这句更正就淹没在聊天记录里。文档没人改，下周同样的错答案又会出现。

KB Steward 把团队频道变成知识库的"受控变更流程"：

| 治理能力 | 在群里是什么样子 |
| --- | --- |
| **过期识别** | 每条回答都标注出处文档、章节、**负责人**和**复核日期**；超过 `KB_STALE_DAYS` 天没复核的文档打上 ⚠️。发送 `@kb-curator 复核报告`（或运行 `scripts/stale-report.py`）即可列出超期文档并 @ 对应负责人。 |
| **冲突移交** | 两份文档说法不一致时，回答者不替你挑一个，而是把两个说法都摆出来，并移交给 curator，由它去问负责人哪个才对。 |
| **影响分析** | 一句更正会变成独立 git 分支上的提案 `KB-n`，附带**影响分析**：改了哪些文件、动了哪些章节，以及一句话风险提示（例如"数值类制度变更（¥100 → ¥120）"），外加新回答的预览。 |
| **负责人把关** | 只有文档负责人能 `approve KB-n` / `reject KB-n`（也可以写"批准 KB-n"/"驳回 KB-n"）。其他人尝试审批会被拒绝并说明原因，任何内容都进不了 `main`。 |
| **git 审计链** | 批准后以 merge commit 合并，带 `Approved-by:` / `Requested-by:` / `Impact:` trailer，随时可以 `git revert`。 |
| **频道决策日志** | 每次决策都追加到 Switch 频道文档 **"KB decision log"**；知识库答不上的问题记入 **"KB knowledge gaps"**。 |
| **中英双语** | 中文提问中文作答，英文提问英文作答，出处、负责人、过期标记一样不少；中英文的更正都能识别。 |

完整闭环：

1. **`@kb-answerer`** 只依据 git 管理的知识库回答，附出处、负责人和复核日期，用提问者的语言作答。遇到过期或互相矛盾的文档会明确标出；遇到冲突或知识缺口不硬答，而是移交给 curator。
2. **`@kb-curator`** 监听整个频道。只要有人对回答提出更正——不需要 @ 它，中文英文都行——它就在仓库上跑一轮工具调用（`search_kb` / `grep` / `read_file` / `write_file`），找出**所有**写着这条错误信息的文档（比如制度原文和照抄了旧数字的 FAQ），在自己的分支上提交修改，计算影响范围，**用分支上的新内容预演一遍回答**，然后发出提案 `KB-n`。
3. **只有文档负责人能批准。** 负责人批准后，分支合并并写入 git trailer，索引重建，决策记入频道的 **"KB decision log"**，原问题交回 answerer，在同一个帖子里给出正确答案。

> 仓库中所有公司数据均为虚构的"北风示例公司（Northwind Example Co.）"**示例数据**。
> `kb-seed/` 是英文知识库，`kb-seed/zh/` 是中文知识库（两套独立的示例制度，例如餐补 ¥100/天 与 $50/天）。

## 演示

📺 视频：[KB Steward demo v2（2:13，英文主线 + 中文闭环）](https://youtu.be/HDlKPY9HajU) · [v1（1:58）](https://youtu.be/8xKwF6o_T2U)，分镜见 [DEMO.md](DEMO.md)。
📝 博客：[英文版](https://breeze-lyf.hashnode.dev/kb-steward-teaching-two-agents-to-fix-the-wiki-from-team-chat-on-switch) · [中文版](https://breeze-lyf.hashnode.dev/kb-steward-agent-switch)

2026-11-04 在同一套环境上的中文实跑记录（`kb-seed/zh/`，略有删节）：

```
newhire       @kb-answerer 出差餐补每天多少钱？
kb-answerer   出差餐补每天 ¥100，按出差自然日计算。[1][2]
              [1] zh/finance/travel-faq.md § 出差有餐补吗？ · 负责人 finance · 复核于 2025-02-18 ⚠️ 已 624 天未复核
              [2] zh/finance/travel-expense-policy.md § 差旅餐补 · 负责人 finance · 复核于 2024-04-08 ⚠️ 已 940 天未复核
finance-lead  这个过时了，从 11 月起餐补已经是每天 ¥120，超过 ¥300 才需要开发票。
kb-curator    📝 KB-2 · 更新差旅餐补与发票门槛 — kb/KB-2 (2 files changed)
              影响分析
              - 2 个文件：zh/finance/travel-expense-policy.md, zh/finance/travel-faq.md
              - 涉及章节：§ 差旅餐补; § 发票要求; § 出差有餐补吗？
              - 风险: 数值类制度变更（¥100 → ¥120, ¥200 → ¥300）…；2 篇文档需保持一致
              🔎 预览：从 11 月起，因公出差餐补为每天 ¥120 …
support-lead  批准 KB-2
kb-curator    ⛔ @support-lead 无权批准 KB-2：它修改的是 finance 的文档，只有 @finance-lead 可以审批。未合并任何内容。
finance-lead  批准 KB-2
kb-curator    ✅ KB-2 已由 @finance-lead 批准，合并为 d575676。索引已重建 …
kb-answerer   从 11 月起，出差餐补为每天 ¥120 … 复核于 2026-11-04 · 置信度: 高

newhire       @kb-answerer 客户申请退款的期限是多久？
kb-answerer   资料中的退款申请期限存在冲突：退款政策为 30 天[1]，客服话术写 7 天[2] …
kb-curator    🧭 两份文档说法不一致，我不会随便选一个。@support-lead：哪个才是对的？
support-lead  30 天是对的，话术写错了。
kb-curator    📝 KB-3 · 更正客服退款申请期限 … 风险: 数值类制度变更（7 天 → 30 天）
support-lead  批准 KB-3   →  ✅ 合并；kb-answerer：客户可在购买后 30 天内申请全额退款。

newhire       @kb-answerer 公司的停车补贴怎么申请？
kb-curator    🕳️ 已记录知识缺口：公司的停车补贴怎么申请？（频道文档 KB knowledge gaps，当前 1 条未解决）
```

合并后的 git 历史：

```
874576a Merge KB-3: 更正客服退款申请期限
Approved-by: support-lead in Switch room kb-ops
Requested-by: support-lead
Impact: 数值类制度变更（7 天 → 30 天），所有引用该数值的回答都会随之变化
```

英文完整实跑记录见 [README.md](README.md#demo)。

## Switch 在这里做了什么（项目核心）

是 Switch 让两个 Python 进程变成了频道里的"同事"。代码里没有任何直接调用 Mattermost/Slack 的地方——所有读写都走 Switch 的 agent 协议，所以同一套 agent 不改代码就能跑在 Slack、Teams、Discord、Telegram 或 Mattermost 上。

| 用途 | Switch 能力 | 代码位置 |
| --- | --- | --- |
| 两个 agent，各有身份和头像 | 用注册 token `POST /agents` → `{id, api_key}`（持久化，重启复用） | `switch_proto.py` `SwitchAgent.register` |
| 人和 agent 共用一个频道 | op `create_room`（公开频道、成员、**频道说明**，中英双语） | `bootstrap.py` |
| 实时事件（所有消息，不只是 @） | WebSocket `/agents/{id}/connection/ws?filter=all`，带游标的心跳、断档处理、`connect_to_room` 认领 | `switch_proto.py` `run` |
| answerer 只在被 @ 时响应，curator 旁听更正 | 事件里的 `addressed` 标记 + `filter=all` | `answerer.py`, `curator.py` |
| 帖子内回复 | op `post_message(body, thread_id)` | `SwitchAgent.reply` |
| 读取更正所在的上下文 | op `read_context` | `curator.thread_text` |
| agent 之间移交（冲突/缺口 → curator，合并后 → 复查） | op `send_targeted_message(target_names=[...])` | `answerer.py`, `curator.decide` |
| 共享、持久的决策记录 | op `create_room_document` / `update_room_document` → "KB decision log" | `curator.log_decision` |
| 知识缺口视图 | 同上 → 频道文档 "KB knowledge gaps" | `curator.log_gap` |
| 定时复核提醒由 curator 发出 | `POST /agents/{id}/message`（无需长连接） | `scripts/stale-report.py --post` |
| 模型思考时显示"正在输入…" | `POST /agents/{id}/typing` | `SwitchAgent.typing` |
| 频道里任何人都能 @ agent | 网关管理接口 `PUT /gateway/agents/{id}/addressing-policy` | `bootstrap.open_addressing` |
| 审批人身份 | 每个事件的 `sender_name` = 聊天用户名 | `common.sender_handle` |

频道用的是 Switch 独立部署栈自带的 **Mattermost**（完全自托管，一条命令就能复现）。换成 Slack/Discord/Telegram/Teams，只需在 Switch 网关接好对应桥接、把两个 agent 拉进频道，agent 代码不用动。

## 架构

```
             Mattermost / Slack / Discord / Telegram 频道 "kb-ops"
                 ▲   成员：newhire, finance-lead, support-lead
                 │
        ┌────────┴─────────  Switch（core + gateway）  ──────────────┐
        │ 频道 · 寻址 · 帖子 · 定向消息 · 频道文档                    │
        └──────▲─────────────────────────────▲──────────────────────┘
          ws + ops                       ws + ops
        ┌──────┴───────┐    移交     ┌───────┴────────┐
        │ kb-answerer  │◀──────────▶│  kb-curator     │
        │ BM25 + LLM   │    复查     │ LLM 工具循环     │
        └──────┬───────┘             └───────┬────────┘
               │ 读 HEAD                     │ worktree kb/KB-n → merge --no-ff
               └──────────►  git 仓库 /data/kb  ◄────────┘   （可选推送到远端）
```

* `agents/kb_core.py`：按章节建 BM25 索引，读取 front matter（`owner`、`last_reviewed`、`lang`）；中文按字二元组切词；`lang_of` 用中日韩字符占比判断提问语言，检索优先同语言文档，回答和格式化（负责人/复核于/置信度）也随之切换；`HEAD` 一动就重建索引。
* `agents/curator.py`：中英文更正识别（规则预筛 + 模型分类），每个提案一个 git worktree，工具调用式编辑（只能写已有的 `.md`，且只改当前语言的知识库），影响分析，分支预览，负责人把关，合并，决策日志和知识缺口文档。
* LLM：任何 OpenAI 兼容接口（`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`）。

## 自己跑一遍

需要：带 compose 的 Docker，以及一个 OpenAI 兼容模型的 API key。

```bash
git clone https://github.com/breeze-lyf/kb-steward && cd kb-steward
scripts/up-switch.sh switch-stack            # Switch 0.31.0 + gateway + 自带 Mattermost
scripts/make-agents-env.sh switch-stack/.env # 生成 agents.env（chmod 600），不打印任何密钥
$EDITOR agents.env                           # 填 LLM_API_KEY（以及 LLM_BASE_URL/LLM_MODEL），可选 KB_LANG=en|zh|both
docker compose up -d --build                 # bootstrap：灌入知识库、演示账号、注册 agent、建频道
```

打开 Mattermost（`http://127.0.0.1:8065`），用演示账号登录（密码在 `kbdata` 卷的 `/data/state/demo-users.json`），进入 **kb-ops** 频道。

不开浏览器也能彩排——中文闭环：

```bash
say() { docker compose exec kb-answerer python mm_say.py "$@"; }
scripts/reset-demo.sh                 # 重新灌入知识库（按 KB_LANG，默认 both），清空提案和频道
say newhire "@kb-answerer 出差餐补每天多少钱？"
say finance-lead "这个过时了，从 11 月起餐补已经是每天 ¥120，超过 ¥300 才需要开发票。" --reply-last
say support-lead "批准 KB-1" --reply-last        # ⛔ 非负责人被拒
say finance-lead "批准 KB-1" --reply-last        # ✅ 合并，answerer 重新回答 ¥120
say newhire "@kb-answerer 客户申请退款的期限是多久？"   # 冲突移交
say support-lead "30 天是对的，话术写错了。" --reply-last
say support-lead "批准 KB-2" --reply-last
say newhire "@kb-answerer 公司的停车补贴怎么申请？"     # 知识缺口 → "KB knowledge gaps"
say finance-lead "@kb-curator 复核报告"                 # 超期复核提醒
say --dump 10
```

英文闭环：

```bash
say newhire "@kb-answerer what's the meal allowance when travelling?"
say finance-lead "That's outdated: since July it's \$65/day, and receipts are needed for anything over \$25." --reply-last
say support-lead "approve KB-1" --reply-last
say finance-lead "approve KB-1" --reply-last
```

（同一次重置内提案编号会继续累加；`scripts/reset-demo.sh zh` 只加载中文知识库。）

其他：
* 命令行/定时任务做复核巡检：`docker compose exec kb-curator python /app/scripts/stale-report.py [--lang zh] [--json] [--post]`。
* 查看 curator 做了什么：`docker compose exec kb-curator git -C /data/kb log --format='%h %s%n%(trailers)'`。
* 离线测试：`python -m unittest discover -s tests`。
* 配置（`agents.env`）：`APPROVERS=finance:finance-lead;support:support-lead;…` 把文档 `owner` 映射到可审批的聊天用户；`KB_STALE_DAYS`（默认 365）；`KB_LANG`；`KB_PUSH_REMOTE` 可把合并结果推到真实远端仓库。
* 国内网络：`.env` 里的构建参数 `PYTHON_IMAGE`、`PIP_INDEX_URL`、`APT_MIRROR` 可以换成镜像源。

## 安全边界

* agent 只能修改知识库 worktree 里已有的 Markdown（且只改当前语言的那套知识库）；没有负责人批准，什么都进不了 `main`。
* 每次变更都是普通的 git merge commit，写明谁提的、谁批的、影响是什么，可以 `git revert`。
* 密钥只放在 `agents.env` 和 Switch 的 `.env`（都在 .gitignore 里，权限 600），从不写进日志。
* 模型服务出错时，agent 会在帖子里说明，不做任何修改。

## 许可证

MIT，见 [LICENSE](LICENSE)。Switch 版权归其作者所有，遵循其自身许可证。
