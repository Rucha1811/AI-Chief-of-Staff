import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "tr", "td", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "section", "article", "header", "footer", "blockquote", "pre", "hr",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self._space_pending = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self._space_pending = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self._space_pending = True

    def handle_data(self, data):
        if self.skip:
            return
        if self.parts and not self._space_pending and not self.parts[-1].endswith("\n"):
            self.parts.append(" ")
        self._space_pending = False
        self.parts.append(data.strip())


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    try:
        parser = _TextExtractor()
        parser.feed(raw)
        text = " ".join(p for p in parser.parts if p)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw).strip()


def clean_news_summary(summary: str, max_len: int = 200) -> str:
    text = html_to_text(summary)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
