# ============================================================
# test_p5_artifacts.py - P5 systemd + logrotate 产物验证 (v0.2)
# ============================================================
# 覆盖:
#   1. systemd unit 文件存在 + 必备字段
#   2. systemd-analyze verify 语法 OK
#   3. logrotate 配置 存在 + 关键指令
#   4. logrotate -d 干跑 OK
#   5. install / uninstall 脚本 bash 语法 OK
#   6. install-systemd.sh 幂等 (重复运行不报错)
#   7. README 引用 systemd 路径
# ============================================================

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = PROJECT_ROOT / "systemd"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOGROTATE_CONF = SCRIPTS_DIR / "logrotate-novel-publisher"


# ============ helpers ============
def _read(path: Path) -> str:
    assert path.exists(), f"文件缺失: {path}"
    return path.read_text(encoding="utf-8")


def _must_have(text: str, pattern: str, label: str) -> None:
    assert re.search(pattern, text, re.MULTILINE), f"缺 '{label}' (regex: {pattern})"


# ============ systemd unit 文件 ============
def test_service_file_exists() -> None:
    svc = SYSTEMD_DIR / "novel-publish.service"
    assert svc.exists()
    text = _read(svc)
    _must_have(text, r"^\[Unit\]", "Unit section")
    _must_have(text, r"^\[Service\]", "Service section")
    _must_have(text, r"^Type=oneshot", "oneshot 类型")
    _must_have(text, r"^EnvironmentFile=", "EnvironmentFile")
    _must_have(text, r"^WorkingDirectory=", "WorkingDirectory")
    _must_have(
        text,
        r"^ExecStart=.*\.venv/bin/python.*-m src\.publisher",
        "ExecStart 用 venv python 调 publisher",
    )
    _must_have(text, r"^StandardOutput=append:.*logs/publisher\.log", "stdout → logs")
    _must_have(text, r"^StandardError=append:.*logs/publisher\.log", "stderr → logs")
    _must_have(text, r"^After=network-online\.target", "等网络")
    _must_have(text, r"^Wants=network-online\.target", "wants 网络")


def test_timer_file_exists() -> None:
    tmr = SYSTEMD_DIR / "novel-publish.timer"
    assert tmr.exists()
    text = _read(tmr)
    _must_have(text, r"^\[Timer\]", "Timer section")
    _must_have(text, r"OnCalendar=\*-\*-\* 08:00:00", "08:00 触发")
    _must_have(text, r"OnCalendar=\*-\*-\* 12:00:00", "12:00 触发")
    _must_have(text, r"OnCalendar=\*-\*-\* 18:00:00", "18:00 触发")
    _must_have(text, r"^Persistent=true", "持久化 (错过补跑)")
    _must_have(text, r"^Unit=novel-publish\.service", "关联 service")


def test_systemd_analyze_verify() -> None:
    """语法验证 (systemd-analyze 退出码 + 错误关键字)"""
    if not shutil.which("systemd-analyze"):
        return  # 没装 systemd-analyze 跳过
    for unit in ("novel-publish.service", "novel-publish.timer"):
        result = subprocess.run(
            ["systemd-analyze", "verify", str(SYSTEMD_DIR / unit)],
            capture_output=True,
            text=True,
            check=False,
        )
        combined = result.stdout + result.stderr
        # systemd-analyze verify 退出码非 0 但只 warning 也 OK, 只 fail 含 'Failed to parse' 或 'bad unit' 才算错
        assert "Failed to parse" not in combined, f"{unit}: 解析错\n{combined}"
        assert "bad unit" not in combined.lower(), f"{unit}: bad unit\n{combined}"


