import os
import time
import json
from dotenv import find_dotenv, load_dotenv
from datetime import datetime, timedelta
from typing import Dict, List, Union
import pandas as pd

import mlflow
from mlflow.entities import SpanType
from database.init_db import init_database

from utils.utils import (
    create_transaction,
    get_all_inventory,
    get_item_price,
    get_stock_level,
    get_supplier_delivery_date,
    get_cash_balance,
    generate_financial_report,
    search_quote_history,
)

load_dotenv(find_dotenv(), override=True)
openai_api_key = os.getenv("OPENAI_API_KEY")
tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")

mlflow.set_tracking_uri(tracking_uri)
mlflow.set_experiment("munder-difflin")

# Enable the MLflow integration before importing and using smolagents.
mlflow.smolagents.autolog()
from smolagents import OpenAIServerModel, ToolCallingAgent, tool

# Load environment configuration before resolving the tracking URI.
load_dotenv(find_dotenv(), override=True)
openai_api_key = os.getenv("OPENAI_API_KEY")

# Model
model = OpenAIServerModel(
    model_id="gpt-4.1-mini",
    api_base="https://openai.vocareum.com/v1",
    api_key=openai_api_key,
)

DAY = "2025-03-01"


@tool
# @mlflow.trace(name="get_inventory_list", span_type=SpanType.TOOL)
def get_inventory_list(date: str) -> list:
    """Get the list of all inventory items available on a specific date.
    Args:
        date (str): The date for which to retrieve the inventory list. Must be in format YYYY-MM-DD

    Returns:
        list: A list of dictionaries containing item names and their stock levels.
    """
    inventory = get_all_inventory(date)
    return [
        {"item": item_name, "stock": int(stock)}
        for item_name, stock in inventory.items()
    ]


@tool
# @mlflow.trace(name="check_inventory", span_type=SpanType.TOOL)
def check_inventory(item: str, quantity: int, date: str) -> dict:
    """Check if the specified item is available in the inventory.
    Args:
        item (str): The name of the item to check in the inventory.
        quantity (int): The quantity of the item to check in the inventory.
        date (str): The date for which to check the inventory availability. Must be in format YYYY-MM-DD

    Returns:
        dict: A dictionary containing the item, stock level, resupply amount, and the date of the check.
    """

    stock_info = get_stock_level(item, date)
    stock = stock_info["current_stock"]
    min_stock_level = stock_info["min_stock_level"]


    if stock - quantity >= min_stock_level:
        return {"item": item, "stock": stock, "resupply": 0, "date": date}
    else:
        # If the requested quantity exceeds the available stock beyond the minimum stock level, resupply is needed.
        resupply_amount = quantity + min_stock_level - stock
        print(resupply_amount)
        return {"item": item, "stock": stock, "resupply": resupply_amount}


@tool
# @mlflow.trace(name="get_delivery_date", span_type=SpanType.TOOL)
def get_delivery_date(date: str, quantity: int) -> str:
    """Get the estimated delivery date for a specific order.
    Args:
        date (str): The date when the order is placed. Must be in format YYYY-MM-DD
        quantity (int): The quantity of items in the order.

    Returns:
        str: The estimated delivery date in format YYYY-MM-DD.
    """
    date = get_supplier_delivery_date(DAY, quantity)
    return date


class InventoryAgent(ToolCallingAgent):
    """Agent responsible for managing inventory-related tasks."""

    def __init__(self, model):
        super().__init__(
            tools=[get_inventory_list, check_inventory, get_delivery_date],
            model=model,
            name="InventoryAgent",
            description="""
            You are an inventory management agent responsible for checking stock levels and availability of items in the inventory.
            
            You will receive requests containing the item name, quantity, the date of the request and the expected delivery date.
            """,
            instructions="""
            You will receive requests containing the item name, quantity, the date of the request, and the expected delivery date.
            Make sure to match the name of the requested item exactly with the name in the inventory list.

            1. Always call get_inventory_list for the specified date using the get_inventory_list tool.
            2. Always match the requested item name with the items in the inventory list.
            3. Always use the check_inventory tool to verify the stock level and availability.
            4. Always check the estimated delivery date using the get_delivery_date tool if resupply is needed.
            5. Compare the estimated delivery date with the customer's expected delivery date and decide if the order can be fulfilled on time.

            Make sure the item names in your response match exactly with the names in the inventory list. Only provide the following information in your response:
            Order No. (e.g., 001, 002, 003, ...)
            - item_name
            - quantity (0 - inf)
            - stock (0 - inf)
            - resupply (0 - inf)
            - estimated_delivery_date (YYYY-MM-DD or N/A)

            No other explanation. The 

            """,
        )


