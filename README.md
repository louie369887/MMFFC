# MMFFC

**Minecraft Mod / FancyMenu / FTB / Control 自动化框架**

MMFFC 采用「确定性底层管道 + AI 模糊逻辑大脑」双核架构：

- **确定性 CLI（`mmffc`）**——从不调用 LLM：接收 stdin/文件输入，校验后原子落盘；输出默认人类可读，`--json` / `--ndjson` 供机器消费，可直接接入 `jq` / `grep` / `xargs` 管道。
- **AI 工具面（`mmffc-mcp`）**——stdio 模式的 [MCP](https://modelcontextprotocol.io) Server，向 Claude Desktop / Cursor 等 AI 客户端暴露工具；AI 生成的内容交回 CLI 校验与落盘，职责边界清晰（CLI 不调用 LLM，AI 不直接写文件）。

完整命令规范见 [CLI.md](CLI.md)。

## 功能

- **模组管理**：Modrinth / CurseForge 搜索与安装、依赖解析、本地扫描、原子写入 + 备份
- **配置校验与写入**：FancyMenu 布局 DSL、FTB Quests（JSON5 / SNBT）、SNBT、JSON5；两阶段校验（结构 + 语义），校验失败退出码 3
- **结构生成**：体素 NDJSON → `.nbt`（原版结构模板）/ `.litematic`（Litematica 原理图），两格式互转与检查
- **游戏内热重载**：Source RCON 客户端（`exec` / `reload` / `watch`）
- **安全写入**：写前校验、UTC 时间戳备份、原子替换；`--dry-run` 只打印操作计划；非 TTY 高风险操作强制 `--yes`

## 安装

要求 Python ≥ 3.11。

```bash
cd MMFFC
pip install -e ".[dev]"
mmffc --version
```

安装后提供两个入口：

| 入口 | 说明 |
|---|---|
| `mmffc` | 确定性 CLI |
| `mmffc-mcp` | MCP Server（stdio） |

## 快速开始

```bash
# 搜索模组（输出可直接给 jq）
mmffc mod search sodium --loader fabric --json

# 校验 FancyMenu 布局（退出码 3 = 校验失败）
mmffc config validate fancymenu layout.txt --json

# 原子安装（非 TTY 下必须显式 --yes；可先 --dry-run 预览）
mmffc mod install sodium --dry-run --json
mmffc mod install sodium --yes
```

### 体素 NDJSON → 结构文件

```bash
cat > voxels.ndjson <<'EOF'
{"x": 0, "y": 0, "z": 0, "block": "stone"}
{"x": 1, "y": 0, "z": 0, "block": "oak_planks", "properties": {"facing": "north"}}
{"x": 0, "y": 1, "z": 0, "block": "glass"}
EOF

mmffc structure compile --format nbt       -o house.nbt       --input voxels.ndjson --yes --json
mmffc structure compile --format litematic -o house.litematic --input voxels.ndjson --yes --json
mmffc structure inspect  house.litematic --json
mmffc structure convert  house.nbt house2.litematic --format litematic --yes
```

NDJSON 每行一个方块，字段 `x/y/z/block`（命名空间可省略，默认 `minecraft:`）+ 可选 `properties`；重复坐标、未知字段、非整数坐标均为数据错误（退出码 3）。

## 命令树

统一入口：`mmffc <noun> <verb> [options] [args]`（类似 `git remote add`）。

| 分组 | 子命令 | 说明 |
|---|---|---|
| `mod` | `search` `info` `install` `list` `scan` `deps` `update` `remove` | 模组管理 |
| `config` | `validate` `apply` `get` `set` `diff` | 配置校验与写入 |
| `structure` | `compile` `inspect` `convert` | NDJSON → `.nbt` / `.litematic` |
| `rcon` | `exec` `reload` `watch` | 游戏内热重载 |
| `world` | `init` `compile` `build` `validate` `inspect` | 世界存档（Phase 4 占位） |
| `cache` | `info` `clean` | 缓存管理 |
| `shell` | — | 交互式 shell（自动化请用管道） |
| `mcp` | — | 启动 MCP Server（stdio） |

全局选项（所有命令可用，`mmffc --help` 为权威列表）：`--json` / `--ndjson` / `--format table|json|ndjson|csv|tsv|raw` / `--dry-run` / `--yes` / `--no-color` / `--quiet` / `--verbose` / `--cwd` / `--config` / `--log-level`。

> 注意：`structure compile|convert` 的 `--format nbt|litematic` 是**结构格式**，会遮蔽同名的全局输出格式选项；此时全局输出格式需置于子命令之前（如 `mmffc --format json structure ...`）或改用 `--json`。

## 退出码

| 码 | 含义 | 码 | 含义 |
|---:|---|---:|---|
| 0 | 成功 | 6 | 权限错误 |
| 1 | 通用错误 | 7 | 依赖未满足 |
| 2 | 用法 / 参数错误 | 8 | 存档完整性风险 |
| 3 | Schema 校验失败 | 130 | SIGINT 中断 |
| 4 | 网络错误（API、下载） | 141 | 管道破裂（SIGPIPE） |
| 5 | 文件冲突 / 已存在 | | |

流约定：stdout = 正常结果，stderr = 日志/进度/错误，`-` 或省略 = stdin。

## MCP Server

向 AI 客户端提供 4 个工具（stdio 传输，stdout 仅承载 MCP 协议，日志走 stderr）：

| 工具 | 说明 |
|---|---|
| `voxelizer_brain` | 形状（box / sphere / cylinder / line 等）与方块描述 → 体素 NDJSON |
| `complex_nbt_compiler` | FancyMenu 布局 / FTB Quests 章节的编译与校验 |
| `mod_search` | 搜索 Modrinth / CurseForge |
| `mod_install` | 下载安装模组（依赖解析、原子落盘） |

Claude Desktop / Cursor 配置示例：

```json
{
  "mcpServers": {
    "mmffc": {
      "command": "mmffc-mcp"
    }
  }
}
```

`voxelizer_brain` 请求示例：

```json
{
  "description": "stone wall 4x3",
  "context": {
    "ops": [
      { "shape": "box", "block": "stone", "x": 0, "y": 0, "z": 0, "w": 4, "h": 3, "d": 1 }
    ]
  }
}
```

返回校验后的 NDJSON，接 CLI 落盘：`mmffc structure compile --format litematic -o out.litematic --yes`。

## 环境变量

| 变量 | 说明 |
|---|---|
| `MMFFC_HOME` | 数据目录，默认 `~/.mmffc`（配置与备份） |
| `MMFFC_CONFIG` | 配置文件路径 |
| `MMFFC_MODS_DIR` / `MMFFC_CACHE_DIR` | 默认 mods / 缓存目录 |
| `MODRINTH_TOKEN` | Modrinth Personal Access Token |
| `CURSEFORGE_API_KEY` | CurseForge API Key |
| `MMFFC_RCON_PASSWORD` | RCON 密码 |
| `NO_COLOR` | 禁用颜色 |

配置优先级：命令行 > 环境变量 > 项目配置 > 用户配置 > 默认值。

## 项目结构

```
src/mmffc/
├── cli/        # 命令树（mod / config / structure / world / rcon / cache / shell / mcp）
├── core/       # 上下文与全局选项、输出渲染、错误与退出码、体素模型
├── formats/    # FancyMenu / FTB Quests / SNBT / JSON5 / NBT / structure / litematic
├── io/         # 两阶段校验、原子写入 + 备份
├── mods/       # Modrinth / CurseForge 客户端与安装计划
├── net/        # Source RCON 客户端
└── mcp/        # MCP Server（stdio）
tests/          # pytest 测试套件
tests-data/     # 测试夹具
CLI.md          # CLI 权威规范
```

## 开发

```bash
python -m pytest        # 311 个测试，全绿
```

## 路线图

- ✅ **Phase 1**（`v0.1.0-phase1`）：CLI 框架 + 模组管理
- ✅ **Phase 2**（`v0.2.0-phase2`）：MCP 基础设施 + 安全写入管线 + 格式层 + RCON
- ✅ **Phase 3**（`v0.3.0-phase3`）：`voxelizer_brain` + 结构生成（`.nbt` / `.litematic`）
- ⬜ **Phase 4**：`world` 命令组（MCA 编译、世界生成）——当前为占位命令
