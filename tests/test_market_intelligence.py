import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import market_intelligence


class MarketIntelligenceTests(unittest.TestCase):
    def setUp(self):
        market_intelligence._cache = {"expires": 0.0, "items": [], "errors": []}

    @patch("market_intelligence.requests.get")
    def test_allowlisted_rss_is_sanitized_and_relevant_context_has_source_url(self, get):
        get.return_value = Mock(content=b"""<rss><channel><item><title>Bitcoin ETF update</title>
        <link>https://example.test/item</link><description>&lt;b&gt;Market&lt;/b&gt; &amp; update</description>
        <pubDate>Sat, 12 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>""")
        context = market_intelligence.context_for_pair("BTC-USDT", datetime(2026, 9, 12, 13, tzinfo=timezone.utc))
        self.assertEqual(len(context["items"]), 1)
        self.assertEqual(context["items"][0]["url"], "https://example.test/item")
        self.assertEqual(context["items"][0]["summary"], "Market & update")
        self.assertIn("untrusted", context["source_policy"])

    @patch("market_intelligence.requests.get", side_effect=market_intelligence.requests.RequestException())
    def test_feed_failure_is_nonfatal(self, get):
        items, errors = market_intelligence.latest_items()
        self.assertEqual(items, [])
        self.assertEqual(len(errors), len(market_intelligence.SOURCES))


if __name__ == "__main__":
    unittest.main()