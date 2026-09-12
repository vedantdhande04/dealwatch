"""Tests for the Amazon.in product page parser."""

import unittest

from dealwatch.fetcher import OutOfStockError, PriceNotFoundError, parse_amazon_page

IN_STOCK_PAGE = """
<html><head><title>Amazon.in</title></head><body>
  <span id="productTitle" class="a-size-large">
      Sony WH-1000XM4  Wireless Noise Cancelling Headphones
  </span>
  <div id="availability" class="a-section a-spacing-none">
    <span class="a-color-success">In stock</span>
  </div>
  <span class="a-price-whole">24,990<span class="a-price-decimal">.</span></span>
</body></html>
"""

OUT_OF_STOCK_PAGE = """
<html><body>
  <span id="productTitle">Old Kindle Paperwhite</span>
  <div id="availability" class="a-section">
    <span class="a-color-price">Currently unavailable.</span>
  </div>
</body></html>
"""

NO_PRICE_PAGE = """
<html><body>
  <span id="productTitle">Some product</span>
</body></html>
"""


class AmazonParserTest(unittest.TestCase):
    def test_reads_title_and_price(self):
        title, price = parse_amazon_page(IN_STOCK_PAGE)
        self.assertEqual(
            title, "Sony WH-1000XM4 Wireless Noise Cancelling Headphones"
        )
        self.assertEqual(price, 24990.0)

    def test_low_price_without_comma(self):
        html = IN_STOCK_PAGE.replace("24,990", "999")
        _, price = parse_amazon_page(html)
        self.assertEqual(price, 999.0)

    def test_out_of_stock_raises(self):
        with self.assertRaises(OutOfStockError):
            parse_amazon_page(OUT_OF_STOCK_PAGE)

    def test_page_without_price_raises(self):
        with self.assertRaises(PriceNotFoundError):
            parse_amazon_page(NO_PRICE_PAGE)

    def test_missing_title_is_not_fatal(self):
        html = IN_STOCK_PAGE.replace('id="productTitle"', 'id="someOtherId"')
        title, price = parse_amazon_page(html)
        self.assertEqual(title, "")
        self.assertEqual(price, 24990.0)


if __name__ == "__main__":
    unittest.main()
