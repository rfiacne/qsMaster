"""Download docx/pdf/xlsx attachments from SSE guide article pages."""

import re
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://www.sse.com.cn"
LIST_URL = f"{BASE_URL}/lawandrules/guide/latest/"
OUT_DIR = Path("G:/qsMaster/data/sse_guide_attachments")

# Document file extensions to look for
DOC_EXTS = re.compile(r"\.(docx|pdf|xlsx|xls|zip|rar|doc)(\?|$)", re.I)


def fetch(url: str, retries: int = 3) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def extract_article_urls_from_list(html_text: str) -> list[str]:
    """Get article page URLs from listing page."""
    pattern = r'<a[^>]*href=["\']([^"\']*?/lawandrules/guide/[^"\']*?\.shtml)["\'][^>]*title=["\']([^"\']*)'
    matches = re.findall(pattern, html_text, re.DOTALL)
    seen = set()
    urls = []
    for href, title in matches:
        if href not in seen and title.strip():
            seen.add(href)
            url = (
                href
                if href.startswith("http")
                else BASE_URL + (href if href.startswith("/") else "/" + href)
            )
            urls.append(url)
    return urls


def extract_attachment_links(html_text: str, page_url: str) -> list[tuple[str, str]]:
    """Find document download links in article page. Returns [(full_url, filename), ...]."""
    # Find all hrefs
    all_hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_text)
    attachments = []
    seen = set()

    # Resolve base URL for relative paths
    # Article URL like: https://www.sse.com.cn/lawandrules/guide/stock/.../c/c_20260424_10816611.shtml
    # Attachment like: 10816611/files/xxxxx.docx (relative to page directory)
    page_dir = page_url.rsplit("/", 1)[0]  # remove the .shtml filename

    for href in all_hrefs:
        if DOC_EXTS.search(href):
            if href in seen:
                continue
            seen.add(href)

            if href.startswith("http"):
                full_url = href
            elif href.startswith("//"):
                full_url = "https:" + href
            elif href.startswith("/"):
                full_url = BASE_URL + href
            else:
                # Relative path - resolve against page directory
                full_url = page_dir + "/" + href

            filename = href.split("/")[-1].split("?")[0]
            attachments.append((full_url, filename))

    return attachments


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Get listing page
    print("Fetching listing page...")
    list_html = fetch(LIST_URL).decode("utf-8", errors="replace")
    article_urls = extract_article_urls_from_list(list_html)
    print(f"Found {len(article_urls)} article pages")

    # Step 2: Scan each article for attachments
    all_attachments = []  # [(article_url, attachment_url, filename)]
    for i, page_url in enumerate(article_urls):
        try:
            print(f"[{i + 1}/{len(article_urls)}] Scanning: {page_url.split('/')[-1]}")
            page_html = fetch(page_url).decode("utf-8", errors="replace")
            attachments = extract_attachment_links(page_html, page_url)
            for att_url, filename in attachments:
                all_attachments.append((page_url, att_url, filename))
                print(f"  -> {filename}")
        except Exception as e:
            print(f"  -> ERROR scanning: {e}")
        time.sleep(0.3)

    print(f"\nTotal attachments found: {len(all_attachments)}")

    # Step 3: Download all attachments
    downloaded = 0
    skipped = 0
    errors = 0
    for i, (article_url, att_url, filename) in enumerate(all_attachments):
        filepath = OUT_DIR / filename
        if filepath.exists():
            print(f"[{i + 1}/{len(all_attachments)}] SKIP (exists): {filename}")
            skipped += 1
            continue

        try:
            print(f"[{i + 1}/{len(all_attachments)}] Downloading: {filename}")
            data = fetch(att_url)
            filepath.write_bytes(data)
            size_kb = len(data) / 1024
            print(f"  -> Saved: {filename} ({size_kb:.1f} KB)")
            downloaded += 1
        except Exception as e:
            print(f"  -> ERROR: {e}")
            errors += 1
        time.sleep(0.3)

    print(f"\nDone. Downloaded: {downloaded}, Skipped: {skipped}, Errors: {errors}")
    print(f"Files in {OUT_DIR}: {len(list(OUT_DIR.iterdir()))}")


if __name__ == "__main__":
    main()
