"""
Render README.md / README_ZH.md to README.pdf / README_ZH.pdf.

Converts the Markdown to styled HTML, then prints it with headless
Microsoft Edge (preinstalled on Windows 10/11).

Usage (from the project folder):
    venv\\Scripts\\python.exe scripts\\make_pdf.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

from markdown_it import MarkdownIt

_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

_CSS = """
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 11pt;
       line-height: 1.55; color: #1f2328; max-width: 860px; margin: 0 auto; }
h1 { border-bottom: 2px solid #d0d7de; padding-bottom: .3em; }
h2 { border-bottom: 1px solid #d0d7de; padding-bottom: .2em; margin-top: 1.6em; }
code { font-family: "Cascadia Code", Consolas, monospace; font-size: 9.5pt;
       background: #f2f4f6; padding: .1em .3em; border-radius: 4px; }
pre { background: #f6f8fa; padding: 10px 12px; border-radius: 6px;
      white-space: pre-wrap; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: .8em 0; page-break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 5px 10px; text-align: left; }
th { background: #f6f8fa; }
blockquote { color: #59636e; border-left: 4px solid #d0d7de; margin: 0; padding-left: 1em; }
a { color: #0969da; text-decoration: none; }
"""


def render(md_name: str, pdf_name: str, edge: str):
    md_path = os.path.join(_project_dir, md_name)
    pdf_path = os.path.join(_project_dir, pdf_name)
    with open(md_path, encoding="utf-8") as f:
        body = MarkdownIt("commonmark").enable("table").render(f.read())
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{_CSS}</style></head><body>{body}</body></html>")

    with tempfile.TemporaryDirectory() as tmp:
        html_path = os.path.join(tmp, "readme.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={os.path.join(tmp, 'profile')}",
             f"--print-to-pdf={pdf_path}", "file:///" + html_path.replace("\\", "/")],
            check=True, timeout=120,
        )
    print(f"{md_name} -> {pdf_name}")


def main():
    edge = next((p for p in _EDGE_CANDIDATES if os.path.exists(p)), None) \
        or shutil.which("msedge")
    if not edge:
        sys.exit("Microsoft Edge not found; it is needed to print the PDF.")
    render("README.md", "README.pdf", edge)
    render("README_ZH.md", "README_ZH.pdf", edge)


if __name__ == "__main__":
    main()
