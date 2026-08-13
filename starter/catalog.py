"""商品目录管理（读写 data/products.jsonl）。

商品目录本身是静态 JSONL 文件；管理员增删改查会写回文件。写回后需要
由调用方刷新内存中的 ProductRepository（见 api.py 的 ``_reload_agent``）。
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
PRODUCTS_PATH = PROJECT_DIR / "data" / "products.jsonl"


class CatalogError(Exception):
    """Raised when a catalog operation is invalid."""


def _load() -> list[dict]:
    return [
        json.loads(line)
        for line in PRODUCTS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _save(products: list[dict]) -> None:
    PRODUCTS_PATH.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in products) + "\n",
        encoding="utf-8",
    )


def _next_product_id(products: list[dict]) -> str:
    ids = [int(p["product_id"][1:]) for p in products if p["product_id"].startswith("P") and p["product_id"][1:].isdigit()]
    return f"P{max(ids, default=-1) + 1:04d}"


def create_product(data: dict) -> dict:
    """新增商品，返回完整商品 dict。"""
    if not data.get("name") or not data.get("item_type") or not data.get("manufacturer") or data.get("price") is None:
        raise CatalogError("名称、类型、厂商、价格为必填")
    products = _load()
    product = {
        "product_id": _next_product_id(products),
        "name": str(data["name"]).strip(),
        "item_type": str(data["item_type"]).strip(),
        "manufacturer": str(data["manufacturer"]).strip(),
        "price": round(float(data["price"]), 2),
        "tags": [str(t).strip() for t in data.get("tags", []) if str(t).strip()],
        "description": str(data.get("description", "")).strip(),
    }
    products.append(product)
    _save(products)
    return product


def update_product(product_id: str, data: dict) -> dict:
    """更新商品的 name/price/tags/description 等字段。"""
    products = _load()
    for p in products:
        if p["product_id"] == product_id:
            for key in ("name", "item_type", "manufacturer", "description"):
                if key in data and data[key] is not None:
                    p[key] = str(data[key]).strip()
            if "price" in data and data["price"] is not None:
                p["price"] = round(float(data["price"]), 2)
            if "tags" in data and data["tags"] is not None:
                p["tags"] = [str(t).strip() for t in data["tags"] if str(t).strip()]
            _save(products)
            return p
    raise CatalogError("商品不存在")


def delete_product(product_id: str) -> None:
    """删除商品。"""
    products = _load()
    remaining = [p for p in products if p["product_id"] != product_id]
    if len(remaining) == len(products):
        raise CatalogError("商品不存在")
    _save(remaining)
