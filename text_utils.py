"""
Cleans raw text pulled from RSS feeds or scraped pages — strips HTML tags
and decodes HTML entities (like &#039; -> ') so nothing raw ever ends up
displayed or fed into an LLM prompt.
"""

import re
from html import unescape


def clean_text(text):
    if not text:
        return text
    text = re.sub(r'<[^>]+>', ' ', text)   # strip HTML tags
    text = unescape(text)                    # decode entities: &#039; -> ', &amp; -> &, etc.
    text = re.sub(r'\s+', ' ', text).strip() # collapse whitespace left behind by stripped tags
    return text
