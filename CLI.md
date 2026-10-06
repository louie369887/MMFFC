MMFFC CLI 设计规范（Unix 风格，非 MCP）



1\. 设计原则



参考 Unix 哲学：



1\. 单一职责：每个子命令只做一件事，做好一件事。

2\. 文本流优先：默认输出人类可读文本；--json / --ndjson 输出机器可读文本；所有命令支持 stdin / stdout。

3\. 管道组合：输出可直接被 jq、grep、xargs、parallel 消费。

4\. 非交互默认：默认不弹交互提示；需要确认的高风险操作在非 TTY 下必须显式 --yes。

5\. 退出码明确：成功为 0，不同错误类型有稳定退出码。

6\. 幂等与安全：写入前校验、备份、原子替换；--dry-run 输出操作计划。

7\. 配置优先级明确：命令行 > 环境变量 > 项目配置 > 用户配置 > 默认值。

8\. MCP 边界清晰：CLI 不调用 LLM，不暴露 MCP Tools；AI 生成内容通过 stdin 交给 CLI 校验和落盘。



2\. 全局约定



2.1 命令结构



统一入口：mmffc <noun> <verb> \[options] \[args]，类似 git remote add。



```

mmffc mod search

mmffc mod install

mmffc config apply

mmffc structure compile

mmffc world compile

mmffc rcon exec

```



2.2 全局选项



选项 说明

\--help / -h 帮助

\--version / -V 版本

\--config PATH 指定配置文件

\--json 输出 JSON

\--ndjson 输出 NDJSON（每行一个 JSON 对象）

\--format table\\|json\\|ndjson\\|csv\\|tsv\\|raw 输出格式

\--no-color 禁用颜色，等价于 NO\_COLOR=1

\--quiet / -q 仅输出错误

\--verbose / -v 增加 stderr 日志

\--dry-run 只打印将执行的操作，不落盘

\--yes / -y 跳过确认，非 TTY 下高风险操作必需

\--cwd PATH 指定工作目录

\--log-level debug\\|info\\|warn\\|error 日志级别



2.3 流与退出码



· stdout：正常结果。

· stderr：日志、进度、错误。

· stdin：支持 - 表示从标准输入读取。

· 退出码：



码 含义

0 成功

1 通用错误

2 用法错误 / 参数错误

3 Schema 校验失败

4 网络错误（API、下载）

5 文件冲突 / 已存在

6 权限错误

7 依赖未满足

8 存档完整性风险

130 SIGINT 中断



2.4 环境变量



变量 说明

MMFFC\_CONFIG 配置文件路径

MMFFC\_HOME 数据目录，默认 \~/.mmffc

MMFFC\_MODS\_DIR 默认 mods 目录

MMFFC\_CACHE\_DIR 缓存目录

MODRINTH\_TOKEN Modrinth Personal Access Token

CURSEFORGE\_API\_KEY CurseForge API Key

NO\_COLOR 禁用颜色

MMFFC\_RCON\_PASSWORD RCON 密码



3\. 命令树



3.1 mmffc mod — 模组管理



```bash

mmffc mod search <query> \[--loader fabric|forge|quilt] \[--mc-version 1.20.1] \[--limit 20] \[--json|--ndjson]

mmffc mod info <project\_id|slug> \[--json]

mmffc mod install <project\_id|slug> \[--version VERSION] \[--resolve-deps] \[--dry-run] \[--yes] \[--mods-dir DIR]

mmffc mod install --stdin \[--resolve-deps] \[--yes]   # 从 stdin 读 project\_id，每行一个

mmffc mod list \[--json] \[--dir DIR]

mmffc mod scan \[--dir DIR] \[--json]

mmffc mod deps \[--tree] \[--missing] \[--json]

mmffc mod remove <project\_id|slug> \[--yes]

mmffc mod update \[--all] \[--dry-run]

```



示例：



```bash

\# 搜索并安装下载量最高的 fabric 1.20.1 sodium 模组

mmffc mod search sodium --loader fabric --mc-version 1.20.1 --ndjson \\

&#x20; | jq -r 'select(.downloads > 5000000) | .project\_id' \\

&#x20; | head -n 1 \\

&#x20; | xargs -I{} mmffc mod install {} --resolve-deps --yes

```



3.2 mmffc config — 配置校验与写入



CLI 不生成 AI 内容，只接收 stdin 或文件，做 Schema 校验、语义校验、原子写入。



```bash

mmffc config validate <fancymenu|ftbquests> \[FILE|-] \[--schema PATH]

mmffc config apply <fancymenu|ftbquests> \[FILE|-] --target PATH \[--backup] \[--dry-run] \[--yes]

mmffc config get <fancymenu|ftbquests> <path> \[--target PATH]

mmffc config set <fancymenu|ftbquests> <path> <value> \[--target PATH] \[--yes]

mmffc config diff <fancymenu|ftbquests> \[FILE|-] \[--target PATH]

```



示例：



```bash

\# AI Agent 生成 FTB Quests JSON5 到 stdout，CLI 校验并写入

ai-agent generate-ftb --prompt "..." \\

&#x20; | mmffc config apply ftbquests - --target ./config/ftbquests/quests --yes

```



3.3 mmffc structure — 结构文件生成



