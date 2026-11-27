"""Bóc mục lục và nội dung chương từ trang truyện Trung.

Ưu tiên luật riêng theo trang (SITE_RULES, bộ chọn CSS); không có thì dùng cách đoán chung:
- Mục lục: các link có chữ dạng 第12章 / 第十二回, gom theo khối cha, lấy khối nhiều link nhất.
- Nội dung: phần tử chứa nhiều chữ Hán nhất ở dòng chữ trực tiếp / <p> / <br>, lọc dòng quảng cáo.
Thêm trang mới: thêm một mục vào SITE_RULES (khoá là tên miền, khớp cả tên miền con).
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString

from .text import CHAPTER_ZH_RE

SITE_RULES = {
    # Mẫu giao diện "biquge" (rất nhiều trang dùng chung): <div id="list"><dl><dt>正文</dt><dd><a>
    "biquge.example": {
        "book_title": "#info h1", "book_author": "#info p", "intro": "#intro",
        "toc": "#list dd a", "title": ".bookname h1", "content": "#content",
    },
    # Mẫu giao diện "69shu"
    "69shu.example": {
        "book_title": ".bread a:nth-of-type(3)", "toc": "#catalog ul li a",
        "title": "h1", "content": ".txtnav",
    },
}

JUNK_PATTERNS = [
    r"https?://", r"www\.", r"\.(?:com|net|org|cc|la|info|xyz|tw|cn|me|io|vip)\b",
    r"请收藏", r"收藏本站", r"最新章节", r"最新網址|最新网址", r"本章未完", r"点击下一页|點擊下一頁", r"继续阅读|繼續閱讀",
    r"手机阅读|手機閱讀", r"笔趣阁|筆趣閣", r"加入书签|加入書籤", r"推荐票|推薦票", r"月票", r"章节错误|章節錯誤",
    r"一秒记住|一秒記住", r"免费阅读|免費閱讀", r"百度搜索", r"天才.{0,4}秒", r"小说网|小說網", r"txt下载|TXT下载",
    r"请记住|請記住", r"本站", r"首发|首發", r"更新最快", r"无弹窗|無彈窗", r"全文阅读", r"上一章|下一章|返回目录|返回目錄",
    r"^\s*[（(]?本章完[)）]?\s*$", r"^[-=_*·•.。…\s]+$",
]
_JUNK = re.compile("|".join(JUNK_PATTERNS), re.I)
_CJK = re.compile(r"[一-鿿]")
_NEXT_PAGE = re.compile(r"^\s*(下一页|下一頁|下页|下頁|下一节)\s*[>»→]*\s*$")
_PAGE_MARK = re.compile(r"\s*[（(]\s*\d+\s*/\s*\d+\s*[)）]\s*$")

STRIP_TAGS = ["script", "style", "noscript", "iframe", "form", "header", "footer", "nav", "aside",
              "select", "button", "input", "textarea", "svg", "ins"]


def _rule_for(url: str):
    host = (urlparse(url).hostname or "").lower()
    for domain, rule in SITE_RULES.items():
        if host == domain or host.endswith("." + domain):
            return rule
    return None


def _soup(html: str):
    return BeautifulSoup(html, "lxml")


def cjk_count(s: str) -> int:
    return len(_CJK.findall(s or ""))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("　", " ").replace("\xa0", " ")).strip()


def _meta(soup, *names):
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return _clean(tag["content"])
    return ""


def _sel_text(soup, css):
    if not css:
        return ""
    el = soup.select_one(css)
    return _clean(el.get_text(" ")) if el else ""


def _href_pattern(href: str) -> str:
    path = urlparse(href).path
    return re.sub(r"\d+", "#", path)


# ---------------------------------------------------------------- mục lục

def extract_toc(html: str, url: str) -> dict:
    """Trả {title, author, description, cover, chapters: [{title, url}], next_page}."""
    soup = _soup(html)
    rule = _rule_for(url) or {}
    title = (_sel_text(soup, rule.get("book_title")) or _meta(soup, "og:novel:book_name", "og:title")
             or _clean((soup.find("h1") or soup.new_tag("x")).get_text()) or _clean(soup.title.string if soup.title else ""))
    author = _meta(soup, "og:novel:author", "author") or _sel_text(soup, rule.get("book_author"))
    author = re.sub(r"^(作\s*者|作者)\s*[:：]\s*", "", author)
    description = _sel_text(soup, rule.get("intro")) or _meta(soup, "og:description", "description")
    cover = _meta(soup, "og:image")
    category = _meta(soup, "og:novel:category")

    links = []
    if rule.get("toc"):
        links = [(a.get_text(), a.get("href")) for a in soup.select(rule["toc"]) if a.get("href")]
    if not links:
        links = _generic_toc_links(soup)
    # trùng link: giữ lần xuất hiện sau cùng (khối "chương mới nhất" thường nằm phía trên danh sách đầy đủ)
    last = {}
    for i, (text, href) in enumerate(links):
        href = (href or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        last[urljoin(url, href)] = (i, _clean(text))
    chapters = [{"title": t, "url": u} for u, (i, t) in sorted(last.items(), key=lambda kv: kv[1][0])]

    next_page = None
    for a in soup.find_all("a", href=True):
        if _NEXT_PAGE.match(a.get_text() or ""):
            cand = urljoin(url, a["href"])
            if cand != url and not cand.startswith("javascript"):
                next_page = cand
                break
    return {"title": title, "author": author, "description": description, "cover": cover,
            "category": category, "chapters": chapters, "next_page": next_page}


def _container(a):
    for parent in a.parents:
        if parent.name in ("ul", "ol", "dl", "table", "tbody", "div", "section", "article"):
            return parent
    return None


def _generic_toc_links(soup):
    anchors = [a for a in soup.find_all("a", href=True)]
    matches = [a for a in anchors if CHAPTER_ZH_RE.search(a.get_text() or "")]
    groups = {}
    for a in matches:
        c = _container(a)
        if c is not None:
            groups.setdefault(id(c), [c, []])[1].append(a)
    if groups and max(len(g[1]) for g in groups.values()) >= 3:
        cont, hits = max(groups.values(), key=lambda g: len(g[1]))
        # mẫu đường dẫn phổ biến nhất trong các link chương → lấy luôn các link cùng mẫu (序章, 楔子...)
        pats = {}
        for a in hits:
            p = _href_pattern(a["href"])
            pats[p] = pats.get(p, 0) + 1
        main_pat = max(pats, key=pats.get)
        sel = []
        for el in cont.descendants:
            if getattr(el, "name", None) in ("dt", "h2", "h3", "h4") and _is_main_marker(el):
                sel = []  # bỏ khối "chương mới nhất" phía trên chữ 正文
                continue
            if getattr(el, "name", None) == "a" and el.get("href"):
                if CHAPTER_ZH_RE.search(el.get_text() or "") or _href_pattern(el["href"]) == main_pat:
                    sel.append(el)
        return [(a.get_text(), a["href"]) for a in sel]
    # không có chữ 第N章: lấy khối có nhiều link cùng mẫu đường dẫn nhất
    groups = {}
    for a in anchors:
        c = _container(a)
        if c is None:
            continue
        key = (id(c), _href_pattern(a["href"]))
        groups.setdefault(key, []).append(a)
    if not groups:
        return []
    best = max(groups.values(), key=len)
    return [(a.get_text(), a["href"]) for a in best] if len(best) >= 5 else []


def _is_main_marker(el) -> bool:
    t = _clean(el.get_text())
    return bool(re.search(r"正文", t)) and len(t) <= 30


# ---------------------------------------------------------------- nội dung chương

def _own_score(el) -> int:
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name in ("p", "span", "font", "strong", "em", "b", "i"):
            if not child.find("a"):
                parts.append(child.get_text())
    return cjk_count("".join(parts))


def _text_lines(el):
    for br in el.find_all("br"):
        br.replace_with("\n")
    for p in el.find_all(["p", "div", "h1", "h2", "h3", "h4", "li", "dd"]):
        p.insert_before("\n")
        p.insert_after("\n")
    lines = []
    for ln in el.get_text().split("\n"):
        ln = _clean(ln)
        if ln:
            lines.append(ln)
    return lines


def clean_lines(lines, title: str = ""):
    out = []
    t = _clean(title)
    for ln in lines:
        if _JUNK.search(ln):
            continue
        if t and (ln == t or ln.replace(" ", "") == t.replace(" ", "")):
            continue
        if not cjk_count(ln) and len(ln) < 3:
            continue
        out.append(ln)
    return out


def extract_chapter(html: str, url: str) -> dict:
    """Trả {title, text (đoạn cách nhau 1 dòng trống), next_page (trang 2 của cùng chương, nếu có)}."""
    soup = _soup(html)
    rule = _rule_for(url) or {}
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    next_page = _chapter_next_page(soup, url)
    title = _sel_text(soup, rule.get("title"))
    if not title:
        for h in soup.find_all(["h1", "h2", "h3"]):
            txt = _clean(h.get_text())
            if CHAPTER_ZH_RE.search(txt) or (h.name == "h1" and txt):
                title = txt
                break
    title = _PAGE_MARK.sub("", title)
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()
    body = soup.select_one(rule["content"]) if rule.get("content") else None
    if body is None:
        best, best_score = None, 0
        for el in soup.find_all(["div", "article", "section", "td", "main", "dd", "pre"]):
            s = _own_score(el)
            if s > best_score:
                best, best_score = el, s
        body = best
    if body is None:
        return {"title": title, "text": "", "next_page": next_page}
    lines = clean_lines(_text_lines(body), title)
    return {"title": title, "text": "\n\n".join(lines), "next_page": next_page}


def _stem(u: str) -> str:
    p = urlparse(u)
    path = re.sub(r"\.(html?|php|aspx?)$", "", p.path)
    path = re.sub(r"[_\-]\d{1,2}$", "", path)
    return f"{p.netloc}{path}"


def same_chapter(a: str, b: str) -> bool:
    return a != b and _stem(a) == _stem(b)


def _chapter_next_page(soup, url):
    for a in soup.find_all("a", href=True):
        if _NEXT_PAGE.match(a.get_text() or ""):
            cand = urljoin(url, a["href"])
            if same_chapter(url, cand):
                return cand
    return None


# ---------------------------------------------------------------- dùng với Fetcher

def fetch_toc(fetcher, url: str, max_pages: int = 60) -> dict:
    info = extract_toc(fetcher.get(url), url)
    seen_pages = {url}
    page = info
    chapters = list(info["chapters"])
    while page.get("next_page") and page["next_page"] not in seen_pages and len(seen_pages) < max_pages:
        nxt = page["next_page"]
        seen_pages.add(nxt)
        page = extract_toc(fetcher.get(nxt, referer=url), nxt)
        known = {c["url"] for c in chapters}
        new = [c for c in page["chapters"] if c["url"] not in known]
        if not new:
            break
        chapters.extend(new)
    info["chapters"] = chapters
    return info


def fetch_chapter(fetcher, url: str, max_pages: int = 12) -> dict:
    data = extract_chapter(fetcher.get(url), url)
    texts = [data["text"]] if data["text"] else []
    seen = {url}
    nxt = data["next_page"]
    while nxt and nxt not in seen and len(seen) < max_pages:
        seen.add(nxt)
        part = extract_chapter(fetcher.get(nxt, referer=url), nxt)
        if part["text"]:
            texts.append(part["text"])
        nxt = part["next_page"]
    return {"title": data["title"], "text": "\n\n".join(texts)}