@tool
def get_item_price_tool(item_name: str) -> float:
    """
    Retrieve the price of a specific item using the get_item_price function.

    Args:
        item_name (str): The name of the item to retrieve the price for.

    Returns:
        float: The price of the specified item.
    """
    return get_item_price(item_name)


@tool
def search_quote_history_tool(item_name: str) -> list:
    """
    Search the quote history for a specific item using the search_quote_history function.

    Args:
        item_name (str): The name of the item to search for in the quote history.

    Returns:
        list: A list of quotes related to the specified item.
    """
    return search_quote_history(item_name)


class QuotationAgent(ToolCallingAgent):
    """Agent responsible for generating quotations for customer orders."""

    def __init__(self, model):
        super().__init__(
            tools=[get_item_price_tool, search_quote_history_tool],
            model=model,
            name="QuotationAgent",
            description="""
            You are a quotation agent responsible for generating price quotations for customer orders.
            """,
            instructions="""
            You will receive requests containing the item name, quantity, and the date for which to generate a quotation. Forget any previous requests and focus only on the current one.
            Always provide the total price based on the quantity and any applicable discounts.

            
            1. Use the get_item_price_tool to retrieve the price for the item
            2. Use the search_quote_history_tool to check for previous quotes for the item.
            3. If the price is above 1000$ apply 10 per cent discount and always round down to the nearest 5$
            4. Return the total price to the requester and explain the discount method.

            """,
        )


@tool
def create_transaction_tool(
    item_name: str,
    transaction_type: str,
    quantity: int,
) -> dict:
    """
    Create a transaction for a specific item using the create_transaction function.

    Args:
        item_name (str): The name of the item for the transaction.
        transaction_type (str): The type of transaction (must be 'stock_orders' or 'sales').
        quantity (int): The quantity of the item involved in the transaction.
    Returns:
        int: The ID of the created transaction.
    """
    if transaction_type in ["sale", "sales", "selling"]:
        transaction_type = "sales"
    if transaction_type in ["stock_order", "stock_orders", "restock"]:
        transaction_type = "stock_orders"
    price = get_item_price(item_name)
    price *= quantity
    balance = get_cash_balance(DAY)
    if transaction_type == "stock_orders":
        if balance < price:
            raise ValueError("Insufficient balance for stock order.")
        else:
            price *= 0.9
            transaction_id = create_transaction(
                item_name, transaction_type, quantity, price, DAY
            )

    else:
        transaction_id = create_transaction(
            item_name, transaction_type, quantity, price, DAY
        )
    return transaction_id


class TransactionAgent(ToolCallingAgent):
    """Agent responsible for handling transactions related to customer orders."""

    def __init__(self, model):
        super().__init__(
            tools=[create_transaction_tool],
            model=model,
            name="TransactionAgent",
            description="""
            You are a transaction agent responsible for handling financial transactions for customer orders.
            """,
            instructions="""
            You will receive information in format:
            - item_name (str): The name of the item for the transaction.
            - quantity (int): The quantity of the item for the transaction.
            - resupply (int): The resupply quantity for the transaction.
            
            Extract all important details from them to create the ALL the necessary transactions.

            1.  Always check if the items needs to be resupplied. Call the create_transaction_tool to create a stock_orders transaction. Use User "stock_orders" as the transaction_type.
            2   Transact the order for the item. Use "sales" as the transaction_type.
            3.  Check that you have identified all necessary transactions before completing the process.

            Sometimes only one "sales" transaction is needed without a preceding "stock_orders" transaction. There is never the need for two "stock_orders" transactions or two "sales" transactions.


            """,
        )


