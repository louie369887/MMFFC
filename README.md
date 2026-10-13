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
- **世界存档生成**：`world init` / `compile` / `build` 生成 1.20.1 Anvil 世界（`level.dat` + `session.lock` + `r.X.Z.mca`），Heightmaps、区域合并、`--max-voxels` 分批预算、完整性校验与检查
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

### 世界存档（Anvil / 1.20.1）

```bash
# 1. 初始化世界目录（level.dat + session.lock + region/）
mmffc world init ./saves/test --mc-version 1.20.1 --name "Test" --json

# 2. 体素 NDJSON -> r.0.0.mca（Heightmaps 自动计算）
cat voxels.ndjson | mmffc world compile --output ./saves/test --yes --json

# 3. 或用规则文件（layers 铺层 -> 形状 ops -> 显式 blocks）
mmffc world build --rules rules.json --output ./saves/test --yes --json

# 4. 校验与检查
mmffc world validate ./saves/test --json | jq '.valid'
mmffc world inspect  ./saves/test --json
```

管线为 `NDJSON/规则 -> 区块(24 段 Y=-4..19, YZX 位打包) -> r.X.Z.mca`；区块 `DataVersion` 取自目标世界的 `level.dat`，`compile` / `build` 要求目录已 `world init`（否则退出码 2）。重复编译与已有区块合并并自动备份；`--heightmap none` 省略 Heightmaps（游戏加载时重算）；`--max-voxels N` 设定单批预算（超出退出码 3，便于 AI 分批驱动大世界）。

规则文件 schema（由 MCP `world_gen_rule_engine` 产出，未知键 / 类型错误退出码 3）：

```json
{
  "name": "test",
  "seed": 12345,
  "area": { "x": 0, "z": 0, "w": 16, "d": 16 },
  "layers": [{ "from": -60, "to": -58, "block": "minecraft:stone" }],
  "ops": [{ "shape": "sphere", "block": "glass", "cx": 8, "cy": 70, "cz": 8, "r": 3 }],
  "blocks": [{ "x": 8, "y": 80, "z": 8, "block": "beacon" }]
}
```

### 端到端链路（Phase 5 集成测试覆盖）

```bash
mmffc mod search sodium --loader fabric --mc-version 1.20.1 --json
mmffc mod install sodium --mc-version 1.20.1 --loader fabric --yes
ai-agent generate-quests --prompt "..." | mmffc config validate ftbquests -   # MCP 产出内容
mmffc structure compile --format litematic -o out.litematic --yes              # voxelizer_brain 产出
mmffc world build --rules rules.json --output ./saves/test --yes --max-voxels 250000
mmffc world validate ./saves/test --json | jq '.valid'
```

对应测试 `tests/test_integration_pipeline.py` 以真实代码路径走完整链路（仅网络层打桩）。

### 统一入口 mmffc.exe（ASCII 立体字 + mmffc> 提示符）

PyInstaller onefile 构建的 `dist/mmffc.exe` 是唯一入口，安装到 PATH 后即可在 cmd 直接使用：

```bat
mmffc                                  :: 无参数：ASCII 立体字横幅 + mmffc> 交互提示符
mmffc> world init ./saves/test --mc-version 1.20.1     :: 交互内省略 mmffc 前缀
mmffc> game diagnose ./instance/.minecraft --json
mmffc> mcp                             :: 交互内启动 MCP Server（stdio）
mmffc> exit

mmffc world validate ./saves/test --json        :: 带参：一次性执行（CLI.md 命令去掉前缀）
mmffc --mcp                                  :: 以 stdio 启动 MCP Server 供 AI 客户端连接
mmffc --shell                                :: 强制进入交互 shell（管道驱动也可用）
```

横幅为挤出阴影的 ASCII 立体字（`█` 字面 + `▒` 阴影层）；目标输出流无法编码 Unicode 时自动降级为 `#`/`:` ASCII 字面，stdout 统一重配置为 UTF-8。

### 非模组内容：资源包 / 数据包 / 光影包 / 世界存档（Phase 7）

```bash
# 资源包 / 光影包 / 数据包 / 整合包（平台能力见 prd.md Phase 7）
mmffc pack search faithful --type resourcepack --mc-version 1.20.1 --json
mmffc pack install faithful-32 --type resourcepack --game-dir ./instance --yes --json
mmffc pack validate ./instance/resourcepacks/faithful --json | jq '.valid'
mmffc pack list --game-dir ./instance --json

# 数据包装到指定世界的 datapacks/（省略 --world 时退回 resourcepacks/）
mmffc pack install my-datapack --type datapack --game-dir ./instance --world test --yes

# 世界存档导入（.zip / .mcworld；防 zip-slip，导入前/后校验）
mmffc world import ./downloads/AdventureMap.zip --saves-dir ./saves --yes --json
mmffc world validate ./saves/AdventureMap --json
```

