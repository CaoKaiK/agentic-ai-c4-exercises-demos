import sys
from pathlib import Path

import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Union
from sqlalchemy.sql import text

# Add the project root when this module is executed as a script.
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from database.init_db import db_engine, init_database


def create_transaction(
    item_name: str,
    transaction_type: str,
    quantity: int,
    price: float,
    date: Union[str, datetime],
) -> int:
    """Record a stock order or sales transaction in the database."""
    try:
        date_str = date.isoformat() if isinstance(date, datetime) else date
        if transaction_type not in {"stock_orders", "sales"}:
            raise ValueError("Transaction type must be 'stock_orders' or 'sales'")

        transaction = pd.DataFrame([{
            "item_name": item_name,
            "transaction_type": transaction_type,
            "units": quantity,
            "price": price,
            "transaction_date": date_str,
        }])
        transaction.to_sql("transactions", db_engine, if_exists="append", index=False)
        result = pd.read_sql("SELECT last_insert_rowid() as id", db_engine)
        return int(result.iloc[0]["id"])
    except Exception as error:
        print(f"Error creating transaction: {error}")
        raise


def get_all_inventory(as_of_date: str) -> Dict[str, int]:
    """Retrieve positive inventory quantities as of a given date."""
    query = """
        SELECT
            item_name,
            SUM(CASE
                WHEN transaction_type = 'stock_orders' THEN units
                WHEN transaction_type = 'sales' THEN -units
                ELSE 0
            END) as stock
        FROM transactions
        WHERE item_name IS NOT NULL
        AND transaction_date <= :as_of_date
        GROUP BY item_name
        HAVING stock > 0
    """
    result = pd.read_sql(query, db_engine, params={"as_of_date": as_of_date})
    return dict(zip(result["item_name"], result["stock"]))

def get_item_price(item_name: str) -> float:
    """Retrieve the price of an item from the inventory."""
    query = """
        SELECT unit_price
        FROM inventory
        WHERE item_name = :item_name
        LIMIT 1
    """
    result = pd.read_sql(query, db_engine, params={"item_name": item_name})
    if result.empty:
        raise ValueError(f"Item not found in inventory: {item_name}")
    return float(result.iloc[0]["unit_price"])
  

def get_stock_level(item_name: str, as_of_date: Union[str, datetime]) -> pd.DataFrame:
    """Retrieve the stock level of an item as of a given date."""
    if isinstance(as_of_date, datetime):
        as_of_date = as_of_date.isoformat()

    stock_query = """
        SELECT
            item_name,
            COALESCE(SUM(CASE
                WHEN transaction_type = 'stock_orders' THEN units
                WHEN transaction_type = 'sales' THEN -units
                ELSE 0
            END), 0) AS current_stock
            , (SELECT min_stock_level
               FROM inventory
               WHERE inventory.item_name = :item_name
               LIMIT 1) AS min_stock_level
        FROM transactions
        WHERE item_name = :item_name
        AND transaction_date <= :as_of_date
    """
    return pd.read_sql(
        stock_query,
        db_engine,
        params={"item_name": item_name, "as_of_date": as_of_date},
    ).iloc[0].to_dict()


def get_supplier_delivery_date(input_date_str: str, quantity: int) -> str:
    """Estimate supplier delivery date from order date and quantity."""
    print(f"FUNC (get_supplier_delivery_date): Calculating for qty {quantity} from date string '{input_date_str}'")
    try:
        input_date_dt = datetime.fromisoformat(input_date_str.split("T")[0])
    except (ValueError, TypeError):
        print(f"WARN (get_supplier_delivery_date): Invalid date format '{input_date_str}', using today as base.")
        input_date_dt = datetime.now()

    if quantity <= 10:
        days = 0
    elif quantity <= 100:
        days = 1
    elif quantity <= 1000:
        days = 4
    else:
        days = 7

    return (input_date_dt + timedelta(days=days)).strftime("%Y-%m-%d")


