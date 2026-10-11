"""Напоминания об акциях постоянных позиций: об обычной цене не напоминаем."""
from types import SimpleNamespace

import pytest

from core import deals


@pytest.fixture(autouse=True)
def no_flyer_history(monkeypatch):  # тесты не зависят от скачанных газеток
    monkeypatch.setattr(deals, "src", lambda store="lidl": SimpleNamespace(previous_price=lambda d: None))


def deal(**kw):
    d = {"title": "Kostka z fileta z piersi kurczaka", "price": 11.99, "old_price": None, "discount": None, "unit_price": 29.98,
         "unit": "kg", "approx": False, "start": "2099-01-01", "end": "2099-01-07", "slug": "test-flyer", "page": 1,
         "store_key": "kaufland", "q": "filet z kurczaka", "usual": {"price": 29.9, "unit": "kg", "title": "Filet z kurczaka luz"}}
    return d | kw


def test_regular_price_not_reminded():
    assert not deals.worth(deal())


def test_flyer_discount_reminded():
    assert deals.worth(deal(price=8.99, old_price=11.99, discount=25))


def test_cheaper_than_usual_reminded_with_reason():
    d = deal(price=9.99, unit_price=24.98)
    assert deals.worth(d) and "дешевле твоей обычной цены" in d["why"] and "за кг" in d["why"]


def test_query_only_in_neighbour_text_not_reminded():
    assert not deals.worth(deal(title="TARCZYŃSKI Parówki z szynki lub z fileta", price=8.99, discount=30))


def test_cheaper_than_previous_flyer_reminded(monkeypatch):
    prev = {"price": 12.99, "start": "2098-12-01"}
    monkeypatch.setattr(deals, "src", lambda store="lidl": SimpleNamespace(previous_price=lambda d: prev))
    d = deal(usual=None)
    assert deals.worth(d) and "дешевле, чем в газетке" in d["why"]
