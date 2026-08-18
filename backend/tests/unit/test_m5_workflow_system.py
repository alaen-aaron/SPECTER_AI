"""
Unit tests for M5: Workflow Templates, Conditional Engine, and Workflow Engine.

Tests cover:
- Plugin capability declarations and metadata
- Plugin registry enhanced methods (category, tag, health, compatibility)
- Workflow template DAG validation and execution ordering
- Conditional step execution
- Workflow engine: sequential, parallel, conditional, retry, resume
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.domain.builtin_templates import (
    create_full_port_scan_template,
    create_web_app_scan_template,
    get_builtin_template,
    list_builtin_templates,
)
from app.domain.conditional_engine import ConditionalExecutionEngine
from app.domain.workflow_engine import (
    StepExecutionResult,
    StepExecutionStatus,
    WorkflowEngine,
)
from app.domain.workflow_templates import (
    ConditionOperator,
    StepCondition,
    WorkflowTemplate,
    WorkflowTemplateStep,
)
from app.plugins.base import Plugin, PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakePlugin(Plugin):
    """Deterministic plugin for testing."""

    def __init__(
        self,
        plugin_name: str = "fake",
        *,
        cap: PluginCapability | None = None,
        meta: PluginMetadata | None = None,
    ) -> None:
        self._name = plugin_name
        self._cap = cap or PluginCapability()
        self._meta = meta or PluginMetadata()

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return f"Fake {self._name} plugin"

    def validate_config(self, config: dict[str, Any]) -> None:
        pass

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        return PluginResult(
            success=True,
            stdout="output",
            stderr="",
            exit_code=0,
            metadata={"plugin": self._name},
        )

    def capability(self) -> PluginCapability:
        return self._cap

    def metadata(self) -> PluginMetadata:
        return self._meta


class _FakeExecutor:
    """Fake plugin executor for workflow engine tests."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any], int]] = []
        self.results: dict[str, tuple[bool, str, str, int | None, dict[str, Any]]] = {}

    def set_result(
        self,
        plugin_name: str,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = 0,
        normalized: dict[str, Any] | None = None,
    ) -> None:
        self.results[plugin_name] = (success, stdout, stderr, exit_code, normalized or {})

    def execute(
        self, plugin_name: str, config: dict[str, Any], timeout_seconds: int
    ) -> tuple[bool, str, str, int | None, dict[str, Any]]:
        self.executed.append((plugin_name, config, timeout_seconds))
        if plugin_name in self.results:
            return self.results[plugin_name]
        return (True, "ok", "", 0, {})


# ---------------------------------------------------------------------------
# Plugin capability and metadata tests
# ---------------------------------------------------------------------------


class TestPluginCapability:
    def test_default_capability(self) -> None:
        cap = PluginCapability()
        assert cap.input_asset_types == frozenset()
        assert cap.output_asset_types == frozenset()
        assert cap.produces_findings is False
        assert cap.requires_host is True

    def test_can_accept_any_when_no_inputs(self) -> None:
        cap = PluginCapability()
        assert cap.can_accept(frozenset({"host"})) is True

    def test_can_accept_matching_types(self) -> None:
        cap = PluginCapability(input_asset_types=frozenset({"host", "domain"}))
        assert cap.can_accept(frozenset({"host", "service"})) is True

    def test_cannot_accept_missing_types(self) -> None:
        cap = PluginCapability(input_asset_types=frozenset({"host"}))
        assert cap.can_accept(frozenset({"service"})) is False

    def test_compatible_with_matching_outputs(self) -> None:
        upstream = PluginCapability(output_asset_types=frozenset({"host", "port"}))
        downstream = PluginCapability(input_asset_types=frozenset({"port"}))
        assert downstream.is_compatible_with(upstream) is True

    def test_incompatible_with_missing_outputs(self) -> None:
        upstream = PluginCapability(output_asset_types=frozenset({"host"}))
        downstream = PluginCapability(input_asset_types=frozenset({"port"}))
        assert downstream.is_compatible_with(upstream) is False

    def test_compatible_when_no_inputs(self) -> None:
        upstream = PluginCapability(output_asset_types=frozenset({"host"}))
        downstream = PluginCapability()
        assert downstream.is_compatible_with(upstream) is True

    def test_compatible_when_no_upstream_outputs(self) -> None:
        upstream = PluginCapability()
        downstream = PluginCapability(input_asset_types=frozenset({"port"}))
        assert downstream.is_compatible_with(upstream) is True


