"""
Workflow template system (Milestone 5).

Reusable, composable scanning workflow patterns that encode best
practices for common security assessment scenarios. Templates define
a DAG of plugin steps with dependency edges, conditional gates, and
variable substitution placeholders.

Templates are the bridge between AI planning (which recommends
workflows) and execution (which runs them). They're stored in-memory
as domain objects — no framework imports — and persisted via the
repository layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TemplateStepStatus(StrEnum):
    """Lifecycle state for a workflow template step during execution."""

    PENDING = "pending"
    WAITING = "waiting"  # waiting for dependencies
    READY = "ready"  # all dependencies satisfied
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # condition evaluated to false
    CANCELLED = "cancelled"


class ConditionOperator(StrEnum):
    """Operators for conditional step execution."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_THAN"
    CONTAINS = "contains"
    AND = "and"
    OR = "or"


@dataclass(frozen=True, slots=True)
class StepCondition:
    """
    A condition gate that must evaluate to True before a step runs.

    Conditions reference outputs from previous steps via step_id references.
    For example: "only run if step 'nmap_scan' found open ports" would be:
        StepCondition(
            reference_step="nmap_scan",
            field="open_port_count",
            operator=ConditionOperator.GREATER_THAN,
            value=0,
        )
    """

    reference_step: str
    field: str
    operator: ConditionOperator
    value: Any = None

    def evaluate(self, step_outputs: dict[str, dict[str, Any]]) -> bool:
        """
        Evaluate this condition against accumulated step outputs.

        Returns True if the condition is satisfied.
        Returns True (fail-open) only if the reference step is completely
        missing from step_outputs. If the step exists but the field is
        absent, that's a real condition evaluation (field_value = None).
        """
        if self.reference_step not in step_outputs:
            return True  # fail-open: missing step doesn't block

        ref_output = step_outputs[self.reference_step]
        field_value = ref_output.get(self.field)

        match self.operator:
            case ConditionOperator.EQUALS:
                return field_value == self.value
            case ConditionOperator.NOT_EQUALS:
                return field_value != self.value
            case ConditionOperator.EXISTS:
                return field_value is not None
            case ConditionOperator.NOT_EXISTS:
                return field_value is None
            case ConditionOperator.GREATER_THAN:
                return (
                    isinstance(field_value, (int, float))
                    and isinstance(self.value, (int, float))
                    and field_value > self.value
                )
            case ConditionOperator.LESS_THAN:
                return (
                    isinstance(field_value, (int, float))
                    and isinstance(self.value, (int, float))
                    and field_value < self.value
                )
            case ConditionOperator.CONTAINS:
                return isinstance(field_value, str) and self.value in field_value
            case ConditionOperator.AND:
                if not isinstance(self.value, list):
                    return False
                return all(
                    StepCondition(
                        reference_step=self.reference_step,
                        field=self.field,
                        operator=op,
                    ).evaluate(step_outputs)
                    for op in self.value
                )
            case ConditionOperator.OR:
                if not isinstance(self.value, list):
                    return False
                return any(
                    StepCondition(
                        reference_step=self.reference_step,
                        field=self.field,
                        operator=op,
                    ).evaluate(step_outputs)
                    for op in self.value
                )
        return False


@dataclass(slots=True)
class WorkflowTemplateStep:
    """
    A single step in a workflow template.

    Each step declares:
    - plugin: the plugin to execute
    - depends_on: step_ids that must complete before this step
    - condition: optional gate that must evaluate to True
    - config_overrides: static config merged with runtime variables
    - timeout_seconds: per-step timeout
    - max_retries: retry count on failure
    """

    id: str  # unique within the template
    plugin: str
    name: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    condition: StepCondition | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 120
    max_retries: int = 0
    order: int = 0
    enabled: bool = True

    @property
    def has_condition(self) -> bool:
        return self.condition is not None


