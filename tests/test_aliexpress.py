from decimal import Decimal

import pytest

from alitrends.aliexpress import AliExpressClient, AliExpressError, Product, sign

RAW = {
    "product_id": 1005007038638226,
    "product_title": "Original Lenovo LP40 Wireless Headphones",
    "product_main_image_url": "https://ae01.alicdn.com/kf/x.jpg",
    "promotion_link": "https://s.click.aliexpress.com/e/_abc",
    "target_sale_price": "6.66",
    "target_original_price": "13.88",
    "target_sale_price_currency": "USD",
    "discount": "52%",
    "evaluate_rate": "98.0%",
    "lastest_volume": 31806,
    "commission_rate": "7.0%",
    "first_level_category_name": "Consumer Electronics",
}


def test_sign_is_sorted_hmac_sha256_uppercase():
    # Order of insertion must not matter.
    a = sign({"b": "2", "a": "1"}, "secret")
    b = sign({"a": "1", "b": "2"}, "secret")
    assert a == b and a.isupper() and len(a) == 64


def test_product_parsing():
    p = Product.from_api(RAW)
    assert p.product_id == "1005007038638226"
    assert p.price == Decimal("6.66") and p.original_price == Decimal("13.88")
    assert p.discount == 52 and p.rating == 4.9 and p.sales == 31806 and p.commission_rate == 7.0


@pytest.mark.parametrize("missing", ["promotion_link", "product_main_image_url", "target_sale_price"])
def test_product_without_essentials_is_skipped(missing):
    raw = {k: v for k, v in RAW.items() if k != missing}
    assert Product.from_api(raw) is None


def test_error_response_raises():
    with pytest.raises(AliExpressError, match="InsufficientPermission"):
        AliExpressClient._unwrap("x.y", {"error_response": {"code": "InsufficientPermission", "msg": "nope"}})


def test_unwrap_returns_result():
    data = {"x_y_response": {"resp_result": {"resp_code": 200, "result": {"products": {}}}}}
    assert AliExpressClient._unwrap("x.y", data) == {"products": {}}


def test_rate_limit_is_retryable_error():
    from alitrends.aliexpress import RateLimited
    with pytest.raises(RateLimited):
        AliExpressClient._unwrap("x.y", {"error_response": {"code": "ApiCallLimit", "msg": "slow down"}})