内容类型由「确定性文件存在性」判定：资源包 = `pack.mcmeta` + `assets/`，数据包 = `pack.mcmeta` + `data/`，光影包 = `shaders/program/`（`*.vsh|*.fsh|*.gsh`），世界 = `level.dat` + `region/`。`pack_format` 必须与目标版本兼容（1.20.1 = 15，不兼容退出码 3）。Modrinth 的 `project_type` 支持 `resourcepack/shader/modpack`（`datapack` 走 `all_project_types`），CurseForge 按 classId（12/6552/17/4471/6945）。

## 功能概览

| 能力 | 命令组 | 说明 |
|---|---|---|
| 模组管理 | `mod` | Modrinth / CurseForge 搜索、安装（依赖解析）、本地扫描、清单与移除 |
| 配置校验与写入 | `config` | FancyMenu DSL、FTB Quests（JSON5 / SNBT）两阶段校验 + 原子落盘 |
| 结构生成 | `structure` | 体素 NDJSON → `.nbt` / `.litematic`，互转与检查 |
| 世界存档 | `world` | 1.20.1 Anvil 生成（`level.dat` / `session.lock` / `r.X.Z.mca`）、Heightmaps、区域合并、完整性校验、`.zip`/`.mcworld` 导入 |
| 非模组内容 | `pack` | 资源包 / 数据包 / 光影包 / 整合包的搜索安装与 `pack_format` 校验 |
| 崩溃诊断 | `game` | `latest.log` / 崩溃报告 / `hs_err` 解析与归因；真实启动验证版本保活 |
| 游戏内热重载 | `rcon` | Source RCON `exec` / `reload` / `watch` |
| AI 工具面 | `mmffc-mcp` | 5 个 MCP 工具：`complex_nbt_compiler` / `voxelizer_brain` / `world_gen_rule_engine` / `mod_search` / `mod_install` |

设计红线（CLI.md）：CLI 永不调用 LLM；AI 产出经 stdin/文件交给 CLI 校验后落盘；所有写入原子 + 备份 + `--dry-run` + `--yes`。

## 命令树

统一入口：`mmffc <noun> <verb> [options] [args]`（类似 `git remote add`）。

| 分组 | 子命令 | 说明 |
|---|---|---|
| `mod` | `search` `info` `install` `list` `scan` `deps` `update` `remove` | 模组管理 |
| `config` | `validate` `apply` `get` `set` `diff` | 配置校验与写入 |
| `structure` | `compile` `inspect` `convert` | NDJSON → `.nbt` / `.litematic` |
| `rcon` | `exec` `reload` `watch` | 游戏内热重载 |
| `world` | `init` `compile` `build` `validate` `inspect` `import` | 世界存档（Anvil / level.dat） |
| `game` | `log` `crash` `diagnose` `verify` `list` | 崩溃诊断与版本保活（Phase 6） |
| `pack` | `search` `install` `list` `validate` | 资源包 / 数据包 / 光影包 / 整合包（Phase 7） |
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

向 AI 客户端提供 5 个工具（stdio 传输，stdout 仅承载 MCP 协议，日志走 stderr）：

| 工具 | 说明 |
|---|---|
| `voxelizer_brain` | 形状（box / sphere / cylinder / line 等）与方块描述 → 体素 NDJSON |
| `world_gen_rule_engine` | 自然语言 / 上下文 → 世界生成规则 JSON（`world build` 消费；长时生成上报进度） |
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

`world_gen_rule_engine` 请求示例（也可只给 `description`，默认产出 16×16 石头平台）：

```json
{
  "description": "中心带金字塔的测试世界",
  "context": {
    "area": { "x": 0, "z": 0, "w": 8, "d": 8 },
    "layers": [{ "from": -60, "to": -60, "block": "minecraft:stone" }],
    "ops": [{ "shape": "hollow_box", "x": 0, "y": 0, "z": 0, "w": 4, "h": 3, "d": 4 }]
  }
}
```

返回严格校验后的规则 JSON，接 CLI：`mmffc world build --rules rules.json --output ./saves/test --yes`。

### 长时操作与进度

官方 MCP SDK 2.3.0（协议 2026-07-28）尚未提供 `io.modelcontextprotocol/tasks` 能力，因此长时世界生成采用等价机制：MCP 侧分阶段上报 progress notifications（`layers` → `ops` → `blocks`），CLI 侧用 `--max-voxels` 预算上限分批驱动。客户端在 `tools/call` 时带 `_meta.progressToken` 即可收到进度。

