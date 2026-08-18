"""
Pre-built workflow templates (Milestone 5).

Standard scanning workflow patterns that encode security assessment
best practices. Each template is a validated DAG of plugin steps with
dependency edges, conditional gates, and variable substitution.

These templates are the canonical starting points for both manual
workflow creation and AI-generated workflows.
"""

from __future__ import annotations

from app.domain.workflow_templates import (
    ConditionOperator,
    StepCondition,
    WorkflowTemplate,
    WorkflowTemplateStep,
)


def create_full_port_scan_template() -> WorkflowTemplate:
    """
    Full port scanning workflow: ping → nmap quick → nmap full → vuln scan.

    Flow:
    1. Ping to check host reachability
    2. Quick nmap scan (top 1000 ports)
    3. Full port scan if quick scan found open ports
    4. Vulnerability scan on discovered services
    """
    return WorkflowTemplate(
        id="full_port_scan",
        name="Full Port Scan",
        description=(
            "Comprehensive port scanning workflow: host discovery, "
            "quick scan, full scan, and vulnerability assessment."
        ),
        version="1.0.0",
        tags=frozenset({"port-scan", "comprehensive", "reconnaissance"}),
        category="reconnaissance",
        target_types=frozenset({"ip", "cidr", "domain"}),
        variables={
            "target": "",
            "quick_ports": "1-1000",
            "full_ports": "1-65535",
            "timing": "-T4",
        },
        steps=[
            WorkflowTemplateStep(
                id="ping_check",
                plugin="ping",
                name="Host Reachability Check",
                description="Verify target is reachable via ICMP",
                depends_on=[],
                config_overrides={"hostname": "{{target}}"},
                timeout_seconds=30,
                order=0,
            ),
            WorkflowTemplateStep(
                id="nmap_quick",
                plugin="nmap",
                name="Quick Port Scan",
                description="Scan top 1000 ports for initial discovery",
                depends_on=["ping_check"],
                config_overrides={
                    "target": "{{target}}",
                    "ports": "{{quick_ports}}",
                    "arguments": ["-sV", "-sC", "{{timing}}"],
                },
                timeout_seconds=120,
                order=1,
            ),
            WorkflowTemplateStep(
                id="nmap_full",
                plugin="nmap",
                name="Full Port Scan",
                description="Scan all 65535 ports if quick scan found open ports",
                depends_on=["nmap_quick"],
                condition=StepCondition(
                    reference_step="nmap_quick",
                    field="open_port_count",
                    operator=ConditionOperator.GREATER_THAN,
                    value=0,
                ),
                config_overrides={
                    "target": "{{target}}",
                    "ports": "{{full_ports}}",
                    "arguments": ["-sV", "-sC", "{{timing}}"],
                },
                timeout_seconds=600,
                order=2,
            ),
            WorkflowTemplateStep(
                id="vuln_scan",
                plugin="nmap",
                name="Vulnerability Scan",
                description="Run vulnerability scripts on discovered services",
                depends_on=["nmap_quick"],
                condition=StepCondition(
                    reference_step="nmap_quick",
                    field="open_port_count",
                    operator=ConditionOperator.GREATER_THAN,
                    value=0,
                ),
                config_overrides={
                    "target": "{{target}}",
                    "ports": "{{quick_ports}}",
                    "arguments": ["-sV", "-sC"],
                },
                timeout_seconds=300,
                order=3,
            ),
        ],
    )


