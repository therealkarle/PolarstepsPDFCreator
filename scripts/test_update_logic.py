import subprocess
import sys
from pathlib import Path
import tempfile

from polarsteps_pdf_generator import (
    is_git_repo,
    check_git_updates,
    backup_config,
    perform_git_pull,
    perform_pip_upgrade,
    check_for_update,
    do_update,
    maybe_update,
)

# Simple dummy object to mimic subprocess.CompletedProcess
class DummyProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_is_git_repo_true(monkeypatch, tmp_path):
    # fake subprocess.run returning 'true'
    def fake_run(cmd, cwd, capture_output, text, **kwargs):
        return DummyProc(returncode=0, stdout="true\n")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert is_git_repo(tmp_path) is True


def test_is_git_repo_false(monkeypatch, tmp_path):
    def fake_run(cmd, cwd, capture_output, text, **kwargs):
        return DummyProc(returncode=1, stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert is_git_repo(tmp_path) is False


def test_check_git_updates(monkeypatch, tmp_path):
    # scenario where local != remote
    sequence = []

    def fake_run(cmd, cwd, capture_output, text, **kwargs):
        # first rev-parse HEAD
        if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "HEAD":
            return DummyProc(0, "abc123")
        # fetch
        if cmd[:3] == ["git", "fetch", "origin"]:
            return DummyProc(0, "")
        # remote head
        if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "origin/HEAD":
            return DummyProc(0, "def456")
        # is-inside-work-tree
        if cmd[:4] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return DummyProc(0, "true")
        return DummyProc(1, "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert check_git_updates(tmp_path) is True


def test_backup_config(tmp_path):
    # create dummy config
    cfg = tmp_path / "config.toml"
    cfg.write_text("foo=1")
    backup_config(tmp_path)
    # there should be at least one backup file
    backups = list(tmp_path.glob("config.toml.bak_*") )
    assert backups, "backup not created"


def test_perform_git_pull(monkeypatch, tmp_path):
    called = {}
    def fake_backup(path):
        called['backed'] = True
    monkeypatch.setattr("polarsteps_pdf_generator.backup_config", fake_backup)

    def fake_run(cmd, cwd, capture_output, text):
        return DummyProc(0, "pulled")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert perform_git_pull(tmp_path) is True
    assert called.get('backed')


def test_perform_pip_upgrade(monkeypatch):
    def fake_run(cmd, capture_output, text):
        return DummyProc(0, "upgraded")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert perform_pip_upgrade() is True


def test_check_for_update_git(monkeypatch, tmp_path):
    # ensure git repo path
    def fake_is(path):
        return True
    monkeypatch.setattr("polarsteps_pdf_generator.is_git_repo", fake_is)
    def fake_check(path):
        return True
    monkeypatch.setattr("polarsteps_pdf_generator.check_git_updates", fake_check)
    available, msg = check_for_update(tmp_path)
    assert available is True
    assert "newer commits" in msg


def test_do_update_git(monkeypatch, tmp_path):
    def fake_is(path):
        return True
    monkeypatch.setattr("polarsteps_pdf_generator.is_git_repo", fake_is)
    monkeypatch.setattr("polarsteps_pdf_generator.perform_git_pull", lambda p: True)
    assert do_update(tmp_path) is True


def test_maybe_update_flags(monkeypatch, tmp_path, capsys):
    # simulate args holding flags
    class Args:
        check_update = True
        update = False
        auto_update = False
        yes = False
    # stub check_for_update
    monkeypatch.setattr("polarsteps_pdf_generator.check_for_update", lambda p: (False, ""))
    try:
        maybe_update(tmp_path, {}, Args())
    except SystemExit:
        pass
    out = capsys.readouterr().out
    # language manager may not have loaded packs, so we check for key text
    assert "cli.update_checking" in out

    # test update flag triggers exit
    Args.check_update = False
    Args.update = True
    # stub do_update to avoid real operations
    monkeypatch.setattr("polarsteps_pdf_generator.do_update", lambda p: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    try:
        maybe_update(tmp_path, {}, Args())
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "cli.update_success" in out

    # test auto_update config causes check and update prompt
    Args.check_update = False
    Args.update = False
    cfg = {'auto_update': True}
    monkeypatch.setattr("polarsteps_pdf_generator.check_for_update", lambda p: (True, "new"))
    monkeypatch.setattr("polarsteps_pdf_generator.do_update", lambda p: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    try:
        maybe_update(tmp_path, cfg, Args())
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "cli.update_available" in out or "cli.update_success" in out


def run_all_tests():
    print("Running update logic tests...")
    import pytest
    pytest.main([__file__])


if __name__ == '__main__':
    run_all_tests()
