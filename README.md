# gfsdown — GFS GRIB2 切片下载工具

从 NOAA 公开 S3 存储桶下载 GFS GRIB2 数据，通过 HTTP Range 请求实现变量级切片，
将数 GB 的预报文件缩减到 ~2MB，精确抓取你需要的数据。

## 环境要求

- Python 3.12+
- `uv` 包管理器

无需手动安装 eccodes C 库 —— PyPI 上的 `eccodeslib` 二进制 wheel 已经把 libeccodes
打包进 venv，`uv sync` 一步到位（macOS arm64 / x86_64、Linux、Windows 均有现成 wheel）。

## 安装

```bash
uv sync
```

## 快速开始

### 1. 编辑配置

修改 `config.yaml`，设置日期/区间、轮次、变量和区域：

```yaml
# ── 日期：单日 OR 区间 ────────────────────────────────────────
date: "2026-05-01"              # 单日预报初始化日期
# date_range:                   # 区间（设置后覆盖 date）
#   start: "2026-01-01"
#   end:   "2026-02-01"
#   step_days: 1                # 默认 1，例如 7 = 每周采样一次

# ── 轮次：单个 OR 多个 ────────────────────────────────────────
cycle: 12                       # 单轮次（0/6/12/18 UTC）
# cycles: [0, 6, 12, 18]        # 多轮次（设置后覆盖 cycle）

# ── 预报时次范围 ──────────────────────────────────────────────
# 写成 {start, end, step} 区间，或者直接写 "all" 表示
# 自动发现并下载 S3 上每个 init 的全部可用时次
forecast_hours:
  start: 0                      # 起始预报时次
  end: 24                       # 结束预报时次（含）
  step: 6                       # 间隔（小时）
# forecast_hours: all           # 下载全部可用 f-hour（等价于 CLI --all-hours）

variables:
  - name: "UGRD"
    level: "10 m above ground"
  - name: "VGRD"
    level: "10 m above ground"

region:                         # 地理区域 (可选，不填则下载全球)
  lat_min: 15.0
  lat_max: 55.0
  lon_min: 105.0
  lon_max: 150.0

output_dir: "./output"
plot:
  enabled: false   # 默认不绘图，CLI 加 --plot 才绘制
  type: "wind_speed"
  dpi: 300
```

每次下载会按 **日期 × 轮次 × 预报时次** 三重循环展开，
文件按 `<output_dir>/<YYYYMMDD>/<CC>z/gfs_fXXX.grib2` 自动归档，
不同日期/轮次互不覆盖。

### 2. 查看可用变量与预报时次

```bash
# 列出 f006 时次所有变量 (区分多层/单层)
uv run main.py --list-vars --hour 6

# 查看某个变量的所有层次 (可直接复制到 config.yaml)
uv run main.py --var TMP --hour 6

# 查询某天某轮次在 S3 上实际可用的所有预报时次
uv run main.py --date 2025-01-15 --cycles 12 --list-hours
```

> **`--hour` 只给查询命令用**（`--list-vars` / `--var`），用来选要探测的那个 idx 文件，
> 不会触发任何 GRIB2 下载。下载时要指定预报时次请用 `--fhours`（见下文）。
# 输出会自动检测步长分组，例如：
#   f000 - f120  step 1h     (121 files)
#   f123 - f384  step 3h     (88 files)
#   Max lead time: f384 (16 days 0h ahead)
```

### 3. 运行下载

```bash
# 根据 config.yaml 下载数据（默认只下载，不绘图）
uv run main.py

# 下载并绘图
uv run main.py --plot

# 快速演示 (下载 config 中 date 的风速数据，自动开启绘图)
uv run main.py --demo

# 显式禁用绘图（覆盖 yaml 里 plot.enabled: true 的情况）
uv run main.py --no-plot
```

### 4. CLI 轻量覆盖（不改 config.yaml）

CLI 参数优先级高于 `config.yaml`，未指定的字段仍由配置文件提供。

```bash
# 单日 + 单轮次
uv run main.py --date 2026-05-01 --cycles 12

# 历史区间下载（指定起止日期）
uv run main.py --date-range 2026-01-01:2026-02-01 --cycles 12

