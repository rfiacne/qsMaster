"""Fetch SSE guide articles from sse.com.cn and save as HTML files for ingestion."""

import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://www.sse.com.cn"
LIST_URL = f"{BASE_URL}/lawandrules/guide/latest/"
OUT_DIR = Path("G:/qsMaster/data/sse_guide_articles")


class ArticleLinkExtractor(HTMLParser):
    """Extract article links from the listing page."""

    def __init__(self):
        super().__init__()
        self.articles = []
        self._in_a = False
        self._href = None
        self._title = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href", "")
            title = attrs_dict.get("title", "")
            if "/lawandrules/guide/" in href and href.endswith(".shtml") and title.strip():
                self._in_a = True
                self._href = href
                self._title = title.strip()

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self._in_a = False
            url = self._href if self._href.startswith("http") else BASE_URL + self._href
            self.articles.append((self._title, url))


class ArticleBodyExtractor(HTMLParser):
    """Extract main article body from article page."""

    def __init__(self):
        super().__init__()
        self.body_text = []
        self._capture = False
        self._depth = 0
        self._title = ""
        self._in_title = False
        self._skip = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        # Article title
        if tag in ("h1", "h2") and "article" in cls.lower() or "title" in cls.lower():
            self._in_title = True
        # Main content area - SSE uses various class names
        if tag == "div" and any(
            k in cls
            for k in [
                "article_content",
                "article-content",
                "TRS_Editor",
                "content",
                "main-content",
                "zoom",
            ]
        ):
            self._capture = True
            self._depth = 1
            return
        if self._capture and tag == "div":
            self._depth += 1
        if self._capture:
            if tag == "p":
                self.body_text.append("\n")
            elif tag == "br":
                self.body_text.append("\n")
            elif tag in ("h1", "h2", "h3", "h4"):
                self.body_text.append("\n## ")
            elif tag == "li":
                self.body_text.append("\n- ")
            elif tag == "tr":
                self.body_text.append("\n")
            elif tag == "td" or tag == "th":
                self.body_text.append(" | ")

    def handle_endtag(self, tag):
        if self._in_title and tag in ("h1", "h2"):
            self._in_title = False
        if self._capture and tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._capture = False
        if self._capture and tag in ("h1", "h2", "h3", "h4"):
            self.body_text.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self._title += data
        if self._capture:
            stripped = data.strip()
            if stripped:
                self.body_text.append(stripped)


def fetch(url: str, retries: int = 3) -> bytes:
    """Fetch URL with retries."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def extract_articles_from_list(html_bytes: bytes) -> list[tuple[str, str]]:
    """Parse listing page to get article (title, url) pairs."""
    text = html_bytes.decode("utf-8", errors="replace")
    parser = ArticleLinkExtractor()
    parser.feed(text)
    # Deduplicate
    seen = set()
    result = []
    for title, url in parser.articles:
        if url not in seen:
            seen.add(url)
            result.append((title, url))
    return result


def extract_article_body(html_bytes: bytes) -> tuple[str, str]:
    """Extract title and body text from article page. Returns (title, markdown_text)."""
    text = html_bytes.decode("utf-8", errors="replace")
    parser = ArticleBodyExtractor()
    parser.feed(text)

    # Also try to get title from <title> tag
    title_match = re.search(r"<title>(.*?)</title>", text, re.DOTALL)
    title = parser._title.strip() or (title_match.group(1).strip() if title_match else "")

    body = " ".join(parser.body_text)
    # Clean up whitespace
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = re.sub(r" {2,}", " ", body)
    return title, body.strip()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch listing page
    print("Fetching listing page...")
    list_html = fetch(LIST_URL)
    articles = extract_articles_from_list(list_html)
    print(f"Found {len(articles)} articles")

    # Step 2: Download each article
    for i, (title, url) in enumerate(articles):
        # Generate filename from URL
        slug = url.rstrip("/").split("/")[-1].replace(".shtml", "").replace(".html", "")
        filename = f"{slug}.html"
        filepath = OUT_DIR / filename

        if filepath.exists():
            print(f"[{i + 1}/{len(articles)}] SKIP (exists): {filename}")
            continue

        try:
            print(f"[{i + 1}/{len(articles)}] Fetching: {title[:50]}...")
            article_html = fetch(url)
            art_title, art_body = extract_article_body(article_html)

            # Save as clean HTML with proper encoding
            display_title = art_title or title
            content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{display_title}</title>
</head>
<body>
<h1>{display_title}</h1>
<p><em>来源: 上海证券交易所 | URL: {url}</em></p>
<hr>
{art_body}
</body>
</html>"""
            filepath.write_text(content, encoding="utf-8")
            print(f"  -> Saved: {filename} ({len(art_body)} chars)")

        except Exception as e:
            print(f"  -> ERROR: {e}")

        time.sleep(0.5)  # Be polite

    print(f"\nDone. Files saved to {OUT_DIR}")
    print(f"Total files: {len(list(OUT_DIR.glob('*.html')))}")


if __name__ == "__main__":
    main()
