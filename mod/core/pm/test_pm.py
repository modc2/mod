"""Tests for the core process manager (pm) — service discovery, nix-image
resolution, and the launch-wrapper contents. No pm2/nix execution required."""
import os
import json
import stat
import importlib.util
from pathlib import Path

import pytest

PM_PATH = "/root/mod/mod/core/pm/mod.py"


def load_pm(repo: Path):
    """Load the pm module fresh with MOD_REPO pointed at a fake repo."""
    os.environ["MOD_REPO"] = str(repo)
    spec = importlib.util.spec_from_file_location("pmmod_test", PM_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mod"
    (repo / "core" / "nix").mkdir(parents=True)
    (repo / "core" / "nix" / "flake.nix").write_text("{}")
    return repo


def make_module(repo: Path, name: str, with_flake=False, with_app=True) -> Path:
    d = repo / "orbit" / name
    (d / "src" / "api").mkdir(parents=True)
    (d / "src" / "api" / "start.sh").write_text("#!/bin/bash\nexec true\n")
    if with_app:
        (d / "src" / "app").mkdir(parents=True)
        (d / "src" / "app" / "start.sh").write_text("#!/bin/bash\nexec true\n")
    (d / "config.json").write_text(json.dumps({"name": name, "port": 12345}))
    if with_flake:
        (d / "flake.nix").write_text("{}")
    return d


def test_services_discovers_api_and_app(tmp_path):
    repo = make_repo(tmp_path)
    make_module(repo, "foo")
    pm = load_pm(repo).Pm()
    assert {s[0] for s in pm.services("foo")} == {"api", "app"}


def test_services_falls_back_to_toplevel_start(tmp_path):
    repo = make_repo(tmp_path)
    d = repo / "orbit" / "solo"
    d.mkdir(parents=True)
    (d / "start.sh").write_text("#!/bin/bash\nexec true\n")
    (d / "config.json").write_text("{}")
    pm = load_pm(repo).Pm()
    assert [s[0] for s in pm.services("solo")] == ["main"]


def test_image_prefers_module_flake(tmp_path):
    repo = make_repo(tmp_path)
    make_module(repo, "bar", with_flake=True)
    pm = load_pm(repo).Pm()
    if pm.has_nix():
        assert pm.image("bar") == f"path:{repo / 'orbit' / 'bar'}"


def test_image_falls_back_to_shared(tmp_path):
    repo = make_repo(tmp_path)
    make_module(repo, "baz")  # no own flake
    pm = load_pm(repo).Pm()
    if pm.has_nix():
        assert pm.image("baz") == f"path:{repo / 'core' / 'nix'}"


def test_wrapper_imports_nix_image_then_execs(tmp_path):
    repo = make_repo(tmp_path)
    d = make_module(repo, "qux")
    pm = load_pm(repo).Pm()
    wp = pm._wrapper("qux", "api", d / "src" / "api", "bash start.sh")
    try:
        content = Path(wp).read_text()
        assert "exec bash start.sh" in content
        assert f'cd "{d / "src" / "api"}"' in content
        if pm.has_nix():
            assert "nix print-dev-env" in content
        assert os.stat(wp).st_mode & stat.S_IXUSR  # wrapper is executable
    finally:
        Path(wp).unlink(missing_ok=True)


def test_image_info_shape(tmp_path):
    repo = make_repo(tmp_path)
    make_module(repo, "foo")
    pm = load_pm(repo).Pm()
    info = pm.image_info("foo")
    assert info["module"] == "foo"
    assert info["services"] == ["api", "app"]
    assert "image" in info and "nix_available" in info


def test_unknown_module_raises(tmp_path):
    repo = make_repo(tmp_path)
    pm = load_pm(repo).Pm()
    with pytest.raises(FileNotFoundError):
        pm.dir("does-not-exist")
