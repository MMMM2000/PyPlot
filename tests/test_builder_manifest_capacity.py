"""A large blob index must fit within the independent archive-entry budget."""
from pathlib import Path
import zipfile

import numpy as np
import pytest

from microwire_data_builder import project_package as pp, safe_codec


def payload(value):
    return {'kind':pp.PROJECT_KIND,'sections':{'mini_dma':{
        'section':'mini_dma','title':'Disposable capacity regression',
        'columns':[],'rows':[],'index':[],
        'payloads':{'capacity_values':safe_codec.encode_envelope(value)}}}}


def test_many_small_unique_blobs_roundtrip_above_previous_manifest_limit(tmp_path):
    values=[np.asarray([i],dtype=np.int32) for i in range(24_000)]
    target=tmp_path/'large-index.pydpj'
    index=pp.write_project_package(target,payload(values))
    with zipfile.ZipFile(target) as archive:
        manifest_bytes=archive.getinfo(pp.MANIFEST_PATH).file_size
    assert 8*1024*1024 < manifest_bytes <= pp.MAX_MANIFEST_BYTES
    assert len(index.blobs)==24_000
    assert len(index.entries)+2 < pp.MAX_ARCHIVE_ENTRIES
    checked=pp.inspect_project_package(target,verify_entries=True)
    restored=pp.ProjectPayloadResolver(checked).load('mini_dma','capacity_values')
    assert len(restored)==len(values)
    for original,recovered in zip(values,restored,strict=True):
        np.testing.assert_array_equal(original,recovered)


def test_manifest_byte_bound_still_rejects_before_replacing_target(tmp_path,monkeypatch):
    target=tmp_path/'preserved.pydpj'
    pp.write_project_package(target,payload([1]))
    original=target.read_bytes()
    monkeypatch.setattr(pp,'MAX_MANIFEST_BYTES',128)
    with pytest.raises(safe_codec.SafeCodecError,match='manifest exceeds'):
        pp.write_project_package(target,payload([2]))
    assert target.read_bytes()==original
    with pytest.raises(safe_codec.SafeCodecError,match='limit'):
        pp.inspect_project_package(target)


def test_archive_entry_bound_remains_independent_and_atomic(tmp_path,monkeypatch):
    target=tmp_path/'preserved.pydpj'
    pp.write_project_package(target,payload([1]))
    original=target.read_bytes()
    monkeypatch.setattr(pp,'MAX_ARCHIVE_ENTRIES',8)
    values=[np.asarray([i],dtype=np.int32) for i in range(8)]
    with pytest.raises(safe_codec.SafeCodecError,match='entry|entries'):
        pp.write_project_package(target,payload(values))
    assert target.read_bytes()==original