def create_web_app_scan_template() -> WorkflowTemplate:
    """
    Web application scanning workflow: httpx → nikto → nuclei.

    Flow:
    1. httpx for technology fingerprinting
    2. nikto for web server misconfigurations
    3. nuclei for known vulnerability detection
    """
    return WorkflowTemplate(
        id="web_app_scan",
        name="Web Application Scan",
        description=(
            "Web application security assessment: technology "
            "detection, server misconfiguration, and vulnerability scanning."
        ),
        version="1.0.0",
        tags=frozenset({"web", "application", "vulnerability"}),
        category="vulnerability",
        target_types=frozenset({"domain", "url"}),
        variables={
            "target": "",
            "paths": "/",
        },
        steps=[
            WorkflowTemplateStep(
                id="tech_detect",
                plugin="httpx",
                name="Technology Detection",
                description="Identify web technologies and frameworks",
                depends_on=[],
                config_overrides={
                    "target": "{{target}}",
                    "paths": "{{paths}}",
                },
                timeout_seconds=60,
                order=0,
            ),
            WorkflowTemplateStep(
                id="webserver_scan",
                plugin="nikto",
                name="Web Server Scan",
                description="Scan for web server misconfigurations",
                depends_on=["tech_detect"],
                config_overrides={
                    "target": "{{target}}",
                },
                timeout_seconds=180,
                order=1,
            ),
            WorkflowTemplateStep(
                id="vuln_detect",
                plugin="nuclei",
                name="Vulnerability Detection",
                description="Scan for known vulnerabilities using templates",
                depends_on=["tech_detect"],
                config_overrides={
                    "target": "{{target}}",
                    "templates": "cves,vulnerabilities,misconfigurations",
                },
                timeout_seconds=300,
                order=2,
            ),
        ],
    )


def create_subdomain_takeover_template() -> WorkflowTemplate:
    """
    Subdomain takeover workflow: subfinder → httpx → nuclei.

    Flow:
    1. subfinder for subdomain enumeration
    2. httpx for live host verification
    3. nuclei for takeover vulnerability detection
    """
    return WorkflowTemplate(
        id="subdomain_takeover",
        name="Subdomain Takeover Scan",
        description=(
            "Discover subdomains and check for takeover "
            "vulnerabilities via CNAME/DNS misconfigurations."
        ),
        version="1.0.0",
        tags=frozenset({"subdomain", "takeover", "dns"}),
        category="reconnaissance",
        target_types=frozenset({"domain"}),
        variables={
            "target": "",
        },
        steps=[
            WorkflowTemplateStep(
                id="subdomain_enum",
                plugin="subfinder",
                name="Subdomain Enumeration",
                description="Discover subdomains via passive sources",
                depends_on=[],
                config_overrides={
                    "target": "{{target}}",
                },
                timeout_seconds=120,
                order=0,
            ),
            WorkflowTemplateStep(
                id="live_check",
                plugin="httpx",
                name="Live Host Verification",
                description="Verify which subdomains are live",
                depends_on=["subdomain_enum"],
                config_overrides={
                    "target": "{{target}}",
                    "input_from": "subdomain_enum",
                },
                timeout_seconds=120,
                order=1,
            ),
            WorkflowTemplateStep(
                id="takeover_check",
                plugin="nuclei",
                name="Takeover Detection",
                description="Scan for subdomain takeover vulnerabilities",
                depends_on=["live_check"],
                config_overrides={
                    "target": "{{target}}",
                    "templates": "subdomain-takeover",
                },
                timeout_seconds=180,
                order=2,
            ),
        ],
    )


def create_credential_exposure_template() -> WorkflowTemplate:
    """
    Credential exposure workflow: trufflehog + gitleaks (parallel) → report.

    Flow:
    1. trufflehog and gitleaks run in parallel on code repositories
    2. Combined results for credential exposure report
    """
    return WorkflowTemplate(
        id="credential_exposure",
        name="Credential Exposure Scan",
        description=(
            "Scan code repositories for exposed secrets, "
            "API keys, and credentials using multiple tools."
        ),
        version="1.0.0",
        tags=frozenset({"secrets", "credentials", "code-scan"}),
        category="vulnerability",
        target_types=frozenset({"url"}),
        variables={
            "repo_url": "",
            "branch": "main",
        },
        steps=[
            WorkflowTemplateStep(
                id="trufflehog_scan",
                plugin="trufflehog",
                name="TruffleHog Secret Scan",
                description="Scan repository with TruffleHog",
                depends_on=[],
                config_overrides={
                    "repo_url": "{{repo_url}}",
                    "branch": "{{branch}}",
                },
                timeout_seconds=300,
                order=0,
            ),
            WorkflowTemplateStep(
                id="gitleaks_scan",
                plugin="gitleaks",
                name="Gitleaks Secret Scan",
                description="Scan repository with Gitleaks",
                depends_on=[],
                config_overrides={
                    "repo_url": "{{repo_url}}",
                    "branch": "{{branch}}",
                },
                timeout_seconds=300,
                order=0,
            ),
            WorkflowTemplateStep(
                id="combined_report",
                plugin="echo",
                name="Combined Secret Report",
                description="Merge findings from both scanners",
                depends_on=["trufflehog_scan", "gitleaks_scan"],
                config_overrides={},
                timeout_seconds=30,
                order=1,
            ),
        ],
    )


