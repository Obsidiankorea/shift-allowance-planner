"""In-app guide documents: Markdown files in 문서/ (Obsidian-compatible), rendered for the intro/help popups.

Supported beyond plain Markdown: `![[name]]` embeds another document, `[[name]]` / `[[name|label]]` links open
it in a new window (`doc:` links), and status tags such as `[공식 확인]` are coloured. Status tags must match
docs/LEGAL_EVIDENCE.md; do not upgrade '기존 검토 기록' or '미확정' items to confirmed law in these files.
"""
import re, sys
from pathlib import Path
from urllib.parse import quote, unquote
import markdown
from markdown.extensions.tables import TableExtension
from markdown.extensions.sane_lists import SaneListExtension
from markdown.extensions.fenced_code import FencedCodeExtension

TAGS={'[공식 확인]':'#107c41','[기존 검토 기록]':'#9a6700','[엑셀 재현]':'#5b6676','[사용자 제공 원문]':'#6b4fbb','[미확정]':'#c23b46'}
STYLE=('<style>h1{font-size:19px} h2{font-size:17px;margin:16px 0 4px} h3{font-size:14px;margin:10px 0 2px} li{margin-bottom:4px} '
       'table{border-collapse:collapse;margin:4px 0 8px} td{padding:4px 6px;border:1px solid #e1e6ed;vertical-align:top} '
       'th{padding:4px 6px;background:#edf3fa;border:1px solid #d8e0ea} blockquote{background:#f4f6fa;margin:6px 0;padding:6px 10px} '
       'pre{background:#f4f6fa;padding:8px} .muted{color:#5b6676}</style>')

def doc_dir():
    """문서/ next to the EXE (frozen build) or next to this source file."""
    return (Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent)/'문서'

def doc_path(name):return doc_dir()/f'{name}.md'

def _strip_front_matter(text):
    return re.sub(r'\A---\n.*?\n---\n','',text,flags=re.S)

def expand(name,seen=()):
    """Markdown of `name` with `![[...]]` embeds inlined; returns (text, list of source paths in order)."""
    path=doc_path(name)
    if name in seen:return f'> ⚠️ [[{name}]] 끼워넣기가 반복됩니다.',[]
    if not path.exists():return f'> ⚠️ 문서 [[{name}]]를 찾을 수 없습니다 ({path.parent} 폴더).',[]
    text=_strip_front_matter(path.read_text(encoding='utf-8'));sources=[path]
    def embed(m):
        inner,more=expand(m.group(1).split('|')[0].strip(),seen+(name,));sources.extend(more);return inner
    return re.sub(r'!\[\[([^\]]+)\]\]',embed,text),sources

def _wiki_links(text):
    """[[name]] / [[name|label]] (also `\\|` inside tables) → Markdown links to doc:name."""
    def link(m):
        target,_,label=m.group(1).replace('\\|','|').partition('|')
        return f'[{label.strip() or target.strip()}](doc:{quote(target.strip())})'
    return re.sub(r'\[\[([^\]]+)\]\]',link,text)

def _colour_tags(html):
    for tag,colour in TAGS.items():html=html.replace(tag,f'<span style="color:{colour}">{tag}</span>')
    return html

def render(name):
    """(HTML ready for QTextBrowser, source paths). Missing documents render as a warning, not an exception."""
    text,sources=expand(name)
    body=markdown.markdown(_wiki_links(text),extensions=[TableExtension(),SaneListExtension(),FencedCodeExtension()])
    return STYLE+_colour_tags(body),sources

def doc_target(url_path):
    """Document name from a `doc:` link path."""
    return unquote(url_path)
