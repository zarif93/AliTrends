from dataclasses import replace
from decimal import Decimal

import pytest

from alitrends.aliexpress import AliExpressError, Product
from alitrends.config import MARKETS
from alitrends.sourcing import ProductSource, QualityBar, passes, rank_key, score
from alitrends.storage import Storage

MARKET = MARKETS["English"]
CATEGORY = "Toys & Kids"
CHANNEL = "English/Toys & Kids"


def make(pid, discount=40, sales=1000, rating=4.8, commission=7.0, hot=False):
    return Product(pid, f"title {pid}", "img", "link", Decimal("10"), Decimal("20"), "USD",
                   discount, rating, sales, commission, "cat", hot)


class FakeClient:
    """Returns `products` on page 1 and nothing after; `hot` may be a list or an exception."""

    def __init__(self, products, hot=()):
        self.products = products
        self.hot = hot
        self.calls = {"search": 0, "hot": 0}

    def short_link(self, url):
        return url + "/short"

    def search_products(self, **kwargs):
        self.calls["search"] += 1
        return self.products if kwargs["page"] == 1 else []

    def hot_products(self, **kwargs):
        self.calls["hot"] += 1
        if isinstance(self.hot, Exception):
            raise self.hot
        return [replace(p, hot=True) for p in self.hot] if kwargs["page"] == 1 else []


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    yield s
    s.close()


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


def test_hot_ranks_above_better_scoring_regular():
    assert rank_key(make("h", discount=20, hot=True)) > rank_key(make("r", discount=80))


def test_pick_skips_recent_and_caches_pool(storage):
    client = FakeClient([make("1"), make("2"), make("3", rating=3.0)])
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7)

    storage.record_publication(CHANNEL, "telegram", "1", "m1", "10", "USD")
    picks = {source.pick(MARKET, CATEGORY, CHANNEL).product_id for _ in range(20)}
    assert picks == {"2"}          # "1" was posted recently, "3" fails the quality bar
    assert source.pick(MARKET, CATEGORY, CHANNEL).promotion_link == "link/short"
    assert client.calls == {"hot": 1, "search": 2}  # one refresh (empty hot page stops paging), then cached

    storage.record_publication(CHANNEL, "telegram", "2", "m2", "10", "USD")
    assert source.pick(MARKET, CATEGORY, CHANNEL) is None


def test_hot_first_then_topped_up_from_search(storage):
    hot = [make("h1"), make("h2", discount=0)]            # h2 fails the discount rule in the US
    regular = [make("h1", discount=90), make("r1", discount=90)]  # h1 duplicate: hot version wins
    client = FakeClient(regular, hot=hot)
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7, min_candidates=30)

    pool = source.pool(MARKET, CATEGORY)
    assert [p.product_id for p in pool] == ["h1", "r1"]
    assert pool[0].hot and pool[0].discount == 40 and not pool[1].hot


def test_enough_hot_skips_search(storage):
    client = FakeClient([make("r1")], hot=[make(f"h{i}") for i in range(5)])
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7, min_candidates=5)

    pool = source.pool(MARKET, CATEGORY)
    assert len(pool) == 5 and all(p.hot for p in pool)
    assert client.calls["search"] == 0


def test_hot_failure_falls_back_and_backs_off(storage):
    client = FakeClient([make("r1"), make("r2")], hot=AliExpressError("InsufficientPermission"))
    source = ProductSource(client, storage, pool_ttl=0, cooldown_days=7)

    assert [p.product_id for p in source.pool(MARKET, CATEGORY)] == ["r1", "r2"]
    assert client.calls["hot"] == 1
    source.pool(MARKET, "Beauty & Health")          # another refresh within the back-off window
    assert client.calls["hot"] == 1                 # hot not retried
    assert client.calls["search"] == 4


def test_main_pool_keeps_hot_priority(storage):
    client = FakeClient([make("r1", discount=90)], hot=[make("h1", discount=20)])
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7)
    pool = source.pool(MARKET, "main")
    assert [p.product_id for p in pool] == ["h1", "r1"]


def test_blacklist_filters_ids_and_title_words(storage):
    client = FakeClient([make("1"), replace(make("2"), title="Xiaomi band"), make("3")])
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7)
    storage.add_blacklist("product", "1")
    storage.add_blacklist("keyword", "XIAOMI")
    assert {source.pick(MARKET, CATEGORY, CHANNEL).product_id for _ in range(20)} == {"3"}


def test_channel_tracking_id_builds_link_from_item_page(storage):
    class TrackingClient(FakeClient):
        def short_link(self, url, tracking_id=None):
            return f"{url}|{tracking_id}"

    source = ProductSource(TrackingClient([make("9")]), storage, pool_ttl=3600, cooldown_days=7)
    product = source.pick(MARKET, CATEGORY, CHANNEL, tracking_id="he_main")
    assert product.promotion_link == "https://www.aliexpress.com/item/9.html|he_main"


def test_configure_applies_thresholds_and_drops_pools(storage):
    from alitrends.config import Tuning
    client = FakeClient([make("1", sales=150), make("2", sales=5000)])
    source = ProductSource(client, storage, pool_ttl=3600, cooldown_days=7)
    assert len(source.pool(MARKET, CATEGORY)) == 2
    source.configure(Tuning(min_sales=1000, hot_products_enabled=False))
    assert [p.product_id for p in source.pool(MARKET, CATEGORY)] == ["2"]
    assert client.calls["hot"] == 1  # the second refresh skipped hot products
