"""Lidl: список чеков листается до двух страниц подряд, где всё уже скачано, а не до конца истории."""
from receipts import lidl


def test_list_stops_after_two_known_pages(monkeypatch):
    pages = {p: [{"id": f"t{p}{i}"} for i in range(25)] for p in range(1, 7)}
    asked = []

    class R:
        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            pass

        def json(self):
            return {"tickets": pages[self.page], "size": 25, "totalCount": 150}

    def get(url, params, headers, timeout):
        asked.append(params["pageNumber"])
        return R(params["pageNumber"])

    monkeypatch.setattr(lidl.requests, "get", get)
    monkeypatch.setattr(lidl, "headers", lambda: {})
    known = {f"lidl:t{p}{i}" for p in range(2, 7) for i in range(25)} | {"lidl:t10"}  # новые — только на 1-й странице
    assert len(lidl.list_tickets(known)) == 75 and asked == [1, 2, 3]
    asked.clear()
    assert len(lidl.list_tickets()) == 150 and asked == [1, 2, 3, 4, 5, 6]  # без known — вся история (вход)


def test_weighted_line_without_trailing_zero():
    """«1,246kg x 24.8 30.9 C» — Lidl не печатает ноль в конце суммы (30,90): строка — сумма позиции, а не вторая
    позиция без суммы (из-за неё падали страницы «Банк» и «Аналитика»)."""
    art = 'class="article" data-art-id="1" data-art-quantity="1,246" data-unit-price="24,80" data-art-description="Filet test"'
    html = (f'<span id="purchase_list_line_1" {art}>Filet test</span>'
            f'<span id="purchase_list_line_2" {art}>   1,246kg x 24.8 30.9 C</span>'
            '<span id="purchase_list_line_3" class="discount" data-promotion-id="1">   39% taniej   -12,22</span>')
    assert [(i["name"], i["qty"], i["amount"], i["discount"]) for i in lidl.parse_html_receipt(html)] == \
        [("Filet test", 1.246, 30.9, 12.22)]
