# OmniTranslate

**[English](README.md)**

> 适用于 Windows 的实时屏幕 OCR 与翻译工具。

OmniTranslate 可以捕获屏幕上的任意区域，通过 PaddleOCR 持续提取文字，
并使用流式大语言模型 (LLM) 进行实时翻译——所有功能集中在一个紧凑的、
始终置顶的浮动窗口中，翻译文本无闪烁更新。

## 功能特性

- **区域选择** — 在屏幕上拖动矩形框定义捕获区域
- **实时 OCR** — 使用 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) PP-OCRv4/v3 持续提取文字（完全离线，支持 80+ 语言）
- **LLM 翻译** — 通过任意 OpenAI 兼容 API 进行流式翻译（默认：DeepSeek-chat）
- **双模式**：
  - *阅读模式* — 一次性翻译，适合静态文本
  - *实时模式* — 上下文感知的增量翻译，带防闪烁机制，适合实时字幕
- **无闪烁更新** — 三级合并算法确保翻译框不会闪烁跳动
- **语言选择** — 从 30+ 语言中选择源语言和目标语言
- **一键复制** — OCR 结果和翻译结果均可一键复制到剪贴板
- **可调间隔** — 控制 OCR 频率（500ms–3000ms）
- **全局快捷键** — `Ctrl+Shift+S` 随时触发区域选择
- **调试监视器** — 内置管线检查器，可观察 OCR/LLM/显示状态
- **暗色主题** — Catppuccin Mocha 风格

## 系统要求

- Windows 10 / 11
- Python 3.10+（在 3.13 上测试通过）
- 互联网连接（仅 LLM 翻译需要；OCR 完全离线工作）
- OpenAI 兼容的 API 密钥（如 DeepSeek、OpenAI 等）

## 安装

```bash
# 克隆/下载项目
cd e:\Projects\demo

# 创建虚拟环境
python -m venv venv
.\venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### API 配置

在项目根目录创建 `.env` 文件：

```env
OPENAI_API_KEY=你的API密钥
OPENAI_BASE_URL=https://api.deepseek.com   # 或任何 OpenAI 兼容端点
OPENAI_MODEL=deepseek-chat                  # 可选，默认为 deepseek-chat
```

## 使用方法

```bash
.\venv\Scripts\activate
python run.py
```

### 操作流程

1. 点击 **Select Region**（或按 `Ctrl+Shift+S`）
2. 在要捕获的文字上方拖动矩形框
3. 选择**源语言**和**目标语言**
4. 选择模式：**Reading**（静态文本）或 **Real-time**（实时字幕）
5. 点击 **Start** 开始实时 OCR + 翻译
6. 使用 **Copy** 按钮复制文字到剪贴板
7. 完成后点击 **Stop**

## 项目结构

```
omnitranslate/
├── app.py                # 应用入口，全局快捷键
├── capture/
│   ├── selector.py       # 透明覆盖层用于区域选择
│   └── grabber.py        # 基于 mss 的屏幕截图（每次调用创建新上下文）
├── ocr/
│   └── engine.py         # PaddleOCR PP-OCRv4/v3 封装，含行分组逻辑
├── translator/
│   └── engine.py         # OpenAI 兼容的流式 LLM 翻译引擎
└── ui/
    └── main_window.py    # 主窗口、工作线程、防闪烁逻辑
```

## 架构设计

### 各模块职责

| 模块 | 职责 |
|---|---|
| `omnitranslate.capture` | 屏幕区域选择（`RegionSelector`）和像素捕获（`ScreenGrabber`） |
| `omnitranslate.ocr` | 通过 PaddleOCR 从图像中提取文字（`OCREngine`） |
| `omnitranslate.translator` | 基于 LLM 的流式翻译，带上下文保持（`TranslationEngine`） |
| `omnitranslate.ui` | PyQt5 图形界面、多线程流水线、防闪烁逻辑（`MainWindow`） |
| `omnitranslate.app` | QApplication 启动引导和全局快捷键注册 |

### 数据流

```
用户选择区域
       ↓
  mss 截取区域（RGB 数组）       ← ThreadPoolExecutor
       ↓
  PaddleOCR 提取文字              ← ThreadPoolExecutor
       ↓
  文字有变化？── 否 → 跳过
       ↓ 是
  背压 + 节流阀控制
       ↓
  LLM 流式翻译                   ← QThread
       ↓
  三级合并写入显示：
    1. 完美前缀 → 追加尾部
    2. 发现重叠 → 定向编辑
    3. 无重叠   → 追加新段落
       ↓
  自动滚动至最新内容
```

### 线程模型

| 线程 | 组件 | 原因 |
|---|---|---|
| Qt 主线程 | UI 渲染、信号派发 | Qt 要求 GUI 在主线程 |
| `ThreadPoolExecutor` | OCR（截图 + 识别） | PaddlePaddle 在 QThread 中会段错误 |
| `QThread` | 翻译（LLM API 调用） | 网络 I/O 不能阻塞 UI |

### 内部依赖关系

```
app.py → ui.main_window → capture.selector
                         → capture.grabber
                         → ocr.engine
                         → translator.engine
```

`capture`、`ocr` 和 `translator` 三个包彼此完全独立。只有 `ui.main_window` 从这三个包中导入。

## 技术栈

| 组件 | 库 | 用途 |
|---|---|---|
| 图形界面 | PyQt5 | 窗口、覆盖层、控件、线程 |
| 屏幕截图 | mss | 快速截图，无磁盘 I/O |
| OCR | PaddleOCR (PaddlePaddle) | 离线 OCR — PP-OCRv4（中/英）、PP-OCRv3（其他语言） |
| 翻译 | openai (Python SDK) | 通过任意 OpenAI 兼容 API 进行流式 LLM 翻译 |
| 快捷键 | pynput | 全局键盘监听 |
| 环境配置 | python-dotenv | `.env` 文件加载 API 密钥 |

## 配置项

所有运行时设置均通过 UI 控制：

| 设置 | 控件 | 范围 / 选项 |
|---|---|---|
| OCR 间隔 | 滑块 | 500ms – 3000ms |
| 源语言 | 下拉菜单 | 自动检测 + 30+ 语言 |
| 目标语言 | 下拉菜单 | 30+ 语言 |
| 翻译模式 | 单选按钮 | 阅读模式 / 实时模式 |
| 窗口位置 | 拖动 | 始终置顶 |

## 许可

本项目仅供个人/学习使用。