def get_cash_balance(as_of_date: Union[str, datetime]) -> float:
    """Calculate the cash balance as of a specified date."""
    try:
        if isinstance(as_of_date, datetime):
            as_of_date = as_of_date.isoformat()

        transactions = pd.read_sql(
            "SELECT * FROM transactions WHERE transaction_date <= :as_of_date",
            db_engine,
            params={"as_of_date": as_of_date},
        )
        if not transactions.empty:
            total_sales = transactions.loc[transactions["transaction_type"] == "sales", "price"].sum()
            total_purchases = transactions.loc[transactions["transaction_type"] == "stock_orders", "price"].sum()
            return float(total_sales - total_purchases)
        return 0.0
    except Exception as error:
        print(f"Error getting cash balance: {error}")
        return 0.0


def generate_financial_report(as_of_date: Union[str, datetime]) -> Dict:
    """Generate a financial and inventory report as of a specified date."""
    if isinstance(as_of_date, datetime):
        as_of_date = as_of_date.isoformat()

    cash = get_cash_balance(as_of_date)
    inventory_df = pd.read_sql("SELECT * FROM inventory", db_engine)
    inventory_value = 0.0
    inventory_summary = []

    for _, item in inventory_df.iterrows():
        stock_info = get_stock_level(item["item_name"], as_of_date)
        stock = stock_info["current_stock"]
        item_value = stock * item["unit_price"]
        inventory_value += item_value
        inventory_summary.append({
            "item_name": item["item_name"],
            "stock": stock,
            "unit_price": item["unit_price"],
            "value": item_value,
        })

    top_sales_query = """
        SELECT item_name, SUM(units) as total_units, SUM(price) as total_revenue
        FROM transactions
        WHERE transaction_type = 'sales' AND transaction_date <= :date
        GROUP BY item_name
        ORDER BY total_revenue DESC
        LIMIT 5
    """
    top_sales = pd.read_sql(top_sales_query, db_engine, params={"date": as_of_date})
    return {
        "as_of_date": as_of_date,
        "cash_balance": cash,
        "inventory_value": inventory_value,
        "total_assets": cash + inventory_value,
        "inventory_summary": inventory_summary,
        "top_selling_products": top_sales.to_dict(orient="records"),
    }


def search_quote_history(search_terms: List[str], limit: int = 5) -> List[Dict]:
    """Retrieve historical quotes matching the supplied search terms."""
    conditions = []
    params = {}
    for index, term in enumerate(search_terms):
        param_name = f"term_{index}"
        conditions.append(
            f"(LOWER(qr.response) LIKE :{param_name} OR "
            f"LOWER(q.quote_explanation) LIKE :{param_name})"
        )
        params[param_name] = f"%{term.lower()}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    query = f"""
        SELECT
            qr.response AS original_request,
            q.total_amount,
            q.quote_explanation,
            q.job_type,
            q.order_size,
            q.event_type,
            q.order_date
        FROM quotes q
        JOIN quote_requests qr ON q.request_id = qr.id
        WHERE {where_clause}
        ORDER BY q.order_date DESC
        LIMIT {limit}
    """
    with db_engine.connect() as connection:
        result = connection.execute(text(query), params)
        return [dict(row._mapping) for row in result]


if __name__ == "__main__":
    init_database()
    print("-" * 40)
    print(get_all_inventory("2026-08-09"))
    print("-" * 40)
    print(get_stock_level("A4 paper", "2026-08-09"))
    print("-" * 40)
    print(get_item_price("A4 paper", "sales"))
    print(get_item_price("A4 paper", "stock_orders"))
    print("-" * 40)
    print(get_supplier_delivery_date("2026-08-09", 1001))
    print("-" * 40)
    print(get_cash_balance("2026-08-09"))
    print("-" * 40)
    print(generate_financial_report("2026-08-09"))
    print("-" * 40)
    print(search_quote_history(["A4 paper"], limit=5))