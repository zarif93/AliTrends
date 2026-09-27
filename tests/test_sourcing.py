from decimal import Decimal

from alitrends.aliexpress import Product
from alitrends.config import MARKETS
from alitrends.sourcing import ProductSource, QualityBar, passes, score
from alitrends.storage import Storage


def make(pid, discount=40, sales=1000, rating=4.8, commission=7.0):
    return Product(pid, f"title {pid}", "img", "link", Decimal("10"), Decimal("20"), "USD",
                   discount, rating, sales, commission, "cat")


class FakeClient:
    def __init__(self, products):
        self.products = products
        self.calls = 0

    def short_link(self, url):
        return url + "/short"

    def search_products(self, **kwargs):
        self.calls += 1
        return self.products if kwargs["page"] == 1 else []


def test_quality_filter():
    bar, us, fr = QualityBar(), MARKETS["English"], MARKETS["French"]
    assert passes(make("a"), bar, us)
    assert not passes(make("b", rating=4.2), bar, us)
    assert not passes(make("c", sales=10), bar, us)
    assert not passes(make("d", discount=5), bar, us)
    assert passes(make("e", discount=0), bar, fr)  # EU listings often have no strike-through price


def test_score_prefers_better_deals():
    assert score(make("a", discount=60)) > score(make("b", discount=20))
    assert score(make("a", sales=50_000)) > score(make("b", sales=200))


def test_pick_skips_recent_and_caches_pool(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    client = FakeClient([make("1"), make("2"), make("3", rating=3.0)])
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7)
    market = MARKETS["English"]

    storage.record_publication("English/Toys & Kids", "telegram", "1", "m1", "10", "USD")
    picks = {source.pick(market, "Toys & Kids", "English/Toys & Kids").product_id for _ in range(20)}
    assert picks == {"2"}
    assert source.pick(market, "Toys & Kids", "English/Toys & Kids").promotion_link == "link/short"          # "1" was posted recently, "3" fails the quality bar
    assert client.calls == 2       # page 1 + empty page 2, then served from cache

    storage.record_publication("English/Toys & Kids", "telegram", "2", "m2", "10", "USD")
    assert source.pick(market, "Toys & Kids", "English/Toys & Kids") is None
    storage.close()
