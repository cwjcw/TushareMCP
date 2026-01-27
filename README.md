# TushareMCP

一个面向 BI 工程师 / 量化开发者的「元数据驱动（Metadata-Driven）」Tushare 万能 MCP 工具：

- 离线层：用 Playwright 抓取 Tushare 文档，生成 `tushare_api_specs.json`
- 服务层：FastMCP + 反射转发器（`getattr(pro, api_name)(**params)`）
- 客户端：在 Cherry Studio / Codex 里先查字典再执行，避免“幻觉参数”

## 设计理念

- 解耦：接口定义（Schema）与代码实现（Implementation）完全分离，Tushare 文档变化只需更新 specs 文件。
- 反射：通过动态转发 `getattr(pro, api_name)(**params)` 替代上百个静态函数。
- 认知闭环：在客户端 SOP 中强制“先查字典再执行”，避免幻觉参数。

## 架构概览

1) 离线层（Cartographer）：抓取 Tushare 文档，生成 `tushare_api_specs.json`
2) 服务层（Universal Gateway）：FastMCP + 反射 + 风控（限流/截断/异常处理）
3) 客户端（Cognitive Agent）：严格按“查字典 -> 构参 -> 执行 -> 解释”流程

## 1) 安装

建议使用虚拟环境：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[scrape]"
playwright install chromium
```

需要设置 Tushare Token：

```bash
export TUSHARE_TOKEN="your_token"
```

## 2) 离线构建数据字典（Scraper）

生成 `data/tushare_api_specs.json`：

```bash
tushare-mcp-scrape --base-url https://tushare.pro/document/2 --output data/tushare_api_specs.json
```

如果需要登录态（可选），先用 Playwright 导出 storage state，然后：

```bash
tushare-mcp-scrape --storage-state storage_state.json
```

## 3) 运行万能 MCP Server（Stdio）

```bash
tushare-mcp-server --specs data/tushare_api_specs.json
```

该 Server 仅暴露 2 个核心工具：

- `search_api_docs(keyword, limit=10)`：查字典（模糊搜索 + 返回参数/字段）
- `execute_tushare_query(api_name, params)`：万能执行（反射调用 + 限流 + 自动截断）

### MCP 客户端配置示例

见 `examples/mcp_config.json`。

## 4) 风控参数（可选）

- `TUSHARE_MCP_MAX_ROWS`：返回行数截断（默认 100）
- `TUSHARE_MCP_MIN_INTERVAL_SECONDS`：最小请求间隔（默认 0.35s）
- `TUSHARE_MCP_SPECS_PATH`：默认 specs 路径（默认 `data/tushare_api_specs.json`）
- `TUSHARE_POINTS`：你的积分（可选，用于自动匹配限流档位）
- `TUSHARE_MCP_LIMITS_PATH`：限流档位映射文件（默认 `config/tushare_rate_limits.json`）

如不想在 `mcp_config.json` 里写死数值，可在本地 `.env` 配置：

```bash
TUSHARE_TOKEN=your_token
TUSHARE_POINTS=0
TUSHARE_MCP_LIMITS_PATH=config/tushare_rate_limits.json
```

### 档位匹配与优先级

Server 启动时会按以下优先级确定限流参数：

1) 显式参数：`--max-rows` 与 `--min-interval-seconds`（或同名环境变量）
2) 档位匹配：`TUSHARE_POINTS` + `config/tushare_rate_limits.json`
3) 默认值：`max_rows=100`、`min_interval_seconds=0.35`

> `min_interval_seconds = 60 / 每分钟频次`，已在档位文件中预计算。

### 推荐 SOP（客户端）

1) `search_api_docs` 搜索接口与参数
2) 根据 `required` 构造参数
3) `execute_tushare_query` 执行
4) 若报错或缺参，回到第 1 步修正

### 限流配置文件

- 正式配置：`config/tushare_rate_limits.json`
- 示例模板：`config/tushare_rate_limits.example.json`

该文件包含：
- `tiers`：不同积分的频次限制（含 `min_interval_seconds`）
- `independent_permissions`：独立权限项的“每次返回行数/每次可请求股票数”，避免混淆常规接口