class TestPluginMetadata:
    def test_default_metadata(self) -> None:
        meta = PluginMetadata()
        assert meta.version == "1.0.0"
        assert meta.category == PluginCategory.SCANNING
        assert meta.required_binaries == frozenset()

    def test_healthy_when_no_binaries(self) -> None:
        meta = PluginMetadata()
        assert meta.is_healthy() is True

    def test_check_binaries_empty(self) -> None:
        meta = PluginMetadata(required_binaries=frozenset({"nonexistent_binary_xyz"}))
        missing = meta.check_binaries()
        assert "nonexistent_binary_xyz" in missing

    def test_check_binaries_python(self) -> None:
        meta = PluginMetadata(required_binaries=frozenset({"python"}))
        missing = meta.check_binaries()
        assert "python" not in missing


class TestPluginCapabilityOnPlugins:
    def test_echo_capability(self) -> None:
        from app.plugins.echo_plugin import EchoPlugin
        plugin = EchoPlugin()
        cap = plugin.capability()
        assert cap.requires_host is False
        assert cap.produces_findings is False

    def test_ping_capability(self) -> None:
        from app.plugins.ping_plugin import PingPlugin
        plugin = PingPlugin()
        cap = plugin.capability()
        assert "host" in cap.input_asset_types or "domain" in cap.input_asset_types
        assert cap.produces_findings is False

    def test_nmap_capability(self) -> None:
        from app.plugins.nmap_plugin import NmapPlugin
        plugin = NmapPlugin()
        cap = plugin.capability()
        assert cap.produces_findings is True
        assert "service" in cap.output_asset_types

    def test_echo_metadata(self) -> None:
        from app.plugins.echo_plugin import EchoPlugin
        plugin = EchoPlugin()
        meta = plugin.metadata()
        assert meta.category == PluginCategory.UTILITY
        assert "demo" in meta.tags

    def test_nmap_metadata(self) -> None:
        from app.plugins.nmap_plugin import NmapPlugin
        plugin = NmapPlugin()
        meta = plugin.metadata()
        assert meta.category == PluginCategory.SCANNING
        assert "ports" in meta.tags


# ---------------------------------------------------------------------------
# Enhanced PluginRegistry tests
# ---------------------------------------------------------------------------


class TestEnhancedRegistry:
    @pytest.fixture
    def registry(self) -> PluginRegistry:
        return PluginRegistry()

    def test_get_metadata(self, registry: PluginRegistry) -> None:
        plugin = _FakePlugin(
            "test",
            meta=PluginMetadata(version="2.0.0", category=PluginCategory.VULNERABILITY),
        )
        registry.register(plugin)
        meta = registry.get_metadata("test")
        assert meta.version == "2.0.0"
        assert meta.category == PluginCategory.VULNERABILITY

    def test_get_capability(self, registry: PluginRegistry) -> None:
        plugin = _FakePlugin(
            "test",
            cap=PluginCapability(input_asset_types=frozenset({"host"})),
        )
        registry.register(plugin)
        cap = registry.get_capability("test")
        assert "host" in cap.input_asset_types

    def test_list_by_category(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin(
            "recon",
            meta=PluginMetadata(category=PluginCategory.RECONNAISSANCE),
        ))
        registry.register(_FakePlugin(
            "vuln",
            meta=PluginMetadata(category=PluginCategory.VULNERABILITY),
        ))
        registry.register(_FakePlugin(
            "scan",
            meta=PluginMetadata(category=PluginCategory.SCANNING),
        ))
        recon = registry.list_by_category(PluginCategory.RECONNAISSANCE)
        assert len(recon) == 1
        assert recon[0].name() == "recon"

    def test_list_by_tag(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin(
            "a",
            meta=PluginMetadata(tags=frozenset({"web", "fast"})),
        ))
        registry.register(_FakePlugin(
            "b",
            meta=PluginMetadata(tags=frozenset({"network"})),
        ))
        web_plugins = registry.list_by_tag("web")
        assert len(web_plugins) == 1
        assert web_plugins[0].name() == "a"

    def test_find_compatible(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin(
            "nmap",
            cap=PluginCapability(output_asset_types=frozenset({"host", "port", "service"})),
        ))
        registry.register(_FakePlugin(
            "nikto",
            cap=PluginCapability(input_asset_types=frozenset({"host", "service"})),
        ))
        registry.register(_FakePlugin(
            "subfinder",
            cap=PluginCapability(output_asset_types=frozenset({"subdomain"})),
        ))
        compatible = registry.find_compatible("nmap")
        names = {p.name() for p in compatible}
        assert "nikto" in names
        assert "subfinder" not in names  # subfinder doesn't consume host/port/service

    def test_check_health(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin("a"))
        registry.register(_FakePlugin("b"))
        health = registry.check_health()
        assert health["a"] is True
        assert health["b"] is True

    def test_validate_compatibility(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin(
            "producer",
            cap=PluginCapability(output_asset_types=frozenset({"port"})),
        ))
        registry.register(_FakePlugin(
            "consumer",
            cap=PluginCapability(input_asset_types=frozenset({"port"})),
        ))
        registry.register(_FakePlugin(
            "incompatible",
            cap=PluginCapability(input_asset_types=frozenset({"credential"})),
        ))
        ok, msg = registry.validate_compatibility("producer", "consumer")
        assert ok is True
        ok, msg = registry.validate_compatibility("producer", "incompatible")
        assert ok is False

    def test_get_by_names(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin("a"))
        registry.register(_FakePlugin("b"))
        plugins = registry.get_by_names(["a", "b"])
        assert len(plugins) == 2

    def test_get_healthy_plugins(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin("a"))
        registry.register(_FakePlugin("b"))
        healthy = registry.get_healthy_plugins()
        assert len(healthy) == 2

    def test_real_builtin_health(self) -> None:
        """Built-in plugins should report health based on binary availability."""
        from app.plugins.nmap_plugin import NmapPlugin
        from app.plugins.ping_plugin import PingPlugin
        ping = PingPlugin()
        nmap = NmapPlugin()
        # ping should always be healthy (it's on every OS)
        assert ping.health_check() is True
        # nmap may or may not be installed
        # just ensure it doesn't crash
        _ = nmap.health_check()


