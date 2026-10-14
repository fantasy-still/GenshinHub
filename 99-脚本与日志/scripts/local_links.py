"""Local Markdown destinations; conservative repairs never change link labels/prose.

Handles spaces, balanced parentheses, percent escapes and this library's :line suffix.
This is a local-file checker, not a complete Markdown renderer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
import os
import re
from urllib.parse import unquote, quote

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_DIRS = ["10-剧情", "20-世界观与设定", "30-人物档案", "40-文献与书籍",
                "50-图鉴资料", "60-活动与版本", "70-索引与检索", "90-其他内容"]


@dataclass(frozen=True)
class Link:
    start: int
    end: int
    url: str
    label: str


def links(text: str):
    """Yield only destination spans, leaving all other characters untouched."""
    cursor = 0
    while True:
        pos = text.find("](", cursor)
        if pos < 0:
            return
        start = pos + 2
        if start >= len(text):
            return
        label_start = text.rfind("[", text.rfind("\n", 0, pos) + 1, pos)
        label = text[label_start + 1:pos] if label_start >= 0 else ""
        if text[start] == "<":
            stop = text.find(">", start + 1)
            if stop < 0 or text[stop + 1:stop + 2] != ")":
                cursor = start
                continue
            yield Link(start + 1, stop, text[start + 1:stop], label)
            cursor = stop + 2
            continue
        depth = 1
        stop = start
        while stop < len(text) and text[stop] not in "\r\n":
            if text[stop] == "\\":
                stop += 2
                continue
            if text[stop] == "(":
                depth += 1
            elif text[stop] == ")":
                depth -= 1
                if depth == 0:
                    break
            stop += 1
        if depth != 0:
            cursor = start
            continue
        value = text[start:stop]
        # Optional quoted Markdown title is not part of a file name.
        title = re.search(r'\s+"[^"\n]*"$', value)
        end = start + (title.start() if title else len(value))
        if end > start:
            yield Link(start, end, text[start:end], label)
        cursor = stop + 1


def local_parts(url: str):
    scheme = re.match(r"^([a-zA-Z][\w+.-]*):", url)
    filename_line = (scheme and scheme.group(1).endswith((".md", ".txt", ".wiki"))
                     and re.fullmatch(r"\d+(?:#.*)?", url[scheme.end():]))
    if not url or url.startswith("#") or (scheme and not filename_line) or url.startswith("//"):
        return None
    path, sep, fragment = url.partition("#")
    path = unquote(path).replace("\\", "/")
    line = re.search(r":(\d+)$", path)
    suffix = line.group(0) if line else ""
    if line:
        path = path[:line.start()]
    return path, suffix, (sep + fragment if sep else "")


def target(document: Path, url: str):
    parts = local_parts(url)
    return Path(os.path.abspath(document.parent / parts[0])) if parts else None


@lru_cache(maxsize=None)
def exists(path: Path):
    return path.exists()


@lru_cache(maxsize=None)
def is_file(path: Path):
    return path.is_file()


def relative_url(document: Path, destination: Path, original: str = ""):
    parts = local_parts(original)
    suffix, fragment = (parts[1], parts[2]) if parts else ("", "")
    rel = os.path.relpath(destination, document.parent).replace(os.sep, "/")
    # Spaces and ASCII parentheses require escaping in inline Markdown URLs.
    escaped = re.sub(r'[%\s()#?<>"\[\]]', lambda m: quote(m.group(0), safe=""), rel)
    return escaped + suffix + fragment


class Resolver:
    def __init__(self, paths):
        self.suffixes = {}
        self.known = set()
        for p in paths:
            p = Path(os.path.abspath(p))
            if not p.is_relative_to(ROOT):
                continue
            self.known.add(p)
            pieces = p.relative_to(ROOT).parts
            for n in range(2, len(pieces) + 1):
                self.suffixes.setdefault("/".join(pieces[-n:]), set()).add(p)

    def resolve(self, document: Path, url: str, source: Path | None = None):
        parts = local_parts(url)
        if not parts:
            return None
        path = parts[0]
        if source:
            from_source = Path(os.path.abspath(source.parent / path))
            if from_source.is_relative_to(ROOT) and (from_source in self.known or exists(from_source)):
                return from_source, "摘录来源中的相对路径"
        clean = re.sub(r"^(?:\./|\.\./)+", "", path)
        if clean.split("/", 1)[0] in LIBRARY_DIRS + ["80-原始归档", "99-脚本与日志"]:
            root_path = Path(os.path.abspath(ROOT / clean))
            if root_path.is_relative_to(ROOT) and (root_path in self.known or exists(root_path)):
                return root_path, "明确的库内完整路径"
        candidates = self.suffixes.get(clean, set())
        if len(candidates) == 1:
            return next(iter(candidates)), "完整路径后缀唯一匹配"
        return None


def repair_links(text: str, document: Path, resolver: Resolver, redirects=None):
    """Only repair nonexistent destinations or an explicitly identified header collision."""
    redirects = redirects or {}
    headings = []
    for m in re.finditer(r"(?m)^#{2,6} ([^\r\n]+)", text):
        p = ROOT / m.group(1).strip()
        if p.is_relative_to(ROOT) and is_file(p):
            headings.append((m.start(), p))
    edits, changes, unresolved = [], [], []
    for link in links(text):
        old_target = target(document, link.url)
        if old_target is None:
            continue
        redirect = redirects.get((old_target, link.label))
        if redirect:
            resolved = redirect, "按原始页面标题拆分被覆盖的标题页"
        elif old_target in resolver.known or exists(old_target):
            continue
        else:
            line_start = text.rfind("\n", 0, link.start) + 1
            is_excerpt = re.match(r"- L\d+｜", text[line_start:])
            source = next((p for pos, p in reversed(headings) if pos < link.start), None) if is_excerpt else None
            resolved = resolver.resolve(document, link.url, source)
        if not resolved:
            unresolved.append({"url": link.url, "line": text.count("\n", 0, link.start) + 1})
            continue
        destination, reason = resolved
        new = relative_url(document, destination, link.url)
        edits.append((link.start, link.end, new))
        changes.append({"old": link.url, "new": new, "target": destination.relative_to(ROOT).as_posix(), "reason": reason})
    out = text
    for start, end, new in reversed(edits):
        out = out[:start] + new + out[end:]
    return out, changes, unresolved


def mask_destinations(text: str):
    for link in reversed(list(links(text))):
        text = text[:link.start] + "<DESTINATION>" + text[link.end:]
    return text
