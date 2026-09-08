import ast
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATABASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = DATABASE_DIR / "munder_difflin.db"
db_engine = create_engine(f"sqlite:///{DATABASE_PATH.as_posix()}")

with (DATABASE_DIR / "product_catalogue.json").open(encoding="utf-8") as catalogue_file:
    paper_supplies = json.load(catalogue_file)


def generate_sample_inventory(paper_supplies: list, coverage: float = 0.4, seed: int = 137) -> pd.DataFrame:
    """Generate a reproducible sample inventory from the product catalogue."""
    np.random.seed(seed)
    num_items = int(len(paper_supplies) * coverage)
    selected_indices = np.random.choice(
        range(len(paper_supplies)),
        size=num_items,
        replace=False,
    )

    inventory = []
    for index in selected_indices:
        item = paper_supplies[index]
        inventory.append({
            "item_name": item["item_name"],
            "category": item["category"],
            "unit_price": item["unit_price"],
            "current_stock": np.random.randint(200, 800),
            "min_stock_level": np.random.randint(50, 150),
        })

    return pd.DataFrame(inventory)


def init_database(database_engine: Engine = db_engine, seed: int = 137) -> Engine:
    """Create the database tables and seed their initial records."""
    try:
        transactions_schema = pd.DataFrame({
            "id": [],
            "item_name": [],
            "transaction_type": [],
            "units": [],
            "price": [],
            "transaction_date": [],
        })
        transactions_schema.to_sql("transactions", database_engine, if_exists="replace", index=False)

        initial_date = datetime(2025, 1, 1).isoformat()
        quote_requests_df = pd.read_csv(PROJECT_DIR / "quote_requests.csv")
        quote_requests_df["id"] = range(1, len(quote_requests_df) + 1)
        quote_requests_df.to_sql("quote_requests", database_engine, if_exists="replace", index=False)

        quotes_df = pd.read_csv(PROJECT_DIR / "quotes.csv")
        quotes_df["request_id"] = range(1, len(quotes_df) + 1)
        quotes_df["order_date"] = initial_date

        if "request_metadata" in quotes_df.columns:
            quotes_df["request_metadata"] = quotes_df["request_metadata"].apply(
                lambda value: ast.literal_eval(value) if isinstance(value, str) else value
            )
            quotes_df["job_type"] = quotes_df["request_metadata"].apply(lambda value: value.get("job_type", ""))
            quotes_df["order_size"] = quotes_df["request_metadata"].apply(lambda value: value.get("order_size", ""))
            quotes_df["event_type"] = quotes_df["request_metadata"].apply(lambda value: value.get("event_type", ""))

        quotes_df = quotes_df[[
            "request_id",
            "total_amount",
            "quote_explanation",
            "order_date",
            "job_type",
            "order_size",
            "event_type",
        ]]
        quotes_df.to_sql("quotes", database_engine, if_exists="replace", index=False)

        inventory_df = generate_sample_inventory(paper_supplies, seed=seed)
        initial_transactions = [{
            "item_name": None,
            "transaction_type": "sales",
            "units": None,
            "price": 50000.0,
            "transaction_date": initial_date,
        }]

        for _, item in inventory_df.iterrows():
            initial_transactions.append({
                "item_name": item["item_name"],
                "transaction_type": "stock_orders",
                "units": item["current_stock"],
                "price": item["current_stock"] * item["unit_price"],
                "transaction_date": initial_date,
            })

        pd.DataFrame(initial_transactions).to_sql(
            "transactions", database_engine, if_exists="append", index=False
        )
        inventory_df.to_sql("inventory", database_engine, if_exists="replace", index=False)
        return database_engine
    except Exception as error:
        print(f"Error initializing database: {error}")
        raise


if __name__ == "__main__":
    init_database()
    print(f"Database initialized.")