# ---------------------------------------------------------------------------
# Workflow Template tests
# ---------------------------------------------------------------------------


class TestWorkflowTemplate:
    def _make_template(self) -> WorkflowTemplate:
        return WorkflowTemplate(
            id="test",
            name="Test",
            description="Test template",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=["a"]),
                WorkflowTemplateStep(id="c", plugin="echo", name="C", depends_on=["b"]),
            ],
        )

    def test_validate_dag_valid(self) -> None:
        template = self._make_template()
        errors = template.validate_dag()
        assert errors == []

    def test_validate_dag_missing_dependency(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=["nonexistent"]),
            ],
        )
        errors = template.validate_dag()
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_validate_dag_cycle(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=["b"]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=["a"]),
            ],
        )
        errors = template.validate_dag()
        assert len(errors) >= 1
        assert any("cycle" in e.lower() for e in errors)

    def test_validate_dag_duplicate_ids(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A1"),
                WorkflowTemplateStep(id="a", plugin="nmap", name="A2"),
            ],
        )
        errors = template.validate_dag()
        assert len(errors) >= 1
        assert any("duplicate" in e.lower() for e in errors)

    def test_get_root_steps(self) -> None:
        template = self._make_template()
        roots = template.get_root_steps()
        assert len(roots) == 1
        assert roots[0].id == "a"

    def test_get_dependents(self) -> None:
        template = self._make_template()
        deps = template.get_dependents("a")
        assert len(deps) == 1
        assert deps[0].id == "b"

    def test_get_execution_order(self) -> None:
        template = self._make_template()
        layers = template.get_execution_order()
        assert len(layers) == 3
        assert layers[0] == ["a"]
        assert layers[1] == ["b"]
        assert layers[2] == ["c"]

    def test_get_execution_order_with_parallel(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=[]),
                WorkflowTemplateStep(id="c", plugin="echo", name="C", depends_on=["a", "b"]),
            ],
        )
        layers = template.get_execution_order()
        assert len(layers) == 2
        assert set(layers[0]) == {"a", "b"}
        assert layers[1] == ["c"]

    def test_substitute_variables(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            variables={"target": "192.168.1.1"},
            steps=[],
        )
        config = {"target": "{{target}}", "ports": "1-1000"}
        result = template.substitute_variables(config)
        assert result["target"] == "192.168.1.1"
        assert result["ports"] == "1-1000"

    def test_disabled_steps_excluded(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", enabled=True),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", enabled=False),
            ],
        )
        enabled = template.get_enabled_steps()
        assert len(enabled) == 1
        assert enabled[0].id == "a"