# 区间 + 步长（每 7 天采一次）
uv run main.py --date-range 2026-01-01:2026-04-01:7

# 多轮次（同一天下 4 个轮次）
uv run main.py --cycles 0,6,12,18

# 覆盖预报时次：单值 / 范围 / 带步长
uv run main.py --fhours 1            # 只下 f001
uv run main.py --fhours 0:24         # f000–f024，步长 1
uv run main.py --fhours 0:120:3      # f000–f120，步长 3

# 下载所有可用预报时次（每个 init 自动从 S3 LIST 发现）
uv run main.py --date 2025-01-15 --cycles 12 --all-hours

# 1 个月 × 4 轮次 × 默认预报时次（默认即不绘图）
uv run main.py --date-range 2026-01-01:2026-02-01 --cycles 0,6,12,18
```

互斥规则：`--date` 与 `--date-range` 二选一；`--fhours` 与 `--all-hours` 二选一；
`--plot` 与 `--no-plot` 二选一。
`--cycles` 既可单值（`--cycles 12`）也可多值（`--cycles 0,6,12,18`）。
`--all-hours` 会覆盖 `config.yaml` 里的 `forecast_hours`。
`--plot` / `--demo` 会强制开启绘图，`--no-plot` 会强制关闭。

### `--fhours` vs `--hour` —— 别混

| 标志 | 用途 | 接受值 | 影响下载？ |
|---|---|---|---|
| `--fhours` | **下载**时覆盖预报时次范围（覆盖 yaml 的 `forecast_hours`） | `N` / `START:END` / `START:END:STEP` | ✅ 会 |
| `--hour` / `-H` | **查询**命令选要探测的 idx 时次（仅 `--list-vars` / `--var` 读取） | 单个整数 | ❌ 不会 |

```bash
uv run main.py --hour 6              # ❌ 没用 — 不在查询模式下，--hour 被忽略；
                                     #     下载范围仍按 yaml 的 forecast_hours 走
uv run main.py --fhours 6            # ✅ 下 f006 一个时次
uv run main.py --list-vars --hour 6  # ✅ 查 f006 idx 里有什么变量
```

简单记忆：**`--fhours` 是干活的，`--hour` 是查表的**。

### 下载进度

#### 交互终端 —— 双层 tqdm 进度条

```
Files:  35%|███▌      | 14/40 [02:13<04:08,  9.5s/file]
  gfs_f014.grib2:  62%|██████▏   | 1.24M/2.01M [00:01<00:00, 712kB/s]
```

- **外层 `Files`**：整体文件进度（已完成 / 总数），含 ETA 与单文件平均耗时。
- **内层 `gfs_fXXX.grib2`**：当前文件的下载字节数 / 总字节数 + **实时网速**（自动 KB/s ↔ MB/s）。
- 跳过的（已下载并通过 cfgrib 校验）也让外层 bar +1，但不会出现内层 bar。
- 失败的同样 +1，且 `last_run.json` 会记下失败条目。

#### 后台运行 / `nohup &` / `tail -f log` —— `[N/M]` 进度前缀

非交互场景（管道、重定向到日志、CI 环境）会**自动关闭**进度条，避免 ANSI 控制字符
塞满日志。但每一条关键日志行都带 `[N/M]` 前缀，方便后台挂起后用 `tail -f` 看进度：

```
12:34:01 [INFO] [14/40] Fetching index for 2026-01-01 12Z f014: gfs.t12z.pgrb2.0p25.f014.idx
12:34:03 [INFO] [14/40] Saved 20260101/12z/gfs_f014.grib2 (32,514,231 bytes)
12:34:03 [INFO] [15/40] Skipping 2026-01-01 12Z f015: already downloaded (32,498,776 bytes ...)
12:34:03 [INFO] [16/40] Fetching index for 2026-01-01 12Z f016: ...
```

典型用法：

```bash
nohup uv run main.py --date-range 2026-01-01:2026-02-01 --cycles 0,12 > run.log 2>&1 &
tail -f run.log | grep -E "\[\d+/\d+\]"   # 只看进度行
```

也可显式禁用 bar（不影响 `[N/M]` 前缀）：

```bash
uv run main.py --no-progress
```

## 变量与时次发现

| 命令 | 说明 |
|------|------|
| `--list-vars` | 分类展示某个预报时次的所有变量（区分多层 / 单层） |
| `--var <变量名>` | 查看指定变量的所有层次，输出可直接粘贴到 yaml |
| `--list-hours` | 列出指定 date+cycle 在 S3 上**实际可用**的所有预报时次（自动按步长分组） |

### 变量列表示例

```
$ uv run main.py --var UGRD --hour 6

