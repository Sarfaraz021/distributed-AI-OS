from __future__ import annotations

import json
import logging
import os
import sqlite3
from functools import lru_cache
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from core.config import get_settings
from core.idempotency import make_idem_key
from core.llm import call_llm
from core.tools import send_email_tool

logger = logging.getLogger(__name__)

EMAIL_STEP_INDEX = 2


class AgentState(TypedDict, total=False):
    run_id: str
    task: str
    to: str
    draft: str
    email_result: dict[str, Any]
    summary: str
    status: Literal["running", "completed"]


def _checkpointer() -> SqliteSaver:
    settings = get_settings()
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.checkpoint_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def plan_email(state: AgentState) -> dict[str, Any]:
    run_id = state["run_id"]
    task = state["task"]
    to = state["to"]
    idem_key = make_idem_key(run_id, 1, "call_llm", {"node": "plan_email", "task": task})
    result = call_llm(
        [
            SystemMessage(content="Draft a short transactional email body. No greeting fluff."),
            HumanMessage(content=f"Recipient: {to}\nTask: {task}"),
        ],
        idem_key=idem_key,
        deadline_s=get_settings().default_deadline_s,
    )
    return {"draft": result["content"], "status": "running"}


def send_email_node(state: AgentState) -> dict[str, Any]:
    run_id = state["run_id"]
    body = state.get("draft") or state["task"]
    tool = send_email_tool(run_id, EMAIL_STEP_INDEX)
    raw = tool.invoke({"to": state["to"], "body": body})
    result = json.loads(raw) if isinstance(raw, str) else raw
    if get_settings().crash_after_email:
        logger.warning("crash_after_email run_id=%s exiting before checkpoint", run_id)
        os._exit(9)
    return {"email_result": result}


def summarize(state: AgentState) -> dict[str, Any]:
    run_id = state["run_id"]
    idem_key = make_idem_key(run_id, 3, "call_llm", {"node": "summarize"})
    result = call_llm(
        [
            SystemMessage(content="Confirm in one sentence that the email was sent."),
            HumanMessage(content=f"to={state['to']} draft={state.get('draft', '')[:200]}"),
        ],
        idem_key=idem_key,
        deadline_s=get_settings().default_deadline_s,
    )
    return {"summary": result["content"], "status": "completed"}


@lru_cache(maxsize=1)
def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("plan_email", plan_email)
    builder.add_node("send_email", send_email_node)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "plan_email")
    builder.add_edge("plan_email", "send_email")
    builder.add_edge("send_email", "summarize")
    builder.add_edge("summarize", END)
    return builder.compile(checkpointer=_checkpointer())


def thread_config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}}


def start_run(run_id: str, *, task: str, to: str) -> AgentState:
    graph = build_graph()
    return graph.invoke(
        {"run_id": run_id, "task": task, "to": to, "status": "running"},
        thread_config(run_id),
    )


def resume_run(run_id: str) -> AgentState:
    graph = build_graph()
    return graph.invoke(None, thread_config(run_id))
