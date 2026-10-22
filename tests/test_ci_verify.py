from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / 'scripts' / 'ci_verify.py'
spec = importlib.util.spec_from_file_location('ci_verify_under_test', RUNNER)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def invoke(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    # These subprocesses represent a fresh CLI invocation, even inside xdist.
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith('PYTEST_XDIST_')
    }
    return subprocess.run(
        [sys.executable, str(RUNNER), '--artifacts-dir', str(tmp_path / 'runs'),
         '--run-id', 'unit', *extra],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False, timeout=60,
    )


def environment_lines(output: str) -> dict[str, str]:
    return dict(line.strip().split('=', 1) for line in output.splitlines() if '=' in line)


def test_help_lists_isolation_and_reporting_options(tmp_path: Path) -> None:
    result = invoke(tmp_path, '--help')
    assert result.returncode == 0
    for option in ('--mode', '--workers', '--dist', '--temp-root', '--junit-dir'):
        assert option in result.stdout


def test_default_paths_stay_in_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv('PYPLOT_VERIFY_TEMP_ROOT', raising=False)
    result = invoke(tmp_path, '--dry-run')
    assert result.returncode == 0, result.stderr
    values = environment_lines(result.stdout)
    for key in ('TEMP', 'TMP', 'TMPDIR', 'pytest_basetemp'):
        assert Path(values[key]).resolve().is_relative_to(REPO_ROOT / 'artifacts' / 't')
    assert values['QT_QPA_PLATFORM'] == 'offscreen'
    assert values['MPLBACKEND'] == 'Agg'
    assert values['PYPLOT_TEST_TEMP_ISOLATED'] == '1'
    assert 'test_launcher_detects_pyplot_automation_flags' in result.stdout


def test_explicit_temp_parent_is_private_and_preserves_existing_files(tmp_path: Path) -> None:
    parent = tmp_path / 'short'
    parent.mkdir()
    sentinel = parent / 'keep.txt'
    sentinel.write_text('unrelated', encoding='ascii')
    result = invoke(tmp_path, '--temp-root', str(parent), '--dry-run')
    assert result.returncode == 0, result.stderr
    values = environment_lines(result.stdout)
    base = Path(values['pytest_basetemp']).resolve()
    assert base.is_relative_to(parent.resolve()) and base != parent.resolve()
    assert sentinel.read_text(encoding='ascii') == 'unrelated'
    assert Path(values['TEMP']).resolve() != base


def test_cli_temp_root_overrides_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('PYPLOT_VERIFY_TEMP_ROOT', str(tmp_path / 'env'))
    env_result = invoke(tmp_path, '--dry-run')
    assert env_result.returncode == 0, env_result.stderr
    assert Path(environment_lines(env_result.stdout)['TEMP']).resolve().is_relative_to(tmp_path / 'env')
    cli_result = invoke(tmp_path, '--temp-root', str(tmp_path / 'cli'), '--dry-run')
    assert cli_result.returncode == 0, cli_result.stderr
    assert Path(environment_lines(cli_result.stdout)['TEMP']).resolve().is_relative_to(tmp_path / 'cli')


def test_unusable_temp_parent_fails_preflight_without_traceback(tmp_path: Path) -> None:
    blocked = tmp_path / 'blocked'
    blocked.write_text('not a directory', encoding='ascii')
    result = invoke(tmp_path, '--temp-root', str(blocked), '--dry-run')
    assert result.returncode == 2
    assert 'preflight' in result.stderr.lower()
    assert str(blocked) in result.stderr
    assert '--temp-root' in result.stderr
    assert 'Traceback' not in result.stderr
    assert blocked.read_text(encoding='ascii') == 'not a directory'


def test_denied_write_is_reported_before_pytest(tmp_path: Path, monkeypatch, capsys) -> None:
    original_mkdir = Path.mkdir

    def deny_temp(path, *args, **kwargs):
        if path.is_relative_to(tmp_path / 'denied'):
            raise PermissionError(13, 'simulated denied write', str(path))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'mkdir', deny_temp)
    with pytest.raises(SystemExit) as error:
        runner.main(['--temp-root', str(tmp_path / 'denied'), '--artifacts-dir', str(tmp_path / 'runs')])
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert 'preflight' in stderr.lower() and 'simulated denied write' in stderr


