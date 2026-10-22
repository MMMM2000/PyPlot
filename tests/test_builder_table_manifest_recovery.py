import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from microwire_data_builder import project_package as pp
from microwire_data_builder.table_manifest_recovery import recover_table_manifest
from microwire_data_builder.safe_codec import SafeCodecError


def edited_package(tmp_path, *, entry='sections/microscope/table.json', invalid=False):
    original = tmp_path/'original.pydpj'
    pp.write_project_package(original, {'kind': pp.PROJECT_KIND, 'sections': {'microscope': {
        'columns': ['Composition','Microwire','d (um)'],
        'rows': [{'Composition':'Ni50Fe27Ga23','Microwire':'11/1','d (um)':14}],
        'index': [0]}}})
    edited = tmp_path/'edited.pydpj'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(edited,'w') as target:
        for info in source.infolist():
            raw=source.read(info)
            if info.filename==entry:
                value=json.loads(raw)
                if invalid:value={'rows':'invalid'}
                elif entry.endswith('table.json'):value['rows'][0]['d (um)']=15.25
                else:value['edited']=True
                raw=json.dumps(value).encode()
            target.writestr(info,raw)
    return edited


def test_recovery_preserves_source_and_imports_verified_edited_table(tmp_path):
    source=edited_package(tmp_path)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(SafeCodecError):pp.load_project_table_projection(source)
    target=tmp_path/'recovered.pydpj'
    assert recover_table_manifest(source,target)==['sections/microscope/table.json']
    pp.inspect_project_package(target,verify_entries=True)
    payload=pp.load_project_table_projection(target)
    assert payload['sections']['microscope']['rows'][0]['d (um)']==15.25
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert 'recovery' in pp.inspect_project_package(target).manifest['writer']


@pytest.mark.parametrize('entry,invalid', [('sections/microscope/state.json',False),('sections/microscope/table.json',True)])
def test_recovery_rejects_non_table_damage_and_invalid_tables(tmp_path,entry,invalid):
    source=edited_package(tmp_path,entry=entry,invalid=invalid)
    target=tmp_path/'failed.pydpj'
    with pytest.raises(SafeCodecError):recover_table_manifest(source,target)
    assert not target.exists()
    assert not list(tmp_path.glob('.table-recovery-*'))


def test_recovery_never_overwrites_existing_destination(tmp_path):
    source=edited_package(tmp_path);target=tmp_path/'existing.pydpj'
    target.write_bytes(b'keep')
    with pytest.raises(FileExistsError):recover_table_manifest(source,target)
    assert target.read_bytes()==b'keep'


def test_valid_package_does_not_need_recovery(tmp_path):
    source=tmp_path/'valid.pydpj'
    pp.write_project_package(source, {'kind': pp.PROJECT_KIND, 'sections': {}})
    with pytest.raises(SafeCodecError,match='No table descriptor mismatch'):
        recover_table_manifest(source,tmp_path/'unnecessary.pydpj')


@pytest.mark.parametrize('damage', ['mimetype', 'header_order', 'compressed_mimetype',
                                   'compressed_manifest', 'revision_bool', 'revision_zero',
                                   'empty_writer', 'empty_saved_at'])
def test_recovery_rejects_damaged_headers_and_overwritten_metadata(tmp_path, damage):
    edited = edited_package(tmp_path)
    source, destination = tmp_path/'damaged.pydpj', tmp_path/'recovered.pydpj'
    with zipfile.ZipFile(edited) as old, zipfile.ZipFile(source, 'w') as new:
        infos = old.infolist()
        if damage == 'header_order':
            infos[0], infos[1] = infos[1], infos[0]
        for info in infos:
            raw = old.read(info)
            if info.filename == pp.MIMETYPE_PATH:
                if damage == 'mimetype':
                    raw = b'not-a-builder-package'
                if damage == 'compressed_mimetype':
                    info.compress_type = zipfile.ZIP_DEFLATED
            if info.filename == pp.MANIFEST_PATH:
                manifest = json.loads(raw)
                if damage == 'revision_bool': manifest['revision'] = True
                if damage == 'revision_zero': manifest['revision'] = 0
                if damage == 'empty_writer': manifest['writer'] = ''
                if damage == 'empty_saved_at': manifest['saved_at'] = ''
                raw = json.dumps(manifest).encode()
                if damage == 'compressed_manifest':
                    info.compress_type = zipfile.ZIP_DEFLATED
            new.writestr(info, raw)
    before = source.read_bytes()
    with pytest.raises(SafeCodecError):
        recover_table_manifest(source, destination)
    assert source.read_bytes() == before
    assert not destination.exists()
    assert not list(tmp_path.glob('.table-recovery-*'))