## 环境变量

| 变量 | 说明 |
|---|---|
| `MMFFC_HOME` | 数据目录，默认 `~/.mmffc`（配置与备份） |
| `MMFFC_CONFIG` | 配置文件路径 |
| `MMFFC_MODS_DIR` / `MMFFC_CACHE_DIR` | 默认 mods / 缓存目录 |
| `MODRINTH_TOKEN` | Modrinth Personal Access Token |
| `CURSEFORGE_API_KEY` | CurseForge API Key |
| `MMFFC_RCON_PASSWORD` | RCON 密码 |
| `MMFFC_JAVA` / `JAVA_HOME` | Phase 6 `game verify` 启动服务端 jar 使用的 JVM 路径 |
| `NO_COLOR` | 禁用颜色 |

配置优先级：命令行 > 环境变量 > 项目配置 > 用户配置 > 默认值。

## 项目结构

```
src/mmffc/
├── cli/        # 命令树（mod / config / structure / world / pack / game / rcon / cache / shell / mcp）
├── core/       # 上下文与全局选项、输出渲染、错误与退出码、体素模型、世界规则引擎
├── formats/    # FancyMenu / FTB Quests / SNBT / JSON5 / NBT / structure / litematic / anvil / chunk / level.dat
├── game/       # 日志解析、崩溃报告、归因分类、版本保活验证（Phase 6）
├── packs/      # pack.mcmeta、资源/数据/光影包识别、安全解包与世界导入（Phase 7）
├── io/         # 两阶段校验、原子写入 + 备份
├── mods/       # Modrinth / CurseForge 客户端与安装计划
├── net/        # Source RCON 客户端
└── mcp/        # MCP Server（stdio）
tests/          # pytest 测试套件（含 test_integration_pipeline.py 端到端链路）
tests-data/     # 测试夹具
CLI.md          # CLI 权威规范
prd.md          # 产品需求文档（Phase 6 规划见其中）
```

## 开发

```bash
python -m pytest        # 561 个测试，全绿

# 构建统一入口 exe（PyInstaller onefile，产物 dist/mmffc.exe）
python -m PyInstaller --onefile --console --name mmffc --distpath dist \
  --workpath build/pyi --specpath build --paths src \
  --collect-submodules mmffc --collect-submodules mcp.server \
  --collect-all pydantic --collect-all pydantic_core \
  --collect-all jsonschema --exclude-module mcp.cli \
  src/mmffc/launcher.py
```

## 路线图

- ✅ **Phase 1**（`v0.1.0-phase1`）：CLI 框架 + 模组管理
- ✅ **Phase 2**（`v0.2.0-phase2`）：MCP 基础设施 + 安全写入管线 + 格式层 + RCON
- ✅ **Phase 3**（`v0.3.0-phase3`）：`voxelizer_brain` + 结构生成（`.nbt` / `.litematic`）
- ✅ **Phase 4**（`v0.4.0-phase4`) ：`world` 命令组——Anvil 区域编译（`level.dat` / `session.lock` / `r.X.Z.mca`）、Heightmaps、世界规则引擎与 `world_gen_rule_engine` MCP 工具
- ✅ **Phase 5**：MCP Tool Schema/描述完善（5 工具全覆盖 + schema 断言）、长时生成进度上报与 `--max-voxels` 预算、端到端集成测试（`tests/test_integration_pipeline.py`）
- ✅ **Phase 6**：`game` 命令组——日志解析（`game log`）、崩溃报告解析（`game crash`）、综合归因（`game diagnose`）与版本保活验证（`game verify`，真实启动 + 存活标志 + 保活窗口）
- ✅ **Phase 7**：`pack` 命令组（资源包/数据包/光影包/整合包）+ `world import`——pack.mcmeta/pack_format 校验、平台能力栅格（Modrinth project_type / CurseForge classId）、zip-slip 安全解包；统一入口 `mmffc.exe`（ASCII 立体字横幅 + `mmffc>` 交互提示符，支持管道驱动的 `--shell` 与 `--mcp`）

> 注意：
> 
> 文中”minecraft“游戏著作权由microsoft及mojang AB工作室所有，该项目仅是一个让MCP工具操控并制作minecraft整合包的工具，在遵守EULA的情况下使用；
> 
> 该工具由opencode参与开发，请注意辨别。
> 
> 该工具遵守MIT协议，仅与Minecraft本体游戏和mod无关的部分
