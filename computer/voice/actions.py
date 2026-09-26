"""
On-the-spot tasks for JARVIS's voice: open a document (or an app) and report
on it, report on the document that's on screen right now, and preview a web
page -- open it in the browser and summarize what's there.

Each action does the thing and returns a compact context block; the local
model then reports on it out loud (wake_listener.speak_streamed). Stdlib
only. Text extraction: plain text directly, Word/PowerPoint/Excel by
unzipping their XML, PDF through macOS's PDFKit (via JXA), and RTF / .doc /
HTML / ODT through macOS's textutil.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import gzip
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import skills

EXCERPT_CHARS = 1500
# The last thing opened or read, so "summarize it" / "what's in it" work next.
last_target: dict = {}  # ~400 tokens -- the model reads ~25 tokens/s on this Mac
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".py", ".js", ".ts", ".html", ".htm", ".log", ".yaml", ".yml", ".xml"}
DOC_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".pptx", ".xlsx", ".rtf", ".doc", ".odt", ".pages", ".key", ".numbers"}
_STOP = {"the", "a", "an", "my", "me", "to", "of", "for", "please", "open", "up", "pull", "show", "find", "and",
         "document", "doc", "file", "report", "on", "it", "its", "what", "about", "tell", "can", "you", "jarvis",
         "hey", "is", "in", "latest", "newest", "recent", "last", "called", "named", "that", "this", "summarize", "read"}


def _run(args: list[str], timeout: float = 15) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _clean(text: str, limit: int = EXCERPT_CHARS) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# --- text extraction -------------------------------------------------------------

def _zip_xml_text(path: Path, members: str) -> str:
    """Text from an Office Open XML file (docx/pptx/xlsx)."""
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if re.fullmatch(members, n))
        parts = []
        for name in names:
            xml = z.read(name).decode("utf-8", "replace")
            xml = re.sub(r"</w:p>|</a:p>|</si>|</row>", "\n", xml)
            parts.append(html.unescape(re.sub(r"<[^>]+>", " ", xml)))
        return "\n".join(parts)


_JXA_PDF = """
ObjC.import('PDFKit');
function run(argv) {
  const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!doc || doc.isNil()) return '';
  const pages = Math.min(doc.pageCount, 6);
  let out = '';
  for (let i = 0; i < pages; i++) { const p = doc.pageAtIndex(i); out += ObjC.unwrap(p.string) + '\\n'; }
  return out;
}
"""


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(errors="replace")[:20000]
            return re.sub(r"<[^>]+>", " ", text) if suffix in (".html", ".htm", ".xml") else text
        if suffix == ".docx":
            return _zip_xml_text(path, r"word/document\.xml")
        if suffix == ".pptx":
            return _zip_xml_text(path, r"ppt/slides/slide\d+\.xml")
        if suffix == ".xlsx":
            return _zip_xml_text(path, r"xl/(sharedStrings|worksheets/sheet\d+)\.xml")
        if suffix == ".pdf":
            return _run(["osascript", "-l", "JavaScript", "-e", _JXA_PDF, str(path)], timeout=20)
        if suffix in (".rtf", ".doc", ".odt"):
            return _run(["textutil", "-convert", "txt", "-stdout", str(path)])
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""
    return ""  # .pages / .key / .numbers: opened, but not readable here


# --- finding things ---------------------------------------------------------------

def _search_roots() -> list[Path]:
    src = skills.load_sources()
    raw = list(src.get("recent_files_folders", []))
    for proj in src.get("projects", []):
        raw += proj.get("paths", [])
    roots, seen = [], set()
    for r in raw:
        p = Path(os.path.expanduser(r))
        if p.exists() and str(p) not in seen:
            seen.add(str(p))
            roots.append(p)
    return roots


def _keywords(query: str) -> list[str]:
    words = re.findall(r"[\w'-]+", query.lower())
    return [w for w in words if w not in _STOP and len(w) > 1]


def find_document(query: str) -> Path | None:
    """Best Spotlight match for the words in query: every word in the file
    name, among documents in the configured folders; newest wins."""
    words = _keywords(query)
    if not words:
        return None
    roots = _search_roots()
    # One Spotlight query over the home folder (~2s; one per folder took ~15s),
    # kept to the configured folders.
    clause = " && ".join(f'kMDItemFSName == "*{w}*"cd' for w in words[:5])
    out = _run(["mdfind", "-onlyin", os.path.expanduser("~"), clause], timeout=10)
    hits = [Path(l) for l in out.splitlines() if l.strip()]
    hits = [h for h in hits if any(root in h.parents for root in roots)]
    # Spotlight skips hidden folders (e.g. a project under a dot-folder):
    # walk those roots by hand, shallowly.
    for root in roots:
        if any(part.startswith(".") for part in root.parts):
            hits += _walk_find(root, words)
    docs = [h for h in hits if h.suffix.lower() in DOC_SUFFIXES and h.is_file()]
    if not docs:
        return None

    def score(p: Path) -> tuple:
        name = p.stem.lower()
        exact = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", name))
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        return (exact, mtime)

    return max(set(docs), key=score)


def _walk_find(root: Path, words: list[str], max_depth: int = 4, max_files: int = 20000) -> list[Path]:
    found, seen = [], 0
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in skills._SKIP_DIRS
                       and d not in ("models", "engines", "cache", "logs") and len(Path(dirpath).parts) - base < max_depth]
        for fn in filenames:
            seen += 1
            name = fn.lower()
            if all(w in name for w in words):
                found.append(Path(dirpath) / fn)
        if seen > max_files:
            break
    return found


def find_app(query: str) -> str | None:
    words = _keywords(query)
    if not words:
        return None
    name = " ".join(words)
    for folder in ("/Applications", "/System/Applications", os.path.expanduser("~/Applications"),
                   "/System/Applications/Utilities"):
        for app in Path(folder).glob("*.app"):
            if app.stem.lower() == name or (len(words) == 1 and app.stem.lower().split()[0] == name):
                return str(app)
    return None


def _ago(ts: float) -> str:
    delta = time.time() - ts
    if delta < 3600:
        return f"{max(1, int(delta // 60))} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    return f"{int(delta // 86400)} days ago"


def _report_block(path: Path, verb: str) -> str:
    text = _clean(extract_text(path))
    try:
        when = _ago(path.stat().st_mtime)
    except OSError:
        when = "unknown"
    head = f"{verb} '{path.name}' (in {path.parent.name}, last changed {when})."
    if not text:
        return head + " Its text can't be read here, so describe it only by name. Task: say you opened it."
    return (f"{head} Content excerpt: {text}\nTask: in two or three spoken sentences, say what this "
            "document is and the main points in it. Only use the excerpt.")


# --- actions ------------------------------------------------------------------------

_OPEN_TARGET = re.compile(r"\b(?:open|pull up|show me|bring up|launch|start)\s+(?:up\s+)?(?:the\s+|my\s+)?(.+)", re.I)


def open_document(text: str, step: skills.StepFn = skills._noop_step) -> str:
    m = _OPEN_TARGET.search(text)
    target = (m.group(1) if m else text).strip(" .?!")
    step("Finding it", "running", target[:40])
    app = find_app(target) if len(_keywords(target)) <= 2 else None
    if app and not re.search(r"\b(document|doc|file|pdf|report|cv|resume|letter|notes?|sheet|presentation)\b", target, re.I):
        subprocess.Popen(["open", app])
        step("Finding it", "done", f"opened {Path(app).stem}")
        return f"Opened the app {Path(app).stem}. Task: confirm in a few words."
    path = find_document(target)
    if not path:
        step("Finding it", "failed", "no match")
        return f"No document matching '{target}' in their folders. Task: say you couldn't find it and ask for another name."
    subprocess.Popen(["open", str(path)])
    last_target.clear(); last_target.update(kind="file", path=str(path))
    step("Finding it", "done", path.name[:40])
    step("Reading it", "running", "")
    block = _report_block(path, "Opened")
    step("Reading it", "done", "")
    return block


_FRONT_DOC = """
tell application "System Events"
  set p to first application process whose frontmost is true
  try
    return value of attribute "AXDocument" of front window of p
  end try
