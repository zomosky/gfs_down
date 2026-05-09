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
  enabled: true
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
uv run main.py --date 2025-01-15 --cycle 12 --list-hours
# 输出会自动检测步长分组，例如：
#   f000 - f120  step 1h     (121 files)
#   f123 - f384  step 3h     (88 files)
#   Max lead time: f384 (16 days 0h ahead)
```

### 3. 运行下载

```bash
# 根据 config.yaml 下载数据（含绘图）
uv run main.py

# 快速演示 (下载 config 中 date 的风速数据)
uv run main.py --demo

# 只下载不绘图
uv run main.py --no-plot
```

### 4. CLI 轻量覆盖（不改 config.yaml）

CLI 参数优先级高于 `config.yaml`，未指定的字段仍由配置文件提供。

```bash
# 单日 + 单轮次
uv run main.py --date 2026-05-01 --cycle 12

# 历史区间下载（指定起止日期）
uv run main.py --date-range 2026-01-01:2026-02-01 --cycle 12

# 区间 + 步长（每 7 天采一次）
uv run main.py --date-range 2026-01-01:2026-04-01:7

# 多轮次（同一天下 4 个轮次）
uv run main.py --cycles 0,6,12,18

# 下载所有可用预报时次（每个 init 自动从 S3 LIST 发现）
uv run main.py --date 2025-01-15 --cycle 12 --all-hours

# 1 个月 × 4 轮次 × 默认预报时次，不绘图
uv run main.py --date-range 2026-01-01:2026-02-01 --cycles 0,6,12,18 --no-plot
```

互斥规则：`--date` 与 `--date-range` 二选一；`--cycle` 与 `--cycles` 二选一。
`--all-hours` 会覆盖 `config.yaml` 里的 `forecast_hours`。

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
$ uv run main.py --date 2025-01-15 --cycle 12 --list-hours

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
- **网络抖动**：S3 偶发 SSL EOF 错误已由内置重试（3 次指数退避）自动处理。
- **优先级**：CLI 参数 > `date_range` / `cycles`（列表形式） > `date` / `cycle`（单值）。
