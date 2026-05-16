from __future__ import annotations

from pathlib import Path
import sqlite3
from urllib.parse import quote


PRODUCT_STATUSES = {
    "ready_for_zendrop",
    "rejected",
    "skipped_male",
    "skipped_not_women",
    "skipped_season",
    "zendrop_matched",
    "draft_ready",
    "uploaded_draft",
}


def get_summary(database: sqlite3.Connection) -> dict:
    status_counts = {
        status: count
        for status, count in database.execute(
            """
            select status, count(*)
            from competitor_products
            group by status
            order by status
            """
        ).fetchall()
    }
    zendrop_total = database.execute("select count(*) from zendrop_products").fetchone()[0]
    preview_cards_total = database.execute("select count(*) from product_matches").fetchone()[0]
    manual_approved_total = database.execute("select count(*) from product_matches where status = 'approved'").fetchone()[0]
    final_images_total = database.execute(
        "select count(*) from final_image_sets where status = 'ready' and generated_count >= 5"
    ).fetchone()[0]
    shopify_draft_total = database.execute("select count(*) from shopify_draft_products where media_count >= 5").fetchone()[0]
    competitor_total = sum(status_counts.values())
    ready_for_zendrop = status_counts.get("ready_for_zendrop", 0)
    return {
        "preview_cards_total": preview_cards_total,
        "competitor_total": competitor_total,
        "ready_for_zendrop": ready_for_zendrop,
        "zendrop_total": zendrop_total,
        "manual_approved_total": manual_approved_total,
        "final_images_total": final_images_total,
        "shopify_draft_total": shopify_draft_total,
        "status_counts": status_counts,
    }


def list_competitor_products(
    database: sqlite3.Connection,
    storage_dir: Path,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    parameters: list[object] = []
    where_clause = ""
    if status:
        where_clause = "where status = ?"
        parameters.append(status)
    parameters.append(limit)
    rows = database.execute(
        f"""
        select id, store_url, handle, title, product_type, price, image_url, image_path, status, updated_at
        from competitor_products
        {where_clause}
        order by
            case status
                when 'ready_for_zendrop' then 0
                when 'zendrop_matched' then 1
                when 'draft_ready' then 2
                else 9
            end,
            updated_at desc,
            id desc
        limit ?
        """,
        parameters,
    ).fetchall()
    return [
        {
            "id": product_id,
            "store_url": store_url,
            "handle": handle,
            "title": title,
            "product_type": product_type,
            "price": price,
            "source_image_url": source_image_url,
            "image_url": media_url_for_path(image_path, storage_dir),
            "status": product_status,
            "updated_at": updated_at,
        }
        for (
            product_id,
            store_url,
            handle,
            title,
            product_type,
            price,
            source_image_url,
            image_path,
            product_status,
            updated_at,
        ) in rows
    ]


def list_zendrop_products(database: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = database.execute(
        """
        select product_id, name, price_usd, image_url, shipping_country_code, shipping_price_usd,
            shipping_estimated_delivery, updated_at
        from zendrop_products
        order by updated_at desc, product_id desc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "product_id": product_id,
            "name": name,
            "price_usd": price_usd,
            "image_url": image_url,
            "shipping_country_code": shipping_country_code,
            "shipping_price_usd": shipping_price_usd,
            "shipping_estimated_delivery": shipping_estimated_delivery,
            "updated_at": updated_at,
        }
        for (
            product_id,
            name,
            price_usd,
            image_url,
            shipping_country_code,
            shipping_price_usd,
            shipping_estimated_delivery,
            updated_at,
        ) in rows
    ]


def update_competitor_product_status(database: sqlite3.Connection, product_id: int, status: str) -> dict | None:
    if status not in PRODUCT_STATUSES:
        raise ValueError(f"Unsupported product status: {status}")
    database.execute(
        """
        update competitor_products
        set status = ?, updated_at = current_timestamp
        where id = ?
        """,
        (status, product_id),
    )
    database.commit()
    row = database.execute(
        """
        select id, handle, title, status, updated_at
        from competitor_products
        where id = ?
        """,
        (product_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "handle": row[1],
        "title": row[2],
        "status": row[3],
        "updated_at": row[4],
    }


def build_pipeline_steps(summary: dict) -> list[dict]:
    competitor_done = summary["competitor_total"] > 0
    ready_done = summary["ready_for_zendrop"] > 0
    zendrop_done = summary["zendrop_total"] > 0
    preview_done = summary["preview_cards_total"] > 0
    manual_approved_done = summary.get("manual_approved_total", 0) > 0
    final_images_done = summary.get("final_images_total", 0) > 0
    shopify_done = summary.get("shopify_draft_total", 0) > 0
    return [
        {
            "key": "competitor",
            "title": "1. Sources",
            "state": "done" if competitor_done else "active",
            "metric": summary["competitor_total"],
        },
        {
            "key": "zendrop_search",
            "title": "2. Zendrop search",
            "state": "done" if zendrop_done else "active" if competitor_done else "locked",
            "metric": summary["zendrop_total"],
        },
        {
            "key": "approval_preview",
            "title": "3. Match preview",
            "state": "done" if preview_done else "active" if ready_done and zendrop_done else "locked",
            "metric": summary["preview_cards_total"],
        },
        {
            "key": "approval",
            "title": "4. Approval",
            "state": "done" if manual_approved_done else "active" if preview_done else "locked",
            "metric": summary.get("manual_approved_total", 0),
        },
        {
            "key": "content",
            "title": "5. Product enhancer",
            "state": "active" if manual_approved_done else "locked",
            "metric": summary.get("manual_approved_total", 0),
        },
        {
            "key": "images",
            "title": "6. Image enhancer",
            "state": "done" if final_images_done else "active" if manual_approved_done else "locked",
            "metric": summary.get("final_images_total", 0),
        },
        {
            "key": "shopify",
            "title": "7. Draft upload",
            "state": "done" if shopify_done else "active" if final_images_done else "locked",
            "metric": summary.get("shopify_draft_total", 0),
        },
    ]


def media_url_for_path(image_path: str | None, storage_dir: Path) -> str | None:
    if not image_path:
        return None
    path = Path(image_path)
    try:
        relative_path = path.relative_to(storage_dir)
    except ValueError:
        try:
            relative_path = path.relative_to(storage_dir.resolve())
        except (OSError, ValueError):
            parts = path.parts
            if "storage" in parts:
                storage_index = parts.index("storage")
                relative_path = Path(*parts[storage_index + 1 :])
            else:
                relative_path = Path(path.name)
    return "/media/" + quote(relative_path.as_posix())