从 stdin 读体素 NDJSON，输出 .nbt 或 .litematic。



体素输入格式（NDJSON，每行一个方块）：



```json

{"x":0,"y":0,"z":0,"block":"minecraft:oak\_planks","properties":{}}

```



命令：



```bash

mmffc structure compile --format nbt|litematic -o OUT \[--input -] \[--origin X,Y,Z] \[--name NAME]

mmffc structure inspect <FILE> \[--json]

mmffc structure convert <IN> <OUT> \[--format nbt|litematic]

```



示例：



```bash

cat voxels.ndjson \\

&#x20; | mmffc structure compile --format litematic --origin 0,0,0 -o castle.litematic

```



3.4 mmffc world — MCA 编译与世界生成



非 MCP 部分：接收规则文件或区块数据，编译为 .mca 与 level.dat。自然语言转规则由 MCP 负责，CLI 只消费规则。



```bash

mmffc world init <DIR> --mc-version 1.20.1 --name "Test" \[--seed N] \[--dry-run]

mmffc world compile --input - --output <DIR> \[--region X,Z] \[--heightmap auto] \[--dry-run] \[--yes]

mmffc world build --rules rules.json --output <DIR> \[--dry-run] \[--yes]

mmffc world validate <DIR> \[--json]

mmffc world inspect <DIR> \[--json]

```



示例：



```bash

mmffc world init ./saves/test --mc-version 1.20.1 --name "Test"

cat chunks.ndjson \\

&#x20; | mmffc world compile --output ./saves/test --region 0,0 --heightmap auto --yes

mmffc world validate ./saves/test

```



3.5 mmffc rcon — 游戏内热重载



```bash

mmffc rcon exec <command> \[--host 127.0.0.1] \[--port 25575] \[--password-env MMFFC\_RCON\_PASSWORD]

mmffc rcon reload \[fancymenu|ftbquests|all] \[--host ...]

```



3.6 mmffc cache — 缓存管理



```bash

mmffc cache info \[--json]

mmffc cache clean \[--all|--mods|--structures]

```



3.7 mmffc shell — 可选交互式 REPL



Unix 风格默认非交互；mmffc shell 提供可选的 DOS 风格交互层，内部仍调用相同子命令。可保留历史、补全、彩色输出，但不作为自动化入口。



4\. 管道组合示例



```bash

\# 1. 搜索 + 过滤 + 安装

mmffc mod search "create" --loader forge --mc-version 1.20.1 --ndjson \\

&#x20; | jq -r 'select(.downloads > 1000000) | .project\_id' \\

&#x20; | mmffc mod install --stdin --resolve-deps --yes



\# 2. AI 生成 FancyMenu 布局，CLI 校验并写入

ai-agent generate-fancymenu --prompt "主菜单加一个按钮" \\

&#x20; | mmffc config validate fancymenu - \\

&#x20; \&\& ai-agent generate-fancymenu --prompt "主菜单加一个按钮" \\

&#x20;    | mmffc config apply fancymenu - --target ./config/fancymenu --yes



\# 3. 体素 NDJSON → litematic

cat voxels.ndjson | mmffc structure compile --format litematic -o castle.litematic



\# 4. 世界规则 → MCA

mmffc world init ./saves/test --mc-version 1.20.1 --name "Test"

cat chunks.ndjson | mmffc world compile --output ./saves/test --region 0,0 --heightmap auto --yes

mmffc world validate ./saves/test --json | jq '.valid'

```



5\. 实现映射（Python）



需求 技术选型

命令路由 click 的 @click.group() + 子命令

彩色表格 rich；--json 时切换为纯 json.dumps

NDJSON 逐行 json.dumps + \\n，流式输出

交互式 REPL prompt\_toolkit，仅 mmffc shell 使用

原子写入 临时文件 + os.replace()

备份 .mmffc/backups/<timestamp>/

Schema 校验 jsonschema + 自定义语义校验

RCON mcrcon / mcnexus

文件监听 watchdog（单人游戏热重载）

信号处理 SIGINT → 退出码 130；SIGPIPE 默认行为



6\. 与 MCP 的边界



· CLI 非 MCP：不调用 LLM，不暴露 MCP Tools，不处理自然语言。

· MCP 负责“脑”：complex\_nbt\_compiler、voxelizer\_brain、world\_gen\_rule\_engine 生成 JSON/NDJSON/规则文件。

· CLI 负责“手”：通过 stdin 接收 AI 输出，做 Schema 校验、语义校验、原子写入。

· 可组合：MCP Server 内部也可调用 CLI 子进程完成落盘，但 CLI 本身保持确定性。



典型链路：



```

自然语言 → MCP Tool → NDJSON/JSON5 → stdin → mmffc config apply / structure compile / world compile → 落盘

```



7\. 验收清单



☐ 所有子命令支持 --help 和 --json / --ndjson。

☐ 所有读取命令支持 - 从 stdin 读取。

☐ 所有写入命令支持 --dry-run、--yes、备份。

☐ 退出码稳定，错误输出到 stderr。

☐ --json 输出可被 jq 解析。

☐ NO\_COLOR=1 或 --no-color 时无 ANSI 转义。

☐ 非 TTY 下高风险操作未加 --yes 时退出码为 2。

☐ mmffc shell 可选，不影响管道自动化。

