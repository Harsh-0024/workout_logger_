"""
Turn whatever a note app sends (plain text, HTML, RTF) into plain workout text.

Apple Notes rich text arrives with its own line breaks (U+2028), non-breaking
spaces, bullet glyphs and sometimes as HTML or RTF. The parser only understands
plain lines, so everything is flattened to that before parsing.
"""
import html
import re
from html.parser import HTMLParser

_HTML_HINT = re.compile(r"<\s*(?:br|div|p|li|ul|ol|span|b|i|u|strong|em|h[1-6]|html|body|font|tt)\b[^>]*>", re.I)
_BLOCK_TAGS = {"br", "div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\ufffc"))
_SPACES = dict.fromkeys(map(ord, "\u00a0\u2007\u202f\u2009\u200a\t"), " ")
_LINE_BREAKS = re.compile(r"\r\n?|[\u2028\u2029\u0085\u000b\u000c]")
# List bullets and checklist boxes at the start of a line (plain "-" is left alone).
_BULLET = re.compile(r"^[ ]*(?:[•◦▪▫●○■□‣⁃∙·☐☑☒✓✔]\s*)+", re.M)


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(text: str) -> str:
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


_RTF_TOKEN = re.compile(r"\\([a-z]+)(-?\d+)? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)", re.I | re.S)
_RTF_SKIP_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer",
    "expandedcolortbl", "listtable", "listoverridetable", "generator", "themedata",
}


def _rtf_to_text(text: str) -> str:
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skipping, uc, pending_skip = False, 1, 0
    for word, arg, hexcode, symbol, brace, char in _RTF_TOKEN.findall(text):
        if brace == "{":
            stack.append((skipping, uc))
        elif brace == "}":
            skipping, uc = stack.pop() if stack else (False, 1)
        elif symbol:
            if symbol == "*":
                skipping = True
            elif symbol in "\\{}" and not skipping:
                out.append(symbol)
            elif symbol == "~" and not skipping:
                out.append(" ")
        elif word:
            word = word.lower()
            if word in _RTF_SKIP_DESTINATIONS:
                skipping = True
            elif skipping:
                continue
            elif word in ("par", "line", "row"):
                out.append("\n")
            elif word == "tab":
                out.append(" ")
            elif word == "uc":
                uc = int(arg or 1)
            elif word == "u" and arg:
                code = int(arg)
                out.append(chr(code + 65536 if code < 0 else code))
                pending_skip = uc
        elif hexcode:
            if pending_skip:
                pending_skip -= 1
            elif not skipping:
                out.append(bytes.fromhex(hexcode).decode("cp1252", errors="replace"))
        elif char:
            if pending_skip:
                pending_skip -= 1
            elif not skipping:
                out.append(char)
    return "".join(out)


def to_plain_text(raw) -> str:
    """Best-effort plain text from plain, HTML or RTF input."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw.decode("cp1252", errors="replace")
    text = str(raw or "")
    stripped = text.lstrip()
    if stripped.startswith("{\\rtf"):
        text = _rtf_to_text(stripped)
    elif _HTML_HINT.search(text):
        text = _html_to_text(text)
    else:
        text = html.unescape(text) if re.search(r"&(?:nbsp|amp|lt|gt|quot|#\d+);", text) else text

    text = _LINE_BREAKS.sub("\n", text)
    text = text.translate(_ZERO_WIDTH).translate(_SPACES)
    text = _BULLET.sub("", text)
    lines = [re.sub(r" {2,}", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()
