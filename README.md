# OmniTranslate

**[中文文档](README_ZH.md)**

> Real-time screen OCR and translation tool for Windows.

OmniTranslate captures any region on your screen, continuously extracts text
via PaddleOCR, and translates it in real-time using a streaming LLM — all
from a compact, always-on-top floating window with flicker-free updates.

## Features

- **Region Selection** — draw a rectangle on screen to define the capture area
- **Real-time OCR** — continuous text extraction using [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) PP-OCRv4/v3 (offline, 80+ languages)
- **LLM Translation** — streaming translation via any OpenAI-compatible API (default: DeepSeek-chat)
- **Two Modes**:
  - *Reading Mode* — one-shot translation, best for static text
  - *Real-time Mode* — context-aware incremental translation with flicker suppression, best for live subtitles
- **Flicker-Free Updates** — three-level merge algorithm ensures the translation box never flashes
- **Language Selection** — choose source and target languages from 30+ options
- **Copy to Clipboard** — one-click copy for both OCR and translated text
- **Adjustable Interval** — control OCR frequency (500ms–3000ms)
- **Global Hotkey** — `Ctrl+Shift+S` to trigger region selection from anywhere
- **Debug Monitor** — built-in pipeline inspector for OCR/LLM/display diagnostics
- **Dark Theme** — Catppuccin Mocha aesthetic

## Requirements

- Windows 10 / 11
- Python 3.10+ (tested on 3.13)
- Internet connection (for LLM translation; OCR works fully offline)
- An OpenAI-compatible API key (e.g. DeepSeek, OpenAI, etc.)

## Installation

```bash
# Clone / download the project
cd e:\Projects\demo

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### API Configuration

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.deepseek.com   # or any OpenAI-compatible endpoint
OPENAI_MODEL=deepseek-chat                  # optional, defaults to deepseek-chat
```

## Usage

```bash
.\venv\Scripts\activate
python run.py
```

### Workflow

1. Click **Select Region** (or press `Ctrl+Shift+S`)
2. Drag a rectangle over the text you want to capture
3. Choose **Source** and **Target** languages
4. Select mode: **Reading** (static text) or **Real-time** (live subtitles)
5. Click **Start** to begin real-time OCR + translation
6. Use **Copy** buttons to copy text to clipboard
7. Click **Stop** when done

## Project Structure

```
omnitranslate/
├── app.py                # Application entry point, global hotkey
├── capture/
│   ├── selector.py       # Transparent overlay for region selection
│   └── grabber.py        # mss-based screen capture (per-call context)
├── ocr/
│   └── engine.py         # PaddleOCR PP-OCRv4/v3 with line grouping
├── translator/
│   └── engine.py         # OpenAI-compatible streaming LLM translation
└── ui/
    └── main_window.py    # Main window, workers, flicker suppression logic
```

## Architecture

### Package Responsibilities

| Package | Role |
|---|---|
| `omnitranslate.capture` | Screen region selection (`RegionSelector`) and pixel capture (`ScreenGrabber`) |
| `omnitranslate.ocr` | Text extraction from images via PaddleOCR (`OCREngine`) |
| `omnitranslate.translator` | Streaming LLM translation with context preservation (`TranslationEngine`) |
| `omnitranslate.ui` | PyQt5 GUI, multi-threaded pipeline, flicker suppression (`MainWindow`) |
| `omnitranslate.app` | QApplication bootstrap and global hotkey registration |

### Data Flow

```
User selects region
       ↓
  mss captures region (RGB array)   ← ThreadPoolExecutor
       ↓
  PaddleOCR extracts text           ← ThreadPoolExecutor
       ↓
  Text changed?  ── No → skip
       ↓ Yes
  Backpressure + throttle gate
       ↓
  LLM streaming translation         ← QThread
       ↓
  Three-level merge into display:
    1. Perfect prefix → append tail
    2. Overlap found  → targeted edit
    3. No overlap     → append paragraph
       ↓
  Auto-scroll to latest content
```

### Threading Model

| Thread | Component | Reason |
|---|---|---|
| Qt Main Thread | UI rendering, signal dispatch | Qt requires GUI on main thread |
| `ThreadPoolExecutor` | OCR (ScreenGrabber + OCREngine) | PaddlePaddle segfaults in QThread |
| `QThread` | Translation (LLM API) | Network I/O must not block the UI |

### Internal Dependencies

```
app.py → ui.main_window → capture.selector
                         → capture.grabber
                         → ocr.engine
                         → translator.engine
```

The `capture`, `ocr`, and `translator` packages are fully independent of each
other. Only `ui.main_window` imports from all three.

## Technology Stack

| Component | Library | Purpose |
|---|---|---|
| GUI | PyQt5 | Window, overlay, widgets, threading |
| Screen Capture | mss | Fast screenshot without disk I/O |
| OCR | PaddleOCR (PaddlePaddle) | Offline OCR — PP-OCRv4 (zh/en), PP-OCRv3 (others) |
| Translation | openai (Python SDK) | Streaming LLM via any OpenAI-compatible API |
| Hotkey | pynput | Global keyboard listener |
| Env Config | python-dotenv | `.env` file loading for API keys |

## Configuration

All runtime settings are controlled via the UI:

| Setting | Control | Range / Options |
|---|---|---|
| OCR interval | Slider | 500ms – 3000ms |
| Source language | Dropdown | Auto-detect + 30+ languages |
| Target language | Dropdown | 30+ languages |
| Translation mode | Radio buttons | Reading / Real-time |
| Window position | Drag | Always on top |

## License

This project is for personal/educational use.
