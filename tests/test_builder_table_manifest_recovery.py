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