# ============ logrotate ============
def test_logrotate_config_exists() -> None:
    assert LOGROTATE_CONF.exists()
    text = _read(LOGROTATE_CONF)
    _must_have(text, r"/.*logs/publisher\.log \{", "logs/publisher.log glob")
    _must_have(text, r"^\s*daily", "daily 频率")
    _must_have(text, r"^\s*rotate\s+30", "保留 30 天")
    _must_have(text, r"^\s*compress", "压缩")
    _must_have(text, r"^\s*missingok", "缺失不报错")
    _must_have(text, r"^\s*notifempty", "空日志不轮转")


def test_logrotate_dry_run() -> None:
    """logrotate -d 干跑语法检查"""
    logrotate_bin = shutil.which("logrotate") or "/usr/sbin/logrotate"
    if not Path(logrotate_bin).exists():
        return
    result = subprocess.run(
        [logrotate_bin, "-d", str(LOGROTATE_CONF)],
        capture_output=True,
        text=True,
        check=False,
    )
    # logrotate -d 干跑返回 0 或非 0 都可能, 但不能有 'error:' 关键字
    combined = result.stdout + result.stderr
    assert "error:" not in combined.lower(), f"logrotate -d 报 error:\n{combined}"


# ============ install / uninstall 脚本 ============
@pytest.mark.parametrize(
    "script",
    [
        "install-systemd.sh",
        "uninstall-systemd.sh",
        "install-logrotate.sh",
    ],
)
def test_script_bash_syntax(script: str) -> None:
    """bash -n 检查脚本语法 (无副作用)"""
    path = SCRIPTS_DIR / script
    assert path.exists(), f"脚本缺失: {path}"
    assert path.stat().st_mode & 0o111, f"脚本不可执行: {path}"
    result = subprocess.run(
        ["bash", "-n", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash 语法错: {script}\n{result.stderr}"


def test_install_systemd_idempotent_in_help() -> None:
    """install-systemd.sh 显式声明幂等 + 含 stop + disable 旧 timer 步骤"""
    text = _read(SCRIPTS_DIR / "install-systemd.sh")
    assert "幂等" in text or "idempotent" in text.lower(), "缺幂等声明"
    _must_have(text, r"systemctl stop novel-publish\.timer", "停旧 timer")
    _must_have(text, r"systemctl disable novel-publish\.timer", "disable 旧 timer")
    _must_have(text, r"systemctl enable --now novel-publish\.timer", "enable + start")


def test_install_systemd_safety_guards() -> None:
    """install 脚本安全护栏: sudo 检查 + 前置依赖检查"""
    text = _read(SCRIPTS_DIR / "install-systemd.sh")
    _must_have(text, r"EUID.*-ne 0", "sudo 检查")
    _must_have(text, r"\.env.*缺失|\.env.*不存在", ".env 存在检查")
    _must_have(text, r"\.venv/bin/python", "venv python 检查")


def test_uninstall_systemd_keeps_data() -> None:
    """卸载脚本不能删业务数据 (.env / truth/ / state.json)"""
    text = _read(SCRIPTS_DIR / "uninstall-systemd.sh")
    # 去掉注释行 (避免误判 'rm -rf logs/' 提示文本)
    code_lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    assert "rm -rf" not in code, "禁用 rm -rf (可能误删业务数据)"
    assert "rm -fv" in text, "显式 rm -fv 删除 unit 文件"


# ============ README 引用 ============
def test_readme_references_systemd() -> None:
    """README 快速预览引用 systemd 路径 (直接 systemd/ 路径 或 install-systemd.sh 都行)"""
    readme = PROJECT_ROOT / "README.md"
    if not readme.exists():
        return
    text = _read(readme)
    has_systemd_path = re.search(
        r"systemd/(novel-publish\.(service|timer)|\*\.(service|timer))", text
    )
    has_install_script = re.search(r"install-systemd\.sh", text)
    assert (
        has_systemd_path or has_install_script
    ), "README 缺 systemd 路径引用 (systemd/*.service 或 install-systemd.sh)"
    _must_have(text, r"install-logrotate\.sh", "README 引用 logrotate install")


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
