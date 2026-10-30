"""Перевод того, что сервер отправляет сам (напоминания в Telegram), — тем же словарём, что интерфейс
(app/static/js/i18n-en.js), и теми же правилами (app/static/js/i18n.js): ключ — русский текст, числа и даты — {n},
имена — {s}. Язык — выбор в «Настройках» (meta lang); не выбран или русский — текст как есть."""
import json
import re

from core.common import BUDGET

DICT = BUDGET / "app" / "static" / "js" / "i18n-en.js"
NUM_SRC = r"[-−+]?\d+(?:[   ]\d{3}(?!\d))*(?:[.,:/-]\d+)*(?:[  ]?zł)?%?"
NUM_RE = re.compile(NUM_SRC)
CYR = re.compile(r"[А-Яа-яЁё]")
DECOR = re.compile(r"^((?:[▸▾•✓✗⚠↩–—]\s*|[🔔🔕📷✉🛒]️?\s*)*)(.*?)((?:\s*[▾↗·])*)$", re.S)
_cache = {}


def _load():
    if "plain" in _cache:
        return _cache
    plain, patterns = {}, []
    line_re = re.compile(r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*"),?\s*$')
    for ln in DICT.read_text(encoding="utf-8").splitlines():
        if m := line_re.match(ln):
            k, v = json.loads(m.group(1)), json.loads(m.group(2))
            if "{s}" not in k:
                plain[k] = v
                continue
            kinds, rx = [], ""
            for part in re.split(r"(\{n\}|\{s\})", k):
                if part == "{n}":
                    kinds.append("n"); rx += f"({NUM_SRC})"
                elif part == "{s}":
                    kinds.append("s"); rx += "(.+?)"
                else:
                    rx += re.escape(part)
            patterns.append((re.compile("^" + rx + "$", re.S), v, len(k), kinds))
    patterns.sort(key=lambda p: -p[2])
    _cache.update(plain=plain, patterns=patterns)
    return _cache


def _fill(tpl: str, vals: list[str]) -> str:
    it = iter(vals)
    return re.sub(r"\{(n|s|\d+)\}", lambda m: vals[int(m.group(1)) - 1] if m.group(1).isdigit() else next(it, m.group(0)), tpl)


def _value(v: str) -> str:
    r = translate(v)
    if r != v or ", " not in v:
        return r
    parts = v.split(", ")
    tr = [translate(p) for p in parts]
    return ", ".join(tr) if tr != parts else v


def _lookup(flat: str):
    d = _load()
    out = d["plain"].get(flat)
    if out is None and (nums := NUM_RE.findall(flat)):
        tpl = d["plain"].get(NUM_RE.sub("{n}", flat))
        if tpl is not None:
            out = _fill(tpl, nums)
    if out is None:
        for rx, v, _, kinds in d["patterns"]:
            if m := rx.match(flat):
                out = _fill(v, [_value(x) if kinds[i] == "s" else x for i, x in enumerate(m.groups())])
                break
    if out is None:
        for sep in (" › ", "; ", " · ", "/"):
            if sep in flat:
                parts = flat.split(sep)
                tr = [translate(p) for p in parts]
                if tr != parts:
                    out = sep.join(tr)
                    break
    return out


def translate(s: str) -> str:
    """Одна строка (как текстовый узел страницы): нет в словаре — как есть."""
    if not s or not CYR.search(s):
        return s
    lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", s, re.S).groups()
    flat = re.sub(r"\s+", " ", core)
    out = _lookup(flat)
    if out is None and (m := DECOR.match(flat)) and (m.group(1) or m.group(3)) and m.group(2):
        inner = _lookup(m.group(2))
        if inner is not None:
            out = m.group(1) + inner + m.group(3)
    return s if out is None else lead + out + trail


def lang(con=None) -> str:
    try:
        from core.db import connect, get_meta
        return get_meta(con or connect(), "lang") or "ru"
    except Exception:  # noqa: BLE001 — без базы (lidl.py отдельно) — по-русски
        return "ru"


def localize(text: str, con=None) -> str:
    """Многострочный текст (напоминание): построчно, если выбран английский."""
    if lang(con) != "en":
        return text
    return "\n".join(translate(ln) for ln in text.split("\n"))
