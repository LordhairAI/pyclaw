from typing import Annotated, Sequence
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
