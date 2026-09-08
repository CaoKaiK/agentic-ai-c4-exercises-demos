import os
from dotenv import find_dotenv, load_dotenv
import pandas as pd

import mlflow
from mlflow.entities import SpanType
from database.init_db import init_database

load_dotenv(find_dotenv(), override=True)
openai_api_key = os.getenv("OPENAI_API_KEY")
tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")

mlflow.set_tracking_uri(tracking_uri)
mlflow.set_experiment("munder-difflin")

# Enable the MLflow integration before importing and using smolagents.
mlflow.smolagents.autolog()
from smolagents import OpenAIServerModel, ToolCallingAgent, WebSearchTool

# Load environment configuration before resolving the tracking URI.
load_dotenv(find_dotenv(), override=True)
openai_api_key = os.getenv("OPENAI_API_KEY")

# Model
model = OpenAIServerModel(
    model_id="gpt-4.1-mini",
    api_base="https://openai.vocareum.com/v1",
    api_key=openai_api_key,
)

class InventoryAgent(ToolCallingAgent):
    """Agent responsible for managing inventory-related tasks."""

    def __init__(self, model):
        super().__init__(
            tools=[WebSearchTool()],
            model=model,
            name="InventoryAgent",
            description="Agent responsible for managing inventory-related tasks. You can search the web"
        )

class OrchestrationAgent(ToolCallingAgent):
    """Orchestration agent that manages multiple sub-agents and coordinates their actions."""

    def __init__(self, model):
        self.model = model

        self.inventory_agent = InventoryAgent(model)

        super().__init__(
            tools=[],
            model=model,
            name="OrchestrationAgent",
            description="""
            You are the orchestration agent for a paper supply company, 
            coordinating the actions of specialized sub-agents to fulfill orders efficiently.

            You have access to an inventory agent that can help you.

            """,
        )

    @mlflow.trace(name="process_order", span_type=SpanType.AGENT)
    def process_order(self, request):
        """Process an incoming order by delegating tasks to the appropriate sub-agents."""


        result = self.run(request)
        return result


def run():
    orchestration_agent = OrchestrationAgent(model)

    orchestration_agent.process_order("I need some paper")




if __name__ == "__main__":
    run()