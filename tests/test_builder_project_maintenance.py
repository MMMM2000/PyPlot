"""Corrections must regenerate manifests; publication must never lose old data."""
import hashlib
import json
import zipfile

import pytest

from microwire_data_builder import project_package as pp
from microwire_data_builder.safe_codec import SafeCodecError, encode_envelope


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(path):
    row = {'Composition': 'Ni50Fe27Ga23', 'Microwire': '9/3',
           'd (um)': 15.6, 'D (um)': 23.9, 'd/D': 0.653}
    table = {'columns': list(row), 'rows': [row], 'index': [0]}
    pp.write_project_package(path, {'kind': pp.PROJECT_KIND, 'sections': {
        'microscope': {**table, 'extra': {'reviewed': True}},
        'assemble': {**table, 'selected_columns': ['Microwire']},
        'annealing': {'columns': [], 'rows': [], 'index': [],
                      'payloads': {'records': encode_envelope({'raw': b'keep-me'})}},
    }})
    return table


def test_table_correction_regenerates_descriptors_and_preserves_other_entries(tmp_path):
    from microwire_data_builder.project_maintenance import update_project_tables
    source, result = tmp_path/'source.pydpj', tmp_path/'result.pydpj'
    table = _project(source)
    before = _digest(source)
    table['rows'][0].update({'D (um)': 71.8, 'd/D': 0.217})
    update_project_tables(source, result, {'microscope': table, 'assemble': table})
    index = pp.inspect_project_package(result, verify_entries=True)
    assert index.manifest['revision'] == 2
    for section in ('microscope', 'assemble'):
        row = index.read_section(section, load_payloads=False)['rows'][0]
        assert row['D (um)'] == 71.8
        assert row['d/D'] == 0.217
        assert row['d (um)'] == 15.6
    assert index.read_section('microscope', load_payloads=False)['extra'] == {'reviewed': True}
    from data_logging.current_annealing_logger.current_annealing_logger import MainWindow
    records = MainWindow._records_from_project_payload(
        pp.load_project_table_projection(result), source=result.name)
    assert any(r.microwire == '9/3' and r.diameter_um == 15.6 for r in records)
    assert _digest(source) == before
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(result) as new:
        for name in old.namelist():
            if name not in {'manifest.json', 'sections/microscope/table.json', 'sections/assemble/table.json'}:
                assert old.read(name) == new.read(name), name


@pytest.mark.parametrize('tables', [{}, {'missing': {}}, {'microscope': {'rows': 'invalid'}}])
def test_table_update_rejects_invalid_input_without_publishing(tmp_path, tables):
    from microwire_data_builder.project_maintenance import update_project_tables
    source, result = tmp_path/'source.pydpj', tmp_path/'result.pydpj'
    _project(source)
    with pytest.raises(SafeCodecError):
        update_project_tables(source, result, tables)
    assert not result.exists()


def test_table_update_never_overwrites_source_or_existing_copy(tmp_path):
    from microwire_data_builder.project_maintenance import update_project_tables
    source, result = tmp_path/'source.pydpj', tmp_path/'result.pydpj'
    table = _project(source)
    before = source.read_bytes()
    result.write_bytes(b'keep')
    for destination in (source, result):
        with pytest.raises(FileExistsError):
            update_project_tables(source, destination, {'microscope': table})
    assert source.read_bytes() == before
    assert result.read_bytes() == b'keep'


def test_publication_verifies_staged_copy_and_retains_exact_backup(tmp_path):
    from microwire_data_builder.project_maintenance import update_project_tables, publish_verified_project
    target, candidate, backup = (tmp_path/n for n in ('latest.pydpj', 'candidate.pydpj', 'backup.pydpj'))
    table = _project(target)
    old_bytes = target.read_bytes()
    table['rows'][0]['D (um)'] = 71.8
    update_project_tables(target, candidate, {'microscope': table})
    receipt = publish_verified_project(candidate, target, backup=backup,
                                       expected_target_sha256=_digest(target))
    assert backup.read_bytes() == old_bytes
    assert target.read_bytes() == candidate.read_bytes()
    pp.inspect_project_package(target, verify_entries=True)
    assert receipt['published_sha256'] == _digest(target)
    assert receipt['previous_sha256'] == _digest(backup)
    assert receipt['strict_verification_passed'] is True


