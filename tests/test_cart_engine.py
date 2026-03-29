from __future__ import annotations

import unittest

from app.services.cart_engine import apply_cart_action, extract_item, extract_quantity, parse_cart_intent, parse_cart_requests


class CartEngineTests(unittest.TestCase):
    def test_add_intent_and_quantity(self) -> None:
        self.assertEqual(parse_cart_intent("give me 7 boots"), "add")
        self.assertEqual(extract_quantity("give me 7 boots"), 7)
        self.assertEqual(extract_item("give me 7 boots"), "boots")

    def test_remove_without_quantity_removes_all(self) -> None:
        cart = {"items": [{"name": "boots", "qty": 7}], "address": ""}
        updated = apply_cart_action(cart, "boots", "remove", 7)
        self.assertEqual(updated["items"], [])

    def test_zero_quantity_removes_item(self) -> None:
        cart = {"items": [{"name": "boots", "qty": 6}], "address": ""}
        updated = apply_cart_action(cart, "boots", "set", 0)
        self.assertEqual(updated["items"], [])

    def test_clear_cart_empties_items(self) -> None:
        cart = {"items": [{"name": "boots", "qty": 6}, {"name": "hoodie", "qty": 1}], "address": "Somewhere"}
        updated = apply_cart_action(cart, "", "clear", 0)
        self.assertEqual(updated["items"], [])
        self.assertEqual(updated["address"], "Somewhere")

    def test_parse_multi_item_requests_keeps_quantities_separate(self) -> None:
        requests = parse_cart_requests("I want 2 boots and 3 hoodies")
        self.assertEqual(
            requests,
            [
                {"action": "add", "item": "boots", "qty": 2, "explicit_qty": True},
                {"action": "add", "item": "hoodies", "qty": 3, "explicit_qty": True},
            ],
        )


if __name__ == "__main__":
    unittest.main()