class TestBuiltinTemplates:
    def test_all_builtin_templates_valid(self) -> None:
        templates = list_builtin_templates()
        assert len(templates) >= 5
        for template in templates:
            errors = template.validate_dag()
            assert errors == [], f"Template '{template.id}' has DAG errors: {errors}"

    def test_get_builtin_template(self) -> None:
        t = get_builtin_template("full_port_scan")
        assert t is not None
        assert t.name == "Full Port Scan"

    def test_get_builtin_template_not_found(self) -> None:
        t = get_builtin_template("nonexistent")
        assert t is None

    def test_full_port_scan_has_conditional_steps(self) -> None:
        t = create_full_port_scan_template()
        conditional_steps = [s for s in t.steps if s.has_condition]
        assert len(conditional_steps) >= 1

    def test_web_app_scan_steps(self) -> None:
        t = create_web_app_scan_template()
        step_ids = {s.id for s in t.steps}
        assert "tech_detect" in step_ids
        assert "webserver_scan" in step_ids
        assert "vuln_detect" in step_ids


# ---------------------------------------------------------------------------
# Conditional Engine tests
# ---------------------------------------------------------------------------


class TestConditionalExecutionEngine:
    def setup_method(self) -> None:
        self.engine = ConditionalExecutionEngine()

    def test_no_condition_returns_true(self) -> None:
        step = WorkflowTemplateStep(id="a", plugin="ping", name="A")
        assert self.engine.evaluate_step(step, {}) is True

    def test_equals_condition(self) -> None:
        step = WorkflowTemplateStep(
            id="a",
            plugin="ping",
            name="A",
            condition=StepCondition(
                reference_step="prev",
                field="status",
                operator=ConditionOperator.EQUALS,
                value="ok",
            ),
        )
        outputs = {"prev": {"status": "ok"}}
        assert self.engine.evaluate_step(step, outputs) is True

        outputs = {"prev": {"status": "fail"}}
        assert self.engine.evaluate_step(step, outputs) is False

    def test_greater_than_condition(self) -> None:
        step = WorkflowTemplateStep(
            id="a",
            plugin="ping",
            name="A",
            condition=StepCondition(
                reference_step="prev",
                field="count",
                operator=ConditionOperator.GREATER_THAN,
                value=5,
            ),
        )
        assert self.engine.evaluate_step(step, {"prev": {"count": 10}}) is True
        assert self.engine.evaluate_step(step, {"prev": {"count": 3}}) is False

    def test_exists_condition(self) -> None:
        step = WorkflowTemplateStep(
            id="a",
            plugin="ping",
            name="A",
            condition=StepCondition(
                reference_step="prev",
                field="data",
                operator=ConditionOperator.EXISTS,
            ),
        )
        assert self.engine.evaluate_step(step, {"prev": {"data": "x"}}) is True
        assert self.engine.evaluate_step(step, {"prev": {}}) is False

    def test_not_exists_condition(self) -> None:
        step = WorkflowTemplateStep(
            id="a",
            plugin="ping",
            name="A",
            condition=StepCondition(
                reference_step="prev",
                field="data",
                operator=ConditionOperator.NOT_EXISTS,
            ),
        )
        assert self.engine.evaluate_step(step, {"prev": {}}) is True
        assert self.engine.evaluate_step(step, {"prev": {"data": "x"}}) is False

    def test_contains_condition(self) -> None:
        step = WorkflowTemplateStep(
            id="a",
            plugin="ping",
            name="A",
            condition=StepCondition(
                reference_step="prev",
                field="output",
                operator=ConditionOperator.CONTAINS,
                value="error",
            ),
        )
        assert self.engine.evaluate_step(step, {"prev": {"output": "has error here"}}) is True
        assert self.engine.evaluate_step(step, {"prev": {"output": "all good"}}) is False

    def test_fail_open_for_missing_reference(self) -> None:
        step = WorkflowTemplateStep(
            id="a",
            plugin="ping",
            name="A",
            condition=StepCondition(
                reference_step="nonexistent",
                field="data",
                operator=ConditionOperator.EQUALS,
                value="x",
            ),
        )
        assert self.engine.evaluate_step(step, {}) is True

    def test_get_ready_steps(self) -> None:
        steps = [
            WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
            WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=["a"]),
        ]
        ready = self.engine.get_ready_steps(steps, {}, set(), set())
        assert len(ready) == 1
        assert ready[0].id == "a"

    def test_get_ready_steps_with_condition(self) -> None:
        steps = [
            WorkflowTemplateStep(
                id="a",
                plugin="nmap",
                name="A",
                depends_on=[],
            ),
            WorkflowTemplateStep(
                id="b",
                plugin="vuln",
                name="B",
                depends_on=["a"],
                condition=StepCondition(
                    reference_step="a",
                    field="open_port_count",
                    operator=ConditionOperator.GREATER_THAN,
                    value=0,
                ),
            ),
        ]
        # No outputs yet — only a is ready
        ready = self.engine.get_ready_steps(steps, {}, set(), set())
        assert len(ready) == 1

        # a completed with open ports — b becomes ready
        ready = self.engine.get_ready_steps(steps, {"a": {"open_port_count": 5}}, {"a"}, set())
        assert len(ready) == 1
        assert ready[0].id == "b"

    def test_get_skipped_steps(self) -> None:
        steps = [
            WorkflowTemplateStep(
                id="a",
                plugin="nmap",
                name="A",
                depends_on=[],
            ),
            WorkflowTemplateStep(
                id="b",
                plugin="vuln",
                name="B",
                depends_on=["a"],
                condition=StepCondition(
                    reference_step="a",
                    field="open_port_count",
                    operator=ConditionOperator.GREATER_THAN,
                    value=0,
                ),
            ),
        ]
        # a completed with 0 open ports — b should be skipped
        skipped = self.engine.get_skipped_steps(steps, {"a": {"open_port_count": 0}}, {"a"}, set())
        assert len(skipped) == 1
        assert skipped[0].id == "b"


