# OmniTranslate

**[English](README.md)**

> 适用于 Windows 的实时屏幕 OCR 与翻译工具。

OmniTranslate 可以捕获屏幕上的任意区域，通过 PaddleOCR 持续提取文字，
并使用流式大语言模型 (LLM) 进行实时翻译。

## 功能特性

- **区域选择** — 在屏幕上拖动矩形框定义捕获区域
- **实时 OCR** — 使用 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) PP-OCRv4/v3 持续提取文字（完全离线，支持 10+ 语言）
- **15 种翻译引擎** — 免费的 Google / Bing，DeepL、Azure、百度、有道、腾讯、彩云，以及 AI 大模型（DeepSeek、OpenAI、Gemini、通义千问、Kimi、智谱、任意 OpenAI 兼容 API），在 *Engine* 标签页中勾选
- **双模式**：
  - *阅读模式* — 一次性翻译，适合静态文本
  - *实时模式* — 上下文感知的增量翻译，适合实时字幕
- **语言选择** — 从 10+ 语言中选择源语言和目标语言
- **一键复制** — OCR 结果和翻译结果均可一键复制到剪贴板
- **可调间隔** — 控制 OCR 频率（500ms–3000ms）
- **全局快捷键** — `Ctrl+Shift+S` 随时触发区域选择
- **调试监视器** — 隐藏的管线检查器，可观察 OCR/LLM/显示状态（按 `F12` 打开）
- **暗色主题** — Catppuccin Mocha 风格

## 系统要求

- Windows 10 / 11
- Python 3.10+（在 3.13 上测试通过）
- 互联网连接（仅翻译需要；OCR 完全离线工作）
- 可选：DeepL / DeepSeek / OpenAI 或其他 OpenAI 兼容服务的 API 密钥

## 安装

```bash
git clone https://github.com/Edolance/OmniTranslate.git
cd OmniTranslate

# 在项目文件夹内创建 venv\ 并安装依赖
setup.bat
```

所有脚本都使用相对于项目文件夹的路径，项目可以克隆或移动到任意位置。也可以手动安装：

```bash
python -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

可选：提前下载 OCR 模型（否则首次使用时自动下载）：

```bash
.\venv\Scripts\python.exe scripts\download_models.py
```

### 翻译引擎

打开 **Engine** 标签页，勾选一个引擎，右侧会显示它需要填写的设置：

| 分组 | 引擎 | 需要 |
|---|---|---|
| 免费（无需 key） | Google 翻译 | 无（部分网络下可能被限流） |
| | 微软 Bing | 无（单次最多约 1000 字符） |
| 翻译 API | DeepL | API key（免费 `:fx` 或 Pro） |
| | 微软 Azure | key，区域可选 |
| | 百度翻译 | APP ID + 密钥 |
| | 有道智云 | 应用 ID + 应用密钥 |
| | 腾讯翻译君 | SecretId + SecretKey，区域可选 |
| | 彩云小译 | token |
| AI 大模型 | DeepSeek、OpenAI、Gemini、通义千问、Kimi、智谱 GLM | API key，模型可选 |
| | Custom | API key、Base URL、模型名（任意 OpenAI 兼容 API） |

API key 按用户保存在 Windows 注册表（`HKCU\Software\OmniTranslate`），不在程序文件夹里，
所以把文件夹发给别人不会泄露你的 key。实时模式的上下文保持（让已显示的译文保持稳定）只对
大模型引擎有效；Google 和 DeepL 每次都会重新翻译全文。

`.env` 文件（参考 `.env.example`）仍会在首次启动时被读取并导入到上述设置中，但已不再必需。

## 使用方法

```bash
run.bat
```

或在已激活 venv 的终端中：

```bash
python run.py
```

### 独立 exe

`build_exe.bat` 用 PyInstaller 把程序打包到 `dist\OmniTranslate\`（目标电脑不需要安装 Python）：

```bash
build_exe.bat
```

把整个 `dist\OmniTranslate\` 文件夹拷到任意位置，双击 `OmniTranslate.exe`，在 Engine 标签页选好引擎即可。
已下载的 OCR 模型打包在 `.paddleocr\` 中；运行日志写在同目录的 `omnitranslate.log`。

想在不打开界面的情况下检查打包结果，可以运行 `OmniTranslate.exe --selftest`：
它会对一张生成的图片做一次 OCR，并把结果写进日志。

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
| OCR | PaddleOCR (PaddlePaddle) | 离线 OCR — PP-OCRv4（中/英）、PP-OCRv5（泰语）、PP-OCRv3（其他语言） |
| 翻译 | openai (Python SDK) | 通过任意 OpenAI 兼容 API 进行流式 LLM 翻译 |
| 快捷键 | pynput | 全局键盘监听 |
| HTTP | requests | Google / DeepL 翻译请求 |
| 环境配置 | python-dotenv | 可选：导入旧的 `.env` 配置 |

## 配置项

所有运行时设置均通过 UI 控制：

| 设置 | 控件 | 范围 / 选项 |
|---|---|---|
| OCR 间隔 | 滑块 | 500ms – 3000ms |
| 源语言 | 下拉菜单 | 自动检测 + 10+ 语言 |
| 目标语言 | 下拉菜单 | 10+ 语言 |
| 翻译模式 | 单选按钮 | 阅读模式 / 实时模式 |
| 窗口位置 | 拖动 | 始终置顶 |

## 许可

本项目仅供个人/学习使用。