@dataclass(slots=True)
class WorkflowTemplate:
    """
    A reusable, composable scanning workflow pattern.

    Templates define a DAG of plugin steps with dependency edges and
    conditional gates. They're parameterized via `variables` — runtime
    values that are substituted into step configs.

    Examples:
    - "Full Port Scan": ping → nmap (quick) → nmap (full) → vuln scan
    - "Web App Scan": httpx → nikto → nuclei
    - "Subdomain Takeover": subfinder → httpx → nuclei
    """

    id: str  # unique template identifier
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "SPECTER Team"
    tags: frozenset[str] = field(default_factory=frozenset)
    category: str = ""
    target_types: frozenset[str] = field(default_factory=frozenset)
    steps: list[WorkflowTemplateStep] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_step(self, step_id: str) -> WorkflowTemplateStep | None:
        """Return a step by ID, or None if not found."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_enabled_steps(self) -> list[WorkflowTemplateStep]:
        """Return only enabled steps, sorted by order."""
        return sorted(
            [s for s in self.steps if s.enabled],
            key=lambda s: s.order,
        )

    def get_root_steps(self) -> list[WorkflowTemplateStep]:
        """Return steps with no dependencies (entry points)."""
        return [
            s for s in self.get_enabled_steps()
            if not s.depends_on
        ]

    def get_dependents(self, step_id: str) -> list[WorkflowTemplateStep]:
        """Return steps that depend on the given step_id."""
        return [
            s for s in self.get_enabled_steps()
            if step_id in s.depends_on
        ]

    def validate_dag(self) -> list[str]:
        """
        Validate the template's dependency graph.

        Returns list of error messages (empty = valid).
        Checks:
        - All depends_on references exist
        - No cycles
        - All step IDs are unique
        """
        errors: list[str] = []
        step_ids = {s.id for s in self.steps}

        # Check for duplicate step IDs
        seen_ids: set[str] = set()
        for step in self.steps:
            if step.id in seen_ids:
                errors.append(f"Duplicate step ID: {step.id}")
            seen_ids.add(step.id)

        # Check all dependencies exist
        for step in self.steps:
            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    errors.append(
                        f"Step '{step.id}' depends on unknown step '{dep_id}'"
                    )

        # Cycle detection via DFS
        if not errors:
            visited: set[str] = set()
            rec_stack: set[str] = set()

            def _has_cycle(node_id: str) -> bool:
                visited.add(node_id)
                rec_stack.add(node_id)
                step = self.get_step(node_id)
                if step:
                    for dep_id in step.depends_on:
                        if dep_id in step_ids:
                            if dep_id not in visited:
                                if _has_cycle(dep_id):
                                    return True
                            elif dep_id in rec_stack:
                                errors.append(
                                    f"Cycle detected involving step '{dep_id}'"
                                )
                                return True
                rec_stack.discard(node_id)
                return False

            for step in self.steps:
                if step.id not in visited and _has_cycle(step.id):
                    break

        return errors

    def get_execution_order(self) -> list[list[str]]:
        """
        Return topologically sorted execution layers.

        Each inner list contains step IDs that can run in parallel.
        Steps in layer N must complete before layer N+1 starts.
        Returns empty list if the DAG has cycles.
        """
        if self.validate_dag():
            return []

        step_map = {s.id: s for s in self.get_enabled_steps()}
        in_degree: dict[str, int] = {s.id: 0 for s in self.get_enabled_steps()}
        dependents_map: dict[str, list[str]] = {
            s.id: [] for s in self.get_enabled_steps()
        }

        for step in self.get_enabled_steps():
            for dep_id in step.depends_on:
                if dep_id in step_map:
                    in_degree[step.id] += 1
                    dependents_map[dep_id].append(step.id)

        layers: list[list[str]] = []
        ready = [sid for sid, deg in in_degree.items() if deg == 0]

        while ready:
            layers.append(sorted(ready))
            next_ready: list[str] = []
            for sid in ready:
                for dep_sid in dependents_map[sid]:
                    in_degree[dep_sid] -= 1
                    if in_degree[dep_sid] == 0:
                        next_ready.append(dep_sid)
            ready = next_ready

        return layers

    def substitute_variables(self, config: dict[str, Any]) -> dict[str, Any]:
        """
        Replace {{variable_name}} placeholders in config values
        with values from self.variables.
        """
        result = dict(config)
        for key, value in result.items():
            if isinstance(value, str) and "{{" in value:
                for var_name, var_value in self.variables.items():
                    placeholder = "{{" + var_name + "}}"
                    if placeholder in value:
                        result[key] = value.replace(placeholder, str(var_value))
        return result