@pytest.mark.parametrize('run_id', ['../escape', 'x/y', 'x\\y', '.', 'a' * 49])
def test_invalid_run_id_is_rejected(tmp_path: Path, run_id: str) -> None:
    result = invoke(tmp_path, '--run-id', run_id, '--dry-run')
    assert result.returncode == 2
    assert '--run-id' in result.stderr


@pytest.mark.parametrize('option', ['--basetemp', '--basetemp=', '--baset', '--baset='])
def test_pytest_cannot_override_owned_basetemp(tmp_path: Path, option: str) -> None:
    target = str(tmp_path / 'unowned')
    arguments = [option + target] if option.endswith('=') else [option, target]
    result = invoke(tmp_path, '--dry-run', *arguments)
    assert result.returncode == 2
    assert '--temp-root' in result.stderr


def test_parallel_and_serial_lanes_have_separate_reports_and_marker_filters(tmp_path: Path) -> None:
    result = invoke(tmp_path, '--mode', 'full', '--dry-run', '--junit-dir', str(tmp_path / 'reports'), '-m', 'example')
    assert result.returncode == 0, result.stderr
    assert 'lane=parallel' in result.stdout and '-n 4' in result.stdout
    assert 'lane=serial' in result.stdout and '-n 0' in result.stdout
    assert '(example) and (not serial)' in result.stdout
    assert '(example) and (serial)' in result.stdout
    assert 'parallel.xml' in result.stdout and 'serial.xml' in result.stdout


def test_workers_zero_keeps_one_lane(tmp_path: Path) -> None:
    result = invoke(tmp_path, '--mode', 'full', '--workers', '0', '--dry-run')
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('lane=') == 1
    assert '-n ' not in result.stdout


def test_real_lanes_preserve_reports_and_failure_exit(tmp_path: Path) -> None:
    suite = tmp_path / 'test_disposable_runner.py'
    suite.write_text(
        'import os\nfrom pathlib import Path\nimport pytest\n'
        'def test_parallel_isolation():\n'
        '    assert os.environ["PYTEST_XDIST_WORKER"].startswith("gw")\n'
        '    for key in ("TEMP", "TMP", "TMPDIR", "PYTEST_QSETTINGS_ROOT", "MICROWIRE_BUILDER_STORAGE_ROOT", "MPLCONFIGDIR"):\n'
        '        # This disposable suite has no repository conftest; the regular\n'
        '        # isolation test exercises worker suffixes in the actual gate.\n'
        '        assert Path(os.environ[key]).is_dir()\n'
        '@pytest.mark.serial\n'
        'def test_serial_failure():\n'
        '    assert "PYTEST_XDIST_WORKER" not in os.environ\n'
        '    assert False, "intentional runner regression failure"\n',
        encoding='utf-8',
    )
    result = invoke(tmp_path, '--workers', '2', '--temp-root', str(tmp_path / 'temp'),
                    '--junit-dir', str(tmp_path / 'reports'), str(suite), '-q')
    assert result.returncode == 1
    assert 'intentional runner regression failure' in result.stdout
    parallel = ElementTree.parse(tmp_path / 'reports' / 'parallel.xml').getroot()
    serial = ElementTree.parse(tmp_path / 'reports' / 'serial.xml').getroot()
    assert len(parallel.findall('.//testcase')) == 1
    assert len(serial.findall('.//failure')) == 1


def test_no_selected_tests_remains_a_failure(tmp_path: Path) -> None:
    suite = tmp_path / 'test_empty_selection.py'
    suite.write_text('def test_example(): pass\n', encoding='ascii')
    result = invoke(tmp_path, '--temp-root', str(tmp_path / 'temp'), str(suite), '-k', 'absent', '-q')
    assert result.returncode == 5
    assert 'No tests selected' in result.stdout