UGRD [58 levels]:
    - {name: "UGRD", level: "10 m above ground"}
    - {name: "UGRD", level: "100 m above ground"}
    - {name: "UGRD", level: "500 mb"}
    - {name: "UGRD", level: "850 mb"}
    ...
```

复制所需行到 `config.yaml` 的 `variables:` 下即可。

### 预报时次列表示例

```
$ uv run main.py --date 2025-01-15 --cycles 12 --list-hours

Available forecast hours for 2025-01-15 12Z (209 total)
=================================================================
  f000 - f120  step 1h     (121 files)
  f123 - f384  step 3h     (88 files)

Max lead time: f384  (16 days 0h ahead)
```

GFS 单次初始化的预报时次约 209 个：0–120h 逐小时，120–384h 每 3 小时。

## 输出目录结构

```
output/
├── logs/
│   ├── gfsdown_20260101_120000.log    # 每次下载一份完整日志
│   └── last_run.json                  # 最近一次下载的失败清单（每次覆盖）
├── 20260101/
│   ├── 00z/
│   │   ├── gfs_f000.grib2
│   │   ├── gfs_f006.grib2
│   │   └── plots/
│   │       ├── wind_f000.png
│   │       └── wind_f006.png
│   └── 12z/
│       └── ...
└── 20260102/
    └── ...
```

每个日期的每个轮次独立目录，多日期 / 多轮次批量下载也不会冲突。

`last_run.json` 结构（适合脚本读取做重试）：

```json
{
  "timestamp": "2026-05-09T12:34:56+08:00",
  "succeeded_count": 198,
  "failed_count": 2,
  "failed": [
    {"date": "2026-01-01", "cycle": 0, "forecast_hour": 45, "reason": "byte-range fetch failed: ..."}
  ]
}
```

## 工作原理

```
config.yaml + CLI 覆盖 → 展开 dates × cycles × forecast_hours
            → 抓 .idx → 定位字节偏移 → HTTP Range 切片 → 拼接 .grib2 → 可视化
```

1. **索引下载**：每个 (date, cycle, hour) 对应的 `.idx` 文件只有 ~30KB，记录每个变量的字节位置
2. **变量匹配**：在索引中查找指定变量名和层次，获取字节偏移量
3. **范围合并**：相邻字节段自动合并为一个 Range 请求，减少 HTTP 往返
4. **切片下载**：用 HTTP `Range` 头只下载需要的字节，而非整个文件
5. **可视化**：用 `cfgrib` / `xarray` 读取数据，`cartopy` 绘制地图

## 效果对比

| 项目 | 传统下载 | 切片下载 |
|------|---------|---------|
| 单时次文件 | ~7 GB | ~1.9 MB |
| 5 个时次合计 | ~35 GB | ~9.5 MB |
| 带宽节省 | — | **99.97%** |

## 注意事项

- **数据保留期**：NOAA 公开 bucket `noaa-gfs-bdp-pds` 大约保留**最近 10 天**滚动数据；
  更早的历史归档需要从 [NOAA NCEI](https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast) 拉取，URL 结构不同（暂未支持，需要可联系扩展）。
- **网络抖动**：每个 HTTP 请求自动重试 3 次（指数退避 2s/4s/8s）。重试仍失败的单个时次
  会被记入 `output/logs/last_run.json` 的 `failed` 列表，**不会中断整批**。
- **续传**：每个已存在的 `gfs_fXXX.grib2` 在跑前会用 cfgrib 做轻量元数据校验
  （只读 GRIB 头，不读栅格数据）。校验通过 → 跳过；校验失败 → 删除并重下。
  所以**直接重跑同一条命令就是续传**，不需要手动清理。
- **优先级**：CLI 参数 > `date_range` / `cycles`（列表形式） > `date` / `cycle`（单值）。