# ---------------------------------------------------------------------------
# Workflow Engine tests
# ---------------------------------------------------------------------------


class TestWorkflowEngine:
    def _make_engine(self) -> tuple[WorkflowEngine, _FakeExecutor]:
        executor = _FakeExecutor()
        engine = WorkflowEngine(executor)
        return engine, executor

    def _make_template(self) -> WorkflowTemplate:
        return WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=["a"]),
            ],
        )

    def test_create_execution_state(self) -> None:
        engine, _ = self._make_engine()
        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())
        assert state.workflow_id == "test"
        assert len(state.step_results) == 2
        assert all(
            r.status == StepExecutionStatus.PENDING
            for r in state.step_results.values()
        )

    def test_get_next_steps_initial(self) -> None:
        engine, _ = self._make_engine()
        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())
        next_steps = engine.get_next_steps(template, state)
        assert len(next_steps) == 1
        assert next_steps[0].id == "a"

    def test_execute_step_success(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("ping", True, "pong", "", 0, {"reachable": True})
        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())

        step = template.get_step("a")
        assert step is not None
        result = engine.execute_step(step, state)
        assert result.success is True
        assert result.normalized_payload == {"reachable": True}

    def test_execute_step_failure(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("ping", False, "", "timeout", None)
        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())

        step = template.get_step("a")
        assert step is not None
        result = engine.execute_step(step, state)
        assert result.success is False
        assert result.status == StepExecutionStatus.FAILED

    def test_execute_step_with_retry(self) -> None:
        engine, executor = self._make_engine()
        # First call fails, second succeeds
        call_count = 0

        def flaky_execute(
            plugin_name: str, config: dict[str, Any], timeout_seconds: int
        ) -> tuple[bool, str, str, int | None, dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (False, "", "error", 1, {})
            return (True, "ok", "", 0, {"result": "success"})

        executor.execute = flaky_execute  # type: ignore[assignment]

        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(
                    id="a", plugin="ping", name="A", max_retries=2
                ),
            ],
        )
        state = engine.create_execution_state(template, uuid4(), uuid4())
        step = template.get_step("a")
        assert step is not None
        result = engine.execute_step_with_retry(step, state)
        assert result.success is True
        assert result.attempt == 2

    def test_execute_workflow_sequential(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("ping", True, "ok", "", 0, {"reachable": True})
        executor.set_result("nmap", True, "ports", "", 0, {"open_port_count": 3})

        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())
        state = engine.execute_workflow(template, state)

        assert state.status == "completed"
        assert state.has_failures is False
        assert len(executor.executed) == 2

    def test_execute_workflow_with_failure_cancels_dependents(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("ping", False, "", "unreachable", None)
        executor.set_result("nmap", True, "ok", "", 0, {})

        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())
        state = engine.execute_workflow(template, state)

        assert state.status == "failed"
        b_result = state.step_results["b"]
        assert "b" in state.failed_steps or b_result.status == StepExecutionStatus.CANCELLED

    def test_execute_workflow_parallel_layers(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("ping", True, "ok", "", 0, {})
        executor.set_result("nmap", True, "ok", "", 0, {})

        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=[]),
            ],
        )
        state = engine.create_execution_state(template, uuid4(), uuid4())
        state = engine.execute_workflow(template, state)

        assert state.status == "completed"
        assert len(executor.executed) == 2

    def test_execute_workflow_conditional_skip(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("nmap", True, "ok", "", 0, {"open_port_count": 0})
        executor.set_result("vuln", True, "ok", "", 0, {})

        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(
                    id="a", plugin="nmap", name="A", depends_on=[]
                ),
                WorkflowTemplateStep(
                    id="b",
                    plugin="vuln",
                    name="B",
                    depends_on=["a"],
                    condition=StepCondition(
                        reference_step="a",
                        field="open_port_count",
                        operator=ConditionOperator.GREATER_THAN,
                        value=0,
                    ),
                ),
            ],
        )
        state = engine.create_execution_state(template, uuid4(), uuid4())
        state = engine.execute_workflow(template, state)

        assert state.status == "completed"
        assert "b" in state.skipped_steps
        assert len(executor.executed) == 1  # only nmap ran

    def test_resume_workflow(self) -> None:
        engine, executor = self._make_engine()
        executor.set_result("ping", True, "ok", "", 0, {})

        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=["a"]),
            ],
        )
        state = engine.create_execution_state(template, uuid4(), uuid4())

        # Execute only step a manually
        step_a = template.get_step("a")
        assert step_a is not None
        result = engine.execute_step(step_a, state)
        state.step_results["a"] = result
        state.step_outputs["a"] = result.normalized_payload

        # Resume — should execute step b
        executor.set_result("nmap", True, "ok", "", 0, {})
        state = engine.resume_workflow(template, state)

        assert state.status == "completed"
        # a was not re-executed, only b was
        assert len([e for e in executor.executed if e[0] == "nmap"]) == 1

    def test_workflow_execution_state_resumable_steps(self) -> None:
        template = WorkflowTemplate(
            id="test",
            name="Test",
            description="",
            steps=[
                WorkflowTemplateStep(id="a", plugin="ping", name="A", depends_on=[]),
                WorkflowTemplateStep(id="b", plugin="nmap", name="B", depends_on=["a"]),
                WorkflowTemplateStep(id="c", plugin="echo", name="C", depends_on=["b"]),
            ],
        )
        engine, _ = self._make_engine()
        state = engine.create_execution_state(template, uuid4(), uuid4())

        # Initially only a is resumable
        resumable = state.get_resumable_steps(template.get_enabled_steps())
        assert resumable == ["a"]

        # After completing a, b becomes resumable
        state.step_results["a"] = StepExecutionResult(
            step_id="a", status=StepExecutionStatus.COMPLETED, plugin="ping"
        )
        resumable = state.get_resumable_steps(template.get_enabled_steps())
        assert "b" in resumable

    def test_execute_workflow_exception_handling(self) -> None:
        engine, executor = self._make_engine()

        def raise_execute(
            plugin_name: str, config: dict[str, Any], timeout: int
        ) -> tuple[bool, str, str, int | None, dict[str, Any]]:
            raise RuntimeError("plugin crashed")

        executor.execute = raise_execute  # type: ignore[assignment]

        template = self._make_template()
        state = engine.create_execution_state(template, uuid4(), uuid4())
        state = engine.execute_workflow(template, state)

        assert state.status == "failed"
        assert state.has_failures

    def test_full_port_scan_template_executes(self) -> None:
        """Integration test: full port scan template should be executable."""
        engine, executor = self._make_engine()
        executor.set_result("ping", True, "ok", "", 0, {"reachable": True})
        executor.set_result("nmap", True, "ports", "", 0, {"open_port_count": 3})

        template = create_full_port_scan_template()
        template.variables["target"] = "192.168.1.1"
        state = engine.create_execution_state(template, uuid4(), uuid4())
        state = engine.execute_workflow(template, state)

        # Should have completed at least some steps
        assert len(state.completed_steps) >= 1
