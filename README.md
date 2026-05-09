# gfsdown — GFS GRIB2 切片下载工具

从 NOAA 公开 S3 存储桶下载 GFS GRIB2 数据，通过 HTTP Range 请求实现变量级切片，
将数 GB 的预报文件缩减到 ~2MB，精确抓取你需要的数据。

## 环境要求

- Python 3.12+
- `eccodes` C 库：macOS 执行 `brew install eccodes`，Linux 执行 `conda install -c conda-forge eccodes`
- `uv` 包管理器

## 安装

```bash
uv sync
```

## 快速开始

### 1. 编辑配置

修改 `config.yaml`，设置日期、时次、变量和区域：

```yaml
date: "2026-05-01"        # 预报初始化日期
cycle: 12                 # 预报周期 (0, 6, 12, 18)
forecast_hours:
  start: 0                # 起始预报时次
  end: 24                 # 结束预报时次
  step: 6                 # 间隔
variables:
  - name: "UGRD"          # 变量名
    level: "10 m above ground"  # 层次
  - name: "VGRD"
    level: "10 m above ground"
region:                   # 地理区域 (可选，不填则下载全球)
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

### 2. 查看可用变量

```bash
# 列出 f006 时次所有变量 (区分多层/单层)
uv run main.py --list-vars --hour 6

# 查看某个变量的所有层次 (可直接复制到 config.yaml)
uv run main.py --var TMP --hour 6

# 查看所有变量时也可指定搜索某个变量名
uv run main.py -l -H 6
```

### 3. 运行

```bash
# 根据 config.yaml 下载数据并绘图
uv run main.py

# 快速演示 (下载 2026-05-01 12Z 的风速数据)
uv run main.py --demo

# 只下载不绘图
uv run main.py --no-plot
```

## 变量发现

工具提供了两级变量浏览：

| 命令 | 说明 |
|------|------|
| `--list-vars` | 分类展示所有变量，区分多层变量和单层变量 |
| `--var <变量名>` | 查看指定变量的所有层次，输出可直接粘到 yaml 中 |

### 示例输出

```
uv run main.py --var UGRD --hour 6

UGRD [58 levels]:
    - {name: "UGRD", level: "10 m above ground"}
    - {name: "UGRD", level: "100 m above ground"}
    - {name: "UGRD", level: "500 mb"}
    - {name: "UGRD", level: "850 mb"}
    ...
```

复制所需行到 `config.yaml` 的 `variables:` 下即可。

## 工作原理

```
config.yaml → 解析配置 → 获取 .idx 索引 → 找到变量字节偏移 → HTTP Range 下载 → 拼接 .grib2 → 可视化
```

1. **索引下载**：每个预报时次对应的 `.idx` 文件只有 ~30KB，记录着每个变量的字节位置
2. **变量匹配**：在索引中查找指定变量名和层次的记录，获取字节偏移量
3. **范围合并**：相邻的变量自动合并为一个 Range 请求，减少 HTTP 往返
4. **切片下载**：用 HTTP `Range` 头只下载需要的字节，而非整个文件
5. **可视化**：用 `cfgrib`/`xarray` 读取数据，`cartopy` 绘制地图

## 效果对比

| 项目 | 传统下载 | 切片下载 |
|------|---------|---------|
| 单时次文件 | ~7 GB | ~1.9 MB |
| 5 个时次合计 | ~35 GB | ~9.5 MB |
| 带宽节省 | — | **99.97%** |