@tool
def validate_list(order_list: list) -> bool:
    """Validate that the order list contains all necessary details for each item.

    Args:
        order_list (list): The list of items in the order.

    Returns:
        bool: True if all items have the required details, False otherwise.
    """
    required_keys = {"item_name", "quantity", "request_date", "fulfillment_date"}
    for item in order_list:
        if not required_keys.issubset(item.keys()):
            return False
        # try:
        #     get_item_price(item["item_name"])
        # except ValueError:
        #     return False

    return True


class OrderDecompositionAgent(ToolCallingAgent):
    """Agent responsible for decomposing customer orders into individual items and their details."""

    def __init__(self, model):
        super().__init__(
            tools=[get_inventory_list, validate_list],
            model=model,
            name="OrderDecompositionAgent",
            description="""
            You are an order decomposition agent responsible for breaking down customer orders into individual items and their details.
            """,
            instructions="""
            You will receive a customer order as input. Extract all important details for each item in the order: 
                - customer_item_name
                - quantity
                - request_date
                - fulfillment_date

            Steps:
            1. Get the inventory list with the get_inventory_list tool.
            2. Try to match the customer_item_name with the items in the inventory list. Also allow for close matches or alternative names.
            3. If a match is found, use the corresponding item_name from the inventory list. Forget the customer_item_name. If no match is found set "offered" to False.
            3. Use the following keys for the dict:
                - "item_name"
                - "offered"
                - "quantity"
                - "request_date"
                - "fulfillment_date"
            4. Validate the list using the validate_list tool before proceeding.

            Always send only the validated list of item details as JSON. Nothing else. Return raw json only, no markdown, no explanation.

            """,
        )


class OrchestrationAgent(ToolCallingAgent):
    """Orchestration agent that manages multiple sub-agents and coordinates their actions."""

    def __init__(self, model):
        self.model = model

        self.order_decomposition_agent = OrderDecompositionAgent(model)
        self.inventory_agent = InventoryAgent(model)
        self.quotation_agent = QuotationAgent(model)
        self.transaction_agent = TransactionAgent(model)

        @tool
        # @mlflow.trace(name="call_inventory_agent", span_type=SpanType.TOOL)
        def call_inventory_agent(item_name: str, quantity: int, request_date: str, delivery_date: str) -> str:
            """Call the InventoryAgent to check the inventory for a specific item.
            Args:
                item_name (str): The name of the item to check in the inventory.
                quantity (int): The quantity of the item to check.
                request_date (str): The day that the request is made.
                delivery_date (str): The expected delivery date for the item.

            Returns:
                str: The response from the InventoryAgent.
            """
            return self.inventory_agent.run(
                f"Check inventory for this item: {item_name}, quantity: {quantity}, request date: {request_date}, delivery date: {delivery_date} - Always follow your instructions."
            )

        @tool
        def call_quotation_agent(item_name: str, quantity: int) -> str:
            """Call the QuotationAgent to generate a quotation for a specific item.
            Args:
                item_name (str): The name of the item to generate a quotation for.
                quantity (int): The quantity of the item to generate a quotation for.

            Returns:
                str: The response from the QuotationAgent.
            """
            return self.quotation_agent.run(
                f"Generate quotation for this item: {item_name}, quantity: {quantity} - Always follow your instructions."
            )

        @tool
        def call_transaction_agent(item_name: str, quantity: int, resupply: int) -> str:
            """Call the TransactionAgent to create a transaction for a specific item.
            Args:
                item_name (str): The name of the item for the transaction.
                quantity (int): The quantity of the item for the transaction.
                resupply (int): The resupply quantity for the transaction.

            Returns:
                str: The response from the TransactionAgent.
            """
            return self.transaction_agent.run(
                f"Create transactions for this order: {item_name}, quantity: {quantity}, resupply: {resupply} - Always follow your instructions."
            )

        super().__init__(
            tools=[call_inventory_agent, call_quotation_agent, call_transaction_agent],
            model=model,
            name="OrchestrationAgent",
            max_tool_threads=1,
            description="""
            You are the orchestration agent for a paper supply company, coordinating the actions of specialized sub-agents to fulfill orders efficiently.

            You report to an agent responsible for overseeing the overall order fulfillment process. He will provide you with individual customer orders and expect you to coordinate the sub-agents to fulfill them efficiently.
            """,
            instructions="""

            You have the following agents at your disposal:
            - InventoryAgent: Responsible for managing inventory-related tasks.
            - QuotationAgent: Responsible for generating quotations for requested items.
            - TransactionAgent: Responsible for creating transactions based on inventory and quotation results.

            Process orders by:
            1. Check the inventory for the item using call_inventory_agent. 
            2. Get a quotation for the item using call_quotation_agent. Send the item details exactly as received from the inventory check.
            3. Forward both the inventory check result and the quotation to the call_transaction_agent. Send the information exactly as received from the previous steps.
            4. Summarize the inventory, quotation and transaction results and inform the customer of the outcome.

            Reply with the final summary.
            """,
        )

    def process_order(self, request):
        decomposed_order = self.order_decomposition_agent.run(
            f"Decompose this order: {request} - Always follow your instructions. Only respond with JSON."
        )
        print(decomposed_order)
        order_list = json.loads(decomposed_order) if isinstance(decomposed_order, str) else decomposed_order

        results = []
        for item in order_list:
            if item['offered']:
                result = self.run(
                    f"Customer order: {item}. Always follow your instructions."
                )
                results.append(result)

        
        content = f"""
        The customer had the following request: {request}

        Your orchestration agent has processed the individual items as follows: {results}
        Please provide a very short final feedback for the customer based on these results. Provide the sum of all orders as total price.
        If any item was not offered, mention it explicitly in the final feedback.
        """

        response = model.generate([{"role": "user", "content": content}])

        return response.content


