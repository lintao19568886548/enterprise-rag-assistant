from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.core.checkpointing import checkpoint_config, get_checkpoint_saver


class CounterState(TypedDict):
    value: int


def test_checkpointer_persists_graph_state_by_thread():
    builder = StateGraph(CounterState)
    builder.add_node("increment", lambda state: {"value": state["value"] + 1})
    builder.set_entry_point("increment")
    builder.add_edge("increment", END)
    saver = get_checkpoint_saver("unit-test")
    graph = builder.compile(checkpointer=saver)
    config = checkpoint_config("thread-1", "unit-test")

    result = graph.invoke({"value": 1}, config=config)

    assert result["value"] == 2
    assert saver.get_tuple(config) is not None
