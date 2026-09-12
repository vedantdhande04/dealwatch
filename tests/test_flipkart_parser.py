"""Tests for the Flipkart product page parser."""

import json
import unittest

from dealwatch.fetcher import OutOfStockError, PriceNotFoundError, parse_flipkart_page


def state_page(availability: str = "https://schema.org/InStock", price="3999",
               offers=None) -> str:
    """Build a page with flipkart's embedded __INITIAL_STATE__ product schema."""
    offer = {
        "@type": "Offer",
        "price": price,
        "priceCurrency": "INR",
        "availability": availability,
    }
    product = {
        "@type": "Product",
        "name": "Noise ColorFit Pro 4 Smart Watch",
        "offers": offer if offers is None else offers,
    }
    state = {
        "multiWidgetState": {
            "pageDataResponse": {
                "seoData": {"schema": [{"@type": "BreadcrumbList"}, product]}
            }
        }
    }
    return (
        "<html><body><script>window.__INITIAL_STATE__ = "
        + json.dumps(state)
        + ";</script></body></html>"
    )


# the older markup, used as a fallback when the embedded state is gone
OLD_MARKUP_PAGE = (
    "<html><body>"
    '<span class="B_NuCI">Noise Smart Watch</span>'
    '<div class="_30jeq3 _1_WHN1">\u20b93,999</div>'
    "</body></html>"
)

OG_TITLE_PAGE = (
    "<html><head>"
    '<meta property="og:title" content="BoAt Airdopes 141" />'
    "</head><body>"
    '<div class="_30jeq3">\u20b92,499</div>'
    "</body></html>"
)

BLOCKED_PAGE = "<html><body><h1>Something went wrong</h1></body></html>"


class FlipkartParserTest(unittest.TestCase):
    def test_reads_title_and_price_from_embedded_state(self):
        title, price = parse_flipkart_page(state_page())
        self.assertEqual(title, "Noise ColorFit Pro 4 Smart Watch")
        self.assertEqual(price, 3999.0)

    def test_price_without_commas(self):
        _, price = parse_flipkart_page(state_page(price="299"))
        self.assertEqual(price, 299.0)

    def test_offers_as_a_list(self):
        page = state_page(offers=[{"@type": "Offer", "price": "1299",
                                   "availability": "https://schema.org/InStock"}])
        _, price = parse_flipkart_page(page)
        self.assertEqual(price, 1299.0)

    def test_out_of_stock_raises(self):
        page = state_page(availability="https://schema.org/OutOfStock")
        with self.assertRaises(OutOfStockError):
            parse_flipkart_page(page)

    def test_falls_back_to_old_markup(self):
        title, price = parse_flipkart_page(OLD_MARKUP_PAGE)
        self.assertEqual(title, "Noise Smart Watch")
        self.assertEqual(price, 3999.0)

    def test_og_title_fallback(self):
        title, price = parse_flipkart_page(OG_TITLE_PAGE)
        self.assertEqual(title, "BoAt Airdopes 141")
        self.assertEqual(price, 2499.0)

    def test_blocked_page_raises(self):
        with self.assertRaises(PriceNotFoundError):
            parse_flipkart_page(BLOCKED_PAGE)


if __name__ == "__main__":
    unittest.main()
