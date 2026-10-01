"""Общие функции для всех модулей бюджета."""
import os
import re
import unicodedata
from pathlib import Path

BUDGET = Path(__file__).resolve().parent.parent
DATA = BUDGET / "data"


def fold(s: str) -> str:
    """Нижний регистр без польских диакритик: 'Masło' -> 'maslo'."""
    s = (s or "").lower().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def word_matches(word: str, token: str) -> bool:
    """Польские окончания: 'ser' ~ serem/serek, 'kawa' ~ kawy, но не serdelki/kawałkami."""
    stem = token[:-1] if len(token) >= 4 and token[-1] in "aeiouy" else token
    return word.startswith(stem) and len(word) - len(stem) <= 3


def num(x):
    """'3,99' / '3.99' / 3.99 / None -> float | None"""
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(x).replace(" ", "").replace("\xa0", ""))
    return float(m.group().replace(",", ".")) if m else None


def money(x) -> str:
    return f"{x:.2f}".replace(".", ",") + " zł"


def html_escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def open_file(path: Path):
    try:
        os.startfile(path)
    except (AttributeError, OSError):
        print(f"Открой вручную: {path}")