def create_full_assessment_template() -> WorkflowTemplate:
    """
    Full security assessment: reconnaissance → port scan → service enum → vuln scan.

    Flow:
    1. Subdomain enumeration
    2. Host discovery and port scanning
    3. Service enumeration
    4. Vulnerability scanning
    """
    return WorkflowTemplate(
        id="full_assessment",
        name="Full Security Assessment",
        description=(
            "End-to-end security assessment combining reconnaissance, "
            "port scanning, service enumeration, and vulnerability detection."
        ),
        version="1.0.0",
        tags=frozenset({"full", "assessment", "comprehensive"}),
        category="reconnaissance",
        target_types=frozenset({"domain", "ip", "cidr"}),
        variables={
            "target": "",
            "quick_ports": "1-1000",
            "full_ports": "1-65535",
        },
        steps=[
            WorkflowTemplateStep(
                id="subdomain_enum",
                plugin="subfinder",
                name="Subdomain Enumeration",
                description="Discover subdomains",
                depends_on=[],
                config_overrides={"target": "{{target}}"},
                timeout_seconds=120,
                order=0,
            ),
            WorkflowTemplateStep(
                id="host_discovery",
                plugin="ping",
                name="Host Discovery",
                description="Verify host reachability",
                depends_on=[],
                config_overrides={"hostname": "{{target}}"},
                timeout_seconds=30,
                order=0,
            ),
            WorkflowTemplateStep(
                id="port_scan",
                plugin="nmap",
                name="Port Scanning",
                description="Scan for open ports",
                depends_on=["host_discovery"],
                config_overrides={
                    "target": "{{target}}",
                    "ports": "{{quick_ports}}",
                    "arguments": ["-sV", "-sC", "-T4"],
                },
                timeout_seconds=180,
                order=1,
            ),
            WorkflowTemplateStep(
                id="service_enum",
                plugin="nmap",
                name="Service Enumeration",
                description="Detailed service version detection",
                depends_on=["port_scan"],
                condition=StepCondition(
                    reference_step="port_scan",
                    field="open_port_count",
                    operator=ConditionOperator.GREATER_THAN,
                    value=0,
                ),
                config_overrides={
                    "target": "{{target}}",
                    "ports": "{{quick_ports}}",
                    "arguments": ["-sV", "-sC", "-A"],
                },
                timeout_seconds=300,
                order=2,
            ),
            WorkflowTemplateStep(
                id="vuln_scan",
                plugin="nuclei",
                name="Vulnerability Scan",
                description="Scan for known vulnerabilities",
                depends_on=["service_enum"],
                condition=StepCondition(
                    reference_step="port_scan",
                    field="open_port_count",
                    operator=ConditionOperator.GREATER_THAN,
                    value=0,
                ),
                config_overrides={
                    "target": "{{target}}",
                    "templates": "cves,vulnerabilities",
                },
                timeout_seconds=600,
                order=3,
            ),
        ],
    )


# Registry of all built-in templates
BUILTIN_TEMPLATES: dict[str, WorkflowTemplate] = {
    t.id: t for t in [
        create_full_port_scan_template(),
        create_web_app_scan_template(),
        create_subdomain_takeover_template(),
        create_credential_exposure_template(),
        create_full_assessment_template(),
    ]
}


def get_builtin_template(template_id: str) -> WorkflowTemplate | None:
    """Return a built-in template by ID, or None if not found."""
    return BUILTIN_TEMPLATES.get(template_id)


def list_builtin_templates() -> list[WorkflowTemplate]:
    """Return all built-in templates."""
    return list(BUILTIN_TEMPLATES.values())