@pytest.mark.parametrize('failure', ['stale_target', 'invalid_candidate', 'existing_backup', 'writer_lock', 'other_project'])
def test_publication_refuses_unsafe_replacement(tmp_path, failure):
    from microwire_data_builder.project_maintenance import update_project_tables, publish_verified_project
    target, candidate, backup = (tmp_path/n for n in ('latest.pydpj', 'candidate.pydpj', 'backup.pydpj'))
    table = _project(target)
    update_project_tables(target, candidate, {'microscope': table})
    expected = _digest(target)
    if failure == 'stale_target':
        expected = '0' * 64
    elif failure == 'invalid_candidate':
        candidate.write_bytes(b'not a package')
    elif failure == 'existing_backup':
        backup.write_bytes(b'keep')
    elif failure == 'writer_lock':
        (tmp_path/'.latest.pydpj.save.lock').write_text('busy')
    elif failure == 'other_project':
        candidate.unlink()
        _project(candidate)
    before = target.read_bytes()
    with pytest.raises((SafeCodecError, FileExistsError)):
        publish_verified_project(candidate, target, backup=backup, expected_target_sha256=expected)
    assert target.read_bytes() == before
    if failure == 'existing_backup':
        assert backup.read_bytes() == b'keep'
    assert not list(tmp_path.glob('.project-publish-*'))


def test_publication_refuses_candidate_with_stale_table_manifest(tmp_path):
    from microwire_data_builder.project_maintenance import publish_verified_project
    target, candidate, backup = (tmp_path/n for n in ('latest.pydpj', 'candidate.pydpj', 'backup.pydpj'))
    _project(target)
    with zipfile.ZipFile(target) as old, zipfile.ZipFile(candidate, 'w') as new:
        for info in old.infolist():
            raw = old.read(info)
            if info.filename == 'sections/microscope/table.json':
                table = json.loads(raw)
                table['rows'][0]['D (um)'] = 71.8
                raw = json.dumps(table).encode()
            new.writestr(info, raw)
    before = target.read_bytes()
    with pytest.raises(SafeCodecError):
        publish_verified_project(candidate, target, backup=backup, expected_target_sha256=_digest(target))
    assert target.read_bytes() == before
    assert not backup.exists()


def test_publication_preserves_concurrent_target_edit(tmp_path, monkeypatch):
    from microwire_data_builder import project_maintenance as maintenance
    target, candidate, backup = (tmp_path/n for n in ('latest.pydpj', 'candidate.pydpj', 'backup.pydpj'))
    table = _project(target)
    maintenance.update_project_tables(target, candidate, {'microscope': table})
    expected = _digest(target)
    copy = maintenance._copy_exclusive
    def copy_then_external_edit(source, destination):
        copy(source, destination)
        target.write_bytes(b'concurrent user work')
    monkeypatch.setattr(maintenance, '_copy_exclusive', copy_then_external_edit)
    with pytest.raises(SafeCodecError, match='changed during publication'):
        maintenance.publish_verified_project(candidate, target, backup=backup, expected_target_sha256=expected)
    assert target.read_bytes() == b'concurrent user work'
    assert _digest(backup) == expected
    assert not list(tmp_path.glob('.project-publish-*'))


def test_publication_never_replaces_after_staging_copy_corruption(tmp_path, monkeypatch):
    from microwire_data_builder import project_maintenance as maintenance
    target, candidate, backup = (tmp_path/n for n in ('latest.pydpj', 'candidate.pydpj', 'backup.pydpj'))
    table = _project(target)
    maintenance.update_project_tables(target, candidate, {'microscope': table})
    copy = maintenance.shutil.copyfileobj
    def damaged_copy(source, destination, length):
        copy(source, destination, length)
        destination.write(b'damaged')
    monkeypatch.setattr(maintenance.shutil, 'copyfileobj', damaged_copy)
    before = target.read_bytes()
    with pytest.raises(SafeCodecError, match='changed during staging'):
        maintenance.publish_verified_project(candidate, target, backup=backup, expected_target_sha256=_digest(target))
    assert target.read_bytes() == before
    assert not backup.exists()
    assert not list(tmp_path.glob('.project-publish-*'))


def test_maintenance_commands_correct_and_publish_real_package(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    target, candidate, backup = (tmp_path/n for n in ('latest.pydpj', 'candidate.pydpj', 'backup.pydpj'))
    table = _project(target)
    table['rows'][0]['D (um)'] = 71.8
    changes = tmp_path/'tables.json'
    changes.write_text(json.dumps({'microscope': table, 'assemble': table}))
    root = Path(__file__).resolve().parents[1]
    command = subprocess.run([sys.executable, str(root/'scripts/update_builder_tables.py'),
                              str(target), str(candidate), str(changes)], capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    before = _digest(target)
    command = subprocess.run([sys.executable, str(root/'scripts/publish_builder_project.py'),
                              str(candidate), str(target), '--backup', str(backup),
                              '--expected-target-sha256', before], capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert json.loads(command.stdout)['published_sha256'] == _digest(target)
    assert _digest(backup) == before
    assert pp.load_project_table_projection(target)['sections']['microscope']['rows'][0]['D (um)'] == 71.8
