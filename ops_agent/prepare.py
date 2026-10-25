"""Download provenance, raw-data audit and explicitly grained analytical tables."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/processed/olist.sqlite"
SOURCES = {
    "brazilian-ecommerce": "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
    "marketing-funnel-olist": "https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist",
}
NAMES = {
    "olist_customers_dataset.csv": "customers",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "items",
    "olist_order_payments_dataset.csv": "payments",
    "olist_order_reviews_dataset.csv": "reviews",
    "olist_orders_dataset.csv": "orders",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "product_category_name_translation.csv": "categories",
    "olist_closed_deals_dataset.csv": "deals",
    "olist_marketing_qualified_leads_dataset.csv": "leads",
}
KEYS = {
    "customers": ["customer_id"], "orders": ["order_id"],
    "items": ["order_id", "order_item_id"],
    "payments": ["order_id", "payment_sequential"],
    "products": ["product_id"], "sellers": ["seller_id"],
    "categories": ["product_category_name"], "leads": ["mql_id"],
    "deals": ["mql_id"],
}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def load_raw():
    import pandas as pd
    tables, manifest = {}, []
    for dataset, url in SOURCES.items():
        archive = ROOT / f"data/raw/{dataset}.zip"
        if not archive.is_file():
            raise FileNotFoundError(f"缺少官方下载包：{archive}")
        entry = {
            "dataset": dataset, "official_source": url,
            "download_endpoint": f"https://www.kaggle.com/api/v1/datasets/download/olistbr/{dataset}",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "bytes": archive.stat().st_size,
            "license": "CC BY-NC-SA 4.0 (as listed on official data card)",
            "files": [],
        }
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:
                raise ValueError(f"损坏的压缩包：{archive.name}")
            for filename in z.namelist():
                if filename not in NAMES:
                    raise ValueError(f"未预期的文件：{filename}")
                content = z.read(filename)
                target = ROOT / "data/raw" / filename
                if target.exists() and hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(content).digest():
                    raise ValueError(f"原始文件已有不同内容，不覆盖：{target}")
                if not target.exists():
                    target.write_bytes(content)
                tables[NAMES[filename]] = pd.read_csv(target, dtype={c: "string" for c in [
                    "order_id", "customer_id", "customer_unique_id", "seller_id", "product_id",
                    "review_id", "mql_id", "customer_zip_code_prefix", "seller_zip_code_prefix",
                    "geolocation_zip_code_prefix", "sdr_id", "sr_id", "landing_page_id",
                ]})
                entry["files"].append({"name": filename, "sha256": hashlib.sha256(content).hexdigest()})
        manifest.append(entry)
    return tables, manifest


def audit(tables):
    import pandas as pd
    result = {"generated_utc": datetime.now(timezone.utc).isoformat(), "tables": {}, "relations": [], "warnings": []}
    for name, df in tables.items():
        key = KEYS.get(name)
        result["tables"][name] = {
            "rows": len(df), "columns": list(df.columns),
            "exact_duplicate_rows": int(df.duplicated().sum()),
            "candidate_key": key,
            "duplicate_key_rows": int(df.duplicated(key).sum()) if key else None,
            "null_key_rows": int(df[key].isna().any(axis=1).sum()) if key else None,
            "nulls": {c: int(n) for c, n in df.isna().sum().items() if n},
        }
    relations = [
        ("orders", "customer_id", "customers", "customer_id"),
        ("items", "order_id", "orders", "order_id"),
        ("items", "product_id", "products", "product_id"),
        ("items", "seller_id", "sellers", "seller_id"),
        ("payments", "order_id", "orders", "order_id"),
        ("reviews", "order_id", "orders", "order_id"),
        ("products", "product_category_name", "categories", "product_category_name"),
        ("deals", "mql_id", "leads", "mql_id"),
        ("deals", "seller_id", "sellers", "seller_id"),
    ]
    for child, ck, parent, pk in relations:
        values = tables[child][ck]
        present = values.notna()
        matched = values.isin(tables[parent][pk].dropna()) & present
        result["relations"].append({
            "from": f"{child}.{ck}", "to": f"{parent}.{pk}",
            "non_null_rows": int(present.sum()), "null_rows": int((~present).sum()),
            "unmatched_rows": int((present & ~matched).sum()),
            "matched_rows": int(matched.sum()),
        })
    orders, items, payments, reviews = (tables[n] for n in ["orders", "items", "payments", "reviews"])
    purchase = pd.to_datetime(orders.order_purchase_timestamp, errors="coerce")
    delivery = pd.to_datetime(orders.order_delivered_customer_date, errors="coerce")
    estimate = pd.to_datetime(orders.order_estimated_delivery_date, errors="coerce")
    order_month = purchase.dt.strftime("%Y-%m")
    monthly = orders.assign(month=order_month).groupby(["month", "order_status"]).size().unstack(fill_value=0)
    result["purchase_range"] = [str(purchase.min()), str(purchase.max())]
    result["last_observed_delivery"] = str(delivery.max())
    result["monthly_status_counts"] = {idx: {c: int(v) for c, v in row.items()} for idx, row in monthly.to_dict("index").items()}
    result["special_cases"] = {
        "orders_without_items": int((~orders.order_id.isin(items.order_id)).sum()),
        "orders_without_payments": int((~orders.order_id.isin(payments.order_id)).sum()),
        "orders_without_reviews": int((~orders.order_id.isin(reviews.order_id)).sum()),
        "orders_with_multiple_items": int((items.groupby("order_id").size() > 1).sum()),
        "orders_with_multiple_sellers": int((items.groupby("order_id").seller_id.nunique() > 1).sum()),
        "orders_with_multiple_payments": int((payments.groupby("order_id").size() > 1).sum()),
        "orders_with_multiple_reviews": int((reviews.groupby("order_id").size() > 1).sum()),
        "orders_with_conflicting_review_scores": int((reviews.groupby("order_id").review_score.nunique() > 1).sum()),
        "delivery_before_purchase": int((delivery < purchase).sum()),
        "delivered_missing_actual_date": int(((orders.order_status == "delivered") & delivery.isna()).sum()),
        "missing_estimated_date": int(estimate.isna().sum()),
        "nonpositive_item_prices": int((items.price <= 0).sum()),
        "negative_freight": int((items.freight_value < 0).sum()),
        "invalid_purchase_timestamps": int((orders.order_purchase_timestamp.notna() & purchase.isna()).sum()),
        "invalid_delivery_timestamps": int((orders.order_delivered_customer_date.notna() & delivery.isna()).sum()),
        "invalid_estimate_timestamps": int((orders.order_estimated_delivery_date.notna() & estimate.isna()).sum()),
        "review_score_outside_1_to_5": int((~reviews.review_score.between(1, 5)).sum()),
    }
    cents = lambda s: (s * 100).round().astype("int64")
    expected = items.assign(total=cents(items.price) + cents(items.freight_value)).groupby("order_id").total.sum()
    actual = payments.assign(total=cents(payments.payment_value)).groupby("order_id").total.sum()
    common = expected.index.intersection(actual.index)
    discrepancy = (expected.loc[common] - actual.loc[common]).abs()
    result["payment_reconciliation"] = {
        "orders_with_both": len(common), "difference_over_1_cent": int((discrepancy > 1).sum()),
        "max_absolute_difference_brl": float(discrepancy.max() / 100),
        "note": "商品金额、运费及支付额保留各自口径，不将差异直接视为退款或利润。",
    }
    # Demonstrate fanout from a naive raw join without materializing a huge join.
    pay_count = payments.groupby("order_id").size()
    review_count = reviews.groupby("order_id").size()
    factor = items.order_id.map(pay_count).fillna(1) * items.order_id.map(review_count).fillna(1)
    result["join_fanout"] = {
        "raw_item_amount_brl": float(cents(items.price).sum() / 100),
        "naive_items_payments_reviews_left_join_brl": float((cents(items.price) * factor).sum() / 100),
    }
    deals = tables["deals"]
    trading = deals.seller_id.isin(items.seller_id)
    result["marketing_linkage"] = {
        "mql_rows": len(tables["leads"]), "closed_deal_rows": len(deals),
        "unique_closed_sellers": int(deals.seller_id.nunique()),
        "closed_sellers_with_observed_order_items": int(deals.loc[trading, "seller_id"].nunique()),
        "closed_sellers_not_observed_in_items": int(deals.loc[~trading, "seller_id"].nunique()),
        "first_contact_range": [str(tables["leads"].first_contact_date.min()), str(tables["leads"].first_contact_date.max())],
        "won_range": [str(deals.won_date.min()), str(deals.won_date.max())],
        "note": "线索为抽样数据；未关联不能等同未经营。成交日期超出订单覆盖范围时不可评估后续表现。",
    }
    result["warnings"] = [
        "历史公开数据不是实时业务；首尾月份不保证完整，默认分析区间显式限定。",
        "主数据与商家线索是不同观察窗口，不能拼成消费者转化漏斗。",
        "一单多商品/支付/评价会产生联表膨胀，必须先归并到所需粒度。",
        "评价和最终送达时间属于订单级，多商家订单不能直接归责给单个商家。",
        "缺少成本、访问、广告支出和运营实验，无法计算利润、访问转化或干预因果效果。",
    ]
    return result


def build_db(tables):
    import pandas as pd
    DB.parent.mkdir(parents=True, exist_ok=True)
    staging = DB.with_suffix(".building.sqlite")
    with sqlite3.connect(staging) as con:
        for name, df in tables.items():
            df.to_sql("raw_" + name, con, if_exists="replace", index=False, chunksize=10000)
        items = tables["items"].copy()
        items["item_cents"] = (items.price * 100).round().astype("int64")
        items["freight_cents"] = (items.freight_value * 100).round().astype("int64")
        items = items.merge(tables["products"][["product_id", "product_category_name"]], on="product_id", validate="many_to_one")
        items = items.merge(tables["categories"], on="product_category_name", how="left", validate="many_to_one")
        items["category"] = items.product_category_name_english.fillna(items.product_category_name).fillna("未分类")
        items.to_sql("fact_items", con, if_exists="replace", index=False)
        agg = items.groupby("order_id").agg(item_cents=("item_cents", "sum"), freight_cents=("freight_cents", "sum"),
                                                 item_count=("order_item_id", "count"), seller_count=("seller_id", "nunique"))
        p = tables["payments"].copy()
        p["payment_cents"] = (p.payment_value * 100).round().astype("int64")
        p = p.groupby("order_id").agg(payment_cents=("payment_cents", "sum"), payment_count=("payment_sequential", "count"))
        r = tables["reviews"].copy()
        r = r.sort_values(["order_id", "review_answer_timestamp", "review_id", "review_score"], kind="stable")
        r = r.drop_duplicates("order_id", keep="last")[["order_id", "review_score"]]
        facts = tables["orders"].merge(tables["customers"], on="customer_id", validate="one_to_one")
        facts = facts.merge(agg, on="order_id", how="left", validate="one_to_one").merge(p, on="order_id", how="left", validate="one_to_one")
        facts = facts.merge(r, on="order_id", how="left", validate="one_to_one")
        facts["purchase_date"] = facts.order_purchase_timestamp.str[:10]
        facts["month"] = facts.order_purchase_timestamp.str[:7]
        facts["delivery_date"] = facts.order_delivered_customer_date.str[:10]
        facts["estimated_date"] = facts.order_estimated_delivery_date.str[:10]
        facts["delivery_valid"] = ((facts.order_status == "delivered") & facts.delivery_date.notna() &
                                   facts.estimated_date.notna() & (facts.delivery_date >= facts.purchase_date)).astype(int)
        facts["late"] = pd.Series(pd.NA, index=facts.index, dtype="Int64")
        mask = facts.delivery_valid == 1
        facts.loc[mask, "late"] = (facts.loc[mask, "delivery_date"] > facts.loc[mask, "estimated_date"]).astype(int)
        facts["delivery_days"] = (pd.to_datetime(facts.delivery_date) - pd.to_datetime(facts.purchase_date)).dt.days
        facts.to_sql("fact_orders", con, if_exists="replace", index=False)
        con.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS orders_pk ON fact_orders(order_id);
        CREATE INDEX IF NOT EXISTS orders_period ON fact_orders(purchase_date);
        CREATE INDEX IF NOT EXISTS items_order ON fact_items(order_id);
        CREATE INDEX IF NOT EXISTS items_seller ON fact_items(seller_id);
        CREATE INDEX IF NOT EXISTS raw_orders_pk ON raw_orders(order_id);
        """)
        # Atomic metadata alongside the analytical build, not an audit-fed metric dependency.
        con.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", [
            ("default_month", "2018-07"), ("supported_start", "2017-01"), ("supported_end", "2018-08"),
            ("default_note", "2018-07是保守演示窗口，不宣称已证明截数完整；2018-08有成熟度风险。"),
        ])
    con.close()
    staging.replace(DB)


def main():
    tables, manifest = load_raw()
    result = audit(tables)
    for name, info in result["tables"].items():
        if info["duplicate_key_rows"] or info["null_key_rows"]:
            raise ValueError(f"候选键重复或为空，先处理再建模：{name}")
    for relation in result["relations"]:
        if relation["from"] in ["orders.customer_id", "items.order_id", "items.product_id", "items.seller_id"]:
            if relation["unmatched_rows"] or relation["null_rows"]:
                raise ValueError(f"核心关联缺失，不能静默丢行：{relation['from']}")
    build_db(tables)
    save_json(ROOT / "reports/source_manifest.json", manifest)
    save_json(ROOT / "reports/data_quality.json", result)
    print(json.dumps({"tables": {k: v["rows"] for k, v in result["tables"].items()},
                      "special_cases": result["special_cases"], "marketing": result["marketing_linkage"],
                      "fanout": result["join_fanout"], "db": str(DB)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
