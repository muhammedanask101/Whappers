from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IntentSignal:
    name: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    entities: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolObservation:
    tool: str
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    state_patch: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    internal: bool = False


@dataclass
class PlannerDecision:
    action: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    rationale: str = ""
    confidence: float = 0.0
    plan_steps: List[Dict[str, Any]] = field(default_factory=list)
    goal: Dict[str, Any] = field(default_factory=dict)
    uncertainty: float = 0.0


@dataclass
class MemoryBundle:
    state: Dict[str, Any]
    history: List[Dict[str, str]]
    summary: Dict[str, Any]
    profile: Dict[str, Any]
    learning: Dict[str, Any]
    recent_events: List[Dict[str, Any]]
    business: Dict[str, Any]
    style: str


@dataclass
class AgentRunResult:
    reply: str
    intent: str
    tool_trace: List[ToolObservation] = field(default_factory=list)
    decision_trace: List[PlannerDecision] = field(default_factory=list)
    updated_state: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    reflections: List[Dict[str, Any]] = field(default_factory=list)