def run():
    print("Initializing Database...")
    init_database()
    try:
        quote_requests_sample = pd.read_csv("quote_requests_sample.csv")
        quote_requests_sample["request_date"] = pd.to_datetime(
            quote_requests_sample["request_date"], format="%m/%d/%y", errors="coerce"
        )
        quote_requests_sample.dropna(subset=["request_date"], inplace=True)
        quote_requests_sample = quote_requests_sample.sort_values("request_date")
    except Exception as error:
        print(f"FATAL: Error loading test data: {error}")
        return

    # Get initial state
    initial_date = quote_requests_sample["request_date"].min().strftime("%Y-%m-%d")
    report = generate_financial_report(initial_date)
    current_cash = report["cash_balance"]
    current_inventory = report["inventory_value"]

    orchestration_agent = OrchestrationAgent(model)

    results = []

    sample = pd.DataFrame(
        [
            {
                "job": "teacher",
                "need_size": "small",
                "event": "reception",
                "request": "I need 400 papers in A4 and 50 photo paper by April 10, 2025",
                "request_date": "01.04.2025",
            }
        ]
    )
    sample["request_date"] = pd.to_datetime(sample["request_date"], format="%d.%m.%Y")

    for idx, row in quote_requests_sample.iterrows():  # sample
        request_date = row["request_date"].strftime("%Y-%m-%d")
        DAY = request_date

        print(f"\n=== Request {idx+1} ===")
        print(f"Context: {row['job']} organizing {row['event']}")
        print(f"Request Date: {request_date}")
        print(f"Cash Balance: ${current_cash:.2f}")
        print(f"Inventory Value: ${current_inventory:.2f}")

        # Process request
        request_with_date = f"{row['request']} (Date of request: {request_date})"

        response = orchestration_agent.process_order(request_with_date)
        # Update state
        report = generate_financial_report(request_date)
        current_cash = report["cash_balance"]
        current_inventory = report["inventory_value"]

        print(f"Response: {response}")
        print(f"Updated Cash: ${current_cash:.2f}")
        print(f"Updated Inventory: ${current_inventory:.2f}")

        results.append(
            {
                "request_id": idx + 1,
                "request_date": request_date,
                "cash_balance": current_cash,
                "inventory_value": current_inventory,
                "response": response,
            }
        )

        time.sleep(1)

    # Final report
    final_date = quote_requests_sample["request_date"].max().strftime("%Y-%m-%d")
    final_report = generate_financial_report(final_date)
    print("\n===== FINAL FINANCIAL REPORT =====")
    print(f"Final Cash: ${final_report['cash_balance']:.2f}")
    print(f"Final Inventory: ${final_report['inventory_value']:.2f}")

    # Save results
    pd.DataFrame(results).to_csv("test_results.csv", index=False)
    return results


if __name__ == "__main__":
    run()
