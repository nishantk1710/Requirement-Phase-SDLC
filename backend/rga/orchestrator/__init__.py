"""P8 — end-to-end orchestration.

The RGA pipeline (ingest → extract → analyze → rules → human review → generate → metrics
→ baseline) as a LangGraph `StateGraph`. Control flow is deterministic (no LLM routes it);
the graph pauses at the human-review node via `interrupt_before`, and a persistent
checkpointer makes the run resumable after a kill. Domain state (requirements, decisions)
lives in the SQLite store — the graph checkpoints flow position + lightweight run state.
"""

from .graph import STAGES, RGAState, build_graph, run_stage_order

__all__ = ["STAGES", "RGAState", "build_graph", "run_stage_order"]
