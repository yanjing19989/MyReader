# MyReader

MyReader 是一个面向本机单用户的相册目录与封面浏览器。它可以扫描本地文件夹和 ZIP，将包含图片的路径登记为相册，并根据路径关系生成目录树和封面缩略图。

## 功能

- 递归或非递归扫描多个本地路径
- 支持文件夹及 ZIP 相册
- 支持 JPG、JPEG、PNG、WebP、GIF
- 相册卡片、目录树、面包屑、搜索和排序
- 竖向 3:4 与横向 3:2 封面布局
- 自动生成并缓存 WebP 缩略图
- 使用内部图片、下级相册或上传图片设置封面
- 刷新磁盘状态并移除不存在的相册
- 调用服务器本机的 LocalViewer 打开相册

## 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- npm
- LocalViewer 可选，默认配置为 Windows 下的 BandiView

## 安装

安装后端依赖：

```powershell
uv sync
```

安装前端依赖：

```powershell
cd frontend
npm install
```

## 开发启动

启动 FastAPI：

```powershell
uv run uvicorn app.main:app --reload
```

在另一个终端启动 Vite：

```powershell
cd frontend
npm run dev
```

浏览器访问 `http://localhost:5173`。Vite 会将 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 生产启动

先构建前端：

```powershell
cd frontend
npm run build
cd ..
```

再启动后端：

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

FastAPI 会自动托管 `frontend/dist`，浏览器访问 `http://127.0.0.1:8000`。

## 配置

通过环境变量覆盖默认配置：

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MYREADER_DATA_DIR` | `<项目目录>/data` | 数据根目录 |
| `MYREADER_DB_PATH` | `<数据目录>/myreader.db` | SQLite 数据库路径 |
| `MYREADER_CACHE_DIR` | `<数据目录>/cache` | 缩略图缓存目录 |
| `MYREADER_VIEWER_PATH` | `D:\myprogram\BandiView\BandiView.exe` | LocalViewer 可执行文件路径 |

PowerShell 示例：

```powershell
$env:MYREADER_VIEWER_PATH = 'C:\Program Files\BandiView\BandiView.exe'
uv run uvicorn app.main:app --reload
```

## 操作

- 单击目录或 ZIP 卡片：预览封面原图
- 双击目录卡片：进入下一级
- 双击 ZIP 卡片：使用 LocalViewer 打开
- 预览时按 `←` / `→`：切换当前层相册封面
- 预览时按 `Enter`：执行当前卡片的双击操作
- 按 `Escape` 或点击封面外区域：关闭预览、菜单或侧栏
- 右键相册卡片或目录树节点：打开 LocalViewer 或设置封面

## 数据目录

运行时会自动创建：

```text
data/
├── myreader.db
├── cache/
│   └── thumbs/
└── covers/
```

`data/`、虚拟环境、前端依赖和构建产物默认不会提交到 Git。
