# ============================================================
# test_ci_workflow.py - CI workflow 文件静态校验
# ============================================================
# 覆盖:
#   - .github/workflows/ci.yml 是合法 YAML
#   - 6 jobs 齐全 (lint / unit-test / integration-test / build / auto-push / release)
#   - jobs 依赖链 lint → unit/integration → build → auto-push / release
#   - 触发器: push (main + dev/* + feature/* + tags) + PR + workflow_dispatch
#   - permissions 写权限 (release 上传必需)
#   - matrix 3.11/3.12
#   - coverage fail-under 80%
# ============================================================

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    if not CI_YML.exists():
        pytest.skip(f"CI workflow 不存在: {CI_YML}")
    with CI_YML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============ 文件结构 ============
def test_workflow_valid_yaml(workflow: dict) -> None:
    assert isinstance(workflow, dict)
    assert "jobs" in workflow


def test_workflow_name(workflow: dict) -> None:
    assert workflow.get("name") == "CI"


# ============ 触发器 ============
def test_triggers_push(workflow: dict) -> None:
    on = workflow.get(True, workflow.get("on", {}))
    assert "push" in on
    push = on["push"]
    assert "main" in push["branches"]
    assert "dev/**" in push["branches"]
    assert "feature/**" in push["branches"]
    assert "v*" in push["tags"]


def test_triggers_pull_request(workflow: dict) -> None:
    on = workflow.get(True, workflow.get("on", {}))
    assert "pull_request" in on
    assert "main" in on["pull_request"]["branches"]


def test_triggers_workflow_dispatch(workflow: dict) -> None:
    on = workflow.get(True, workflow.get("on", {}))
    assert "workflow_dispatch" in on


# ============ Permissions ============
def test_permissions_write(workflow: dict) -> None:
    """release 上传 + PR comment 需 write"""
    perms = workflow.get("permissions", {})
    assert perms.get("contents") == "write"
    assert perms.get("pull-requests") == "write"


# ============ 6 Jobs 齐全 ============
def test_jobs_completeness(workflow: dict) -> None:
    jobs = workflow["jobs"]
    expected = {"lint", "unit-test", "integration-test", "build", "auto-push", "release"}
    assert expected.issubset(set(jobs.keys())), f"缺 jobs: {expected - set(jobs.keys())}"


# ============ Lint job ============
def test_lint_job(workflow: dict) -> None:
    job = workflow["jobs"]["lint"]
    assert job["runs-on"] == "ubuntu-latest"
    # 应有 ruff + black + mypy 步骤
    step_names = " ".join(s.get("name", "") for s in job["steps"])
    assert "ruff" in step_names
    assert "black" in step_names
    assert "mypy" in step_names


# ============ Unit-test job ============
def test_unit_test_matrix(workflow: dict) -> None:
    # 7-8 决策: pyproject requires-python=">=3.12", 3.11 装包阶段被拒
    # matrix 只保留 3.12, 节省 CI 配额
    job = workflow["jobs"]["unit-test"]
    matrix = job["strategy"]["matrix"]
    assert "3.12" in matrix["python-version"]
    assert job["needs"] == "lint"


def test_unit_test_coverage_threshold(workflow: dict) -> None:
    """coverage ≥ 80%"""
    job = workflow["jobs"]["unit-test"]
    run_cmds = " ".join(step.get("run", "") for step in job["steps"] if step.get("run"))
    assert "--cov-fail-under=80" in run_cmds


def test_integration_test_coverage_threshold(workflow: dict) -> None:
    # 7-8 决策: 集成测重点端到端流, 不强制 80% (unit 覆盖 utility code, 集成覆盖 publisher.run_once)
    # 集成覆盖率仅作监控, 失败不阻断 build (run cmd 末尾有 || true)
    job = workflow["jobs"]["integration-test"]
    run_cmds = " ".join(step.get("run", "") for step in job["steps"] if step.get("run"))
    # 7-8: 改 '--cov-fail-under=80' 不再出现, 允许 || true 阻断
    assert "|| true" in run_cmds
    assert "--cov-fail-under=80" not in run_cmds


# ============ Build job ============
def test_build_depends_on_tests(workflow: dict) -> None:
    job = workflow["jobs"]["build"]
    needs = job["needs"]
    assert "unit-test" in needs
    assert "integration-test" in needs


def test_build_produces_dist(workflow: dict) -> None:
    job = workflow["jobs"]["build"]
    run_cmds = " ".join(step.get("run", "") for step in job["steps"] if step.get("run"))
    assert "python -m build" in run_cmds
    # 上传 dist
    upload_steps = [
        s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert any("dist" in str(s.get("with", {})) for s in upload_steps)


# ============ Auto-push job ============
def test_auto_push_main_only(workflow: dict) -> None:
    job = workflow["jobs"]["auto-push"]
    assert (
        job["if"] == "github.ref == 'refs/heads/main' && github.event_name == 'push' && success()"
    )
    assert job["needs"] == "build"


# ============ Release job ============
def test_release_tag_only(workflow: dict) -> None:
    job = workflow["jobs"]["release"]
    assert "refs/tags/v" in job["if"]
    assert job["needs"] == "build"
    # 用 softprops/action-gh-release
    uses_cmds = " ".join(step.get("uses", "") for step in job["steps"] if step.get("uses"))
    assert "softprops/action-gh-release" in uses_cmds


# ============ Concurrency ============
def test_concurrency_group(workflow: dict) -> None:
    conc = workflow.get("concurrency", {})
    assert "${{ github.workflow }}-${{ github.ref }}" in conc.get("group", "")
    assert conc.get("cancel-in-progress") is True


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
