"""
WebFetchTool utilities — fetch and extract readable content from URLs.

Uses BeautifulSoup + lxml for smart HTML-to-text extraction with
non-content removal, main-content detection, and whitespace cleanup.
"""
from typing import Optional
import re
import urllib.request
import urllib.error
import urllib.parse
from urllib.parse import urlparse


def validate_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def sanitize_url(url: str) -> str:
    """Sanitize URL for fetching — add https:// if missing."""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url


def extract_content(html: str) -> str:
    """
    Extract readable text content from HTML using BeautifulSoup.

    Removes scripts, styles, navigation, and other non-content elements.
    Intelligently finds <main>, <article>, or #content regions first.
    Falls back to regex-based tag stripping if BeautifulSoup is unavailable.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')

        # Remove non-content elements
        for tag in soup.find_all([
            'script', 'style', 'nav', 'footer', 'header', 'aside',
            'noscript', 'iframe', 'form', 'button', 'input', 'select',
            'textarea', 'svg', 'canvas', 'video', 'audio',
        ]):
            tag.decompose()

        # Remove common non-content CSS classes/IDs
        _non_content_classes = [
            'sidebar', 'sidebar-nav', 'navigation', 'nav-menu',
            'footer', 'header', 'advertisement', 'ad-', 'ads-',
            'cookie', 'popup', 'modal', 'comment', 'social',
            'share', 'related', 'recommend', 'sponsored',
        ]
        for cls in _non_content_classes:
            for tag in soup.find_all(class_=re.compile(cls, re.IGNORECASE)):
                tag.decompose()
            for tag in soup.find_all(id=re.compile(cls, re.IGNORECASE)):
                tag.decompose()

        # Try to find main content area
        main = (
            soup.find('main') or
            soup.find('article') or
            soup.find(role='main') or
            soup.find(id='content') or
            soup.find(id='main') or
            soup.find(class_='content') or
            soup.find(class_='markdown-body') or
            soup.find(class_='documentation') or
            soup.find(class_='doc-content')
        )

        if main:
            text = main.get_text(separator='\n', strip=True)
        else:
            body = soup.find('body')
            if body:
                text = body.get_text(separator='\n', strip=True)
            else:
                text = soup.get_text(separator='\n', strip=True)

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

    except ImportError:
        # Fallback: regex-based tag removal
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text


async def get_url_markdown_content(url: str) -> str:
    """
    Fetch a URL and return its content as plain text.

    Uses BeautifulSoup for smart extraction with non-content removal.
    Handles redirects and common HTTP errors gracefully.

    Args:
        url: The URL to fetch.

    Returns:
        Extracted text content from the page, or empty string on failure.
    """
    url = sanitize_url(url)

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            # Respect charset from Content-Type header
            content_type = response.headers.get('Content-Type', '')
            charset_match = re.search(r'charset=([^\s;]+)', content_type)
            encoding = charset_match.group(1) if charset_match else 'utf-8'
            html = response.read().decode(encoding, errors='replace')

        return extract_content(html)

    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return f"URL Error: {exc.reason}"
    except Exception as exc:
        return f"Fetch error: {exc}"


__all__ = ['validate_url', 'sanitize_url', 'extract_content', 'get_url_markdown_content']
