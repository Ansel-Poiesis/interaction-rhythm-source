"""Validate a letter's evidence binding and render matching local artifacts.

Markdown is the single source: report.md is the letter itself; report.html is
rendered from the same markdown via markdown-it-py (hard breaks on, raw HTML
escaped) so line-broken letters keep their rhythm in both outputs.
"""
from pathlib import Path
import argparse
import html
import json
import sys

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from interaction_rhythm.report_evidence import validate_evidence, validate_letter

_MD = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table").enable("strikethrough")

LETTER_CSS = (
    "body{margin:0;background:#f7f7f2;color:#26372c;font:17px/1.95 'Microsoft YaHei',sans-serif}"
    "article{max-width:720px;margin:40px auto;padding:44px 38px;background:white;border-top:5px solid #34775b}"
    "h1{font-size:26px;line-height:1.5;margin:0 0 30px}"
    "p{margin:0 0 23px}"
    "hr{border:0;border-top:1px solid #e1e7df;margin:30px 0}"
    "table{border-collapse:collapse;width:100%;font-size:14px}"
    "th,td{border:1px solid #e1e7df;padding:6px 9px;text-align:left}"
    "details{font-size:13px;color:#637267;border-top:1px solid #e1e7df;padding-top:18px}"
    "a{color:#34775b}"
    "@media(max-width:600px){article{margin:0;padding:27px 22px}body{font-size:16px}}"
)


def render(packet, letter):
    validate_evidence(packet)
    validate_letter(packet, letter)
    paragraphs = [letter["salutation"], *[p["text"] for p in letter["paragraphs"]], letter["closing"]]
    paragraphs = [p for p in paragraphs if p]
    markdown = f'# {letter["subject"]}\n\n' + "\n\n".join(paragraphs)
    markdown += "\n\n---\n\n" + letter["scope_note"] + "\n"
    body = _MD.render("\n\n".join(paragraphs))
    scope = _MD.render(letter["scope_note"])
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">')
    page += "<title>" + html.escape(letter["subject"]) + "</title><style>" + LETTER_CSS + "</style>"
    page += "<article><h1>" + html.escape(letter["subject"]) + "</h1>" + body
    page += ('<details open><summary>记录范围与证据</summary>' + scope +
             '<p><a href="evidence.json">本机证据包</a> · <a href="letter.json">段落事实与方法引用</a></p></details>'
             "</article></html>")
    return markdown, page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    packet = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    letter = json.loads((directory / "letter.json").read_text(encoding="utf-8"))
    markdown, page = render(packet, letter)
    (directory / "report.md").write_text(markdown, encoding="utf-8")
    (directory / "report.html").write_text(page, encoding="utf-8")
    print(json.dumps({"validated": True, "markdown": str(directory / "report.md"),
                      "html": str(directory / "report.html"), "delivery": "none"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