end tell
return ""
"""


def front_document(step: skills.StepFn = skills._noop_step) -> str:
    """Report on the document in the frontmost window (Preview, Word, TextEdit,
    VS Code... anything that exposes its file to Accessibility)."""
    step("Document on screen", "running", "")
    url = _run(["osascript", "-e", _FRONT_DOC], timeout=6).strip()
    if url.startswith("http"):  # a browser tab: report on the page itself
        step("Document on screen", "done", urllib.parse.urlparse(url).netloc[:40])
        try:
            title, body = _page_text(url)
        except Exception:  # noqa: BLE001
            return "Couldn't read the page on screen. Task: say so."
        return (f"The page on screen is '{title}'. Page excerpt: {body}\nTask: in two or three spoken "
                "sentences, say what the page is and its key points. Only use the excerpt.")
    path = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)) if url.startswith("file://") else None
    if (not path or not path.is_file()) and not url.startswith("http") and last_target:
        # Nothing readable in front: "it" is the last thing JARVIS opened.
        if last_target.get("kind") == "file" and Path(last_target["path"]).is_file():
            step("Document on screen", "done", Path(last_target["path"]).name[:40])
            return _report_block(Path(last_target["path"]), "The document you just opened is")
        if last_target.get("kind") == "url":
            url = last_target["url"]
            try:
                title, body = _page_text(url)
                step("Document on screen", "done", title[:40])
                return (f"The page you just opened is '{title}'. Page excerpt: {body}\nTask: in two or three spoken "
                        "sentences, say what the page is and its key points. Only use the excerpt.")
            except Exception:  # noqa: BLE001
                pass
    if not path or not path.is_file():
        step("Document on screen", "failed", "window doesn't expose a file")
        return ("Couldn't tell which document is on screen (that app doesn't share its file). "
                "Task: say so and suggest they ask you to open it by name.")
    step("Document on screen", "done", path.name[:40])
    return _report_block(path, "The document on screen is")


# --- web preview ------------------------------------------------------------------------

_URLISH = re.compile(r"\b((?:https?://)?(?:[\w-]+\.)+(?:com|de|org|net|io|ai|co|uk|eu|app|dev|info|tv|me)(?:/\S*)?)", re.I)
_PREVIEW_FILLER = re.compile(r"^(hey |ok |okay )?(jarvis[, ]*)?(can you |could you |please )*"
                             r"(preview|look up|look at|open|go to|pull up|show me|check|search( the web| online)? for)\s+(the\s+)?"
                             r"(website|site|page|web ?page\s+)?(for|of|about)?\s*", re.I)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": skills.USER_AGENT, "Accept-Encoding": "identity",
                                               "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
    if data[:2] == b"\x1f\x8b":  # gzipped anyway
        data = gzip.decompress(data)
    return data.decode(charset, "replace")


def _page_text(url: str) -> tuple[str, str]:
    raw = _fetch(url)
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    paras = re.findall(r"(?is)<(?:p|h1|h2|h3|li)[^>]*>(.*?)</(?:p|h1|h2|h3|li)>", body)
    text = "\n".join(skills._strip_tags(p) for p in paras if len(skills._strip_tags(p)) > 30)
    return (skills._strip_tags(title.group(1)) if title else url), _clean(text or skills._strip_tags(body))


def _top_result_url(query: str) -> str | None:
    page = skills._get("https://html.duckduckgo.com/html/", timeout=10, form={"q": query}).decode("utf-8", "replace")
    for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', page):
        href = html.unescape(href)
        if "uddg=" in href:  # DuckDuckGo redirect wrapper
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query)["uddg"][0])
        if href.startswith("http") and "duckduckgo.com" not in href:
            return href
    return None


def web_preview(text: str, step: skills.StepFn = skills._noop_step) -> str:
    """Open the page (named, or the top search result) in the browser and
    summarize it."""
    m = _URLISH.search(text)
    query = _PREVIEW_FILLER.sub("", text).strip(" ?.!") or text
    step("Finding the page", "running", query[:40])
    try:
        url = (m.group(1) if m else None) or _top_result_url(query)
        if url and not url.startswith("http"):
            url = "https://" + url
    except Exception as exc:  # noqa: BLE001
        step("Finding the page", "failed", str(exc)[:50])
        return "The web search failed. Task: say you couldn't reach the internet just now."
    if not url:
        step("Finding the page", "failed", "no results")
        return f"No web results for '{query}'. Task: say so."
    subprocess.Popen(["open", url])
    last_target.clear(); last_target.update(kind="url", url=url)
    host = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    step("Finding the page", "done", host[:40])
    step("Reading the page", "running", "")
    try:
        title, body = _page_text(url)
    except Exception as exc:  # noqa: BLE001
        step("Reading the page", "failed", str(exc)[:50])
        return f"Opened {host} in the browser but couldn't read it. Task: say it's open on screen."
    step("Reading the page", "done", title[:40])
    return (f"Opened {host} in the browser: '{title}'. Page excerpt: {body}\nTask: in two or three spoken "
            "sentences, say what the page is and its key points. Only use the excerpt. Never read out the URL.")


if __name__ == "__main__":  # quick manual test: python3 actions.py "open my cv"
    import sys
    q = " ".join(sys.argv[1:]) or "open the readme"
    fn = web_preview if re.search(r"preview|website|\.com|online", q) else open_document
    print(json.dumps(fn(q, lambda *a: print("  step:", *a)), ensure_ascii=False)[:2500])
