from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import copy,json

import numpy as np
import pandas as pd
import pytest

from microwire_data_builder import core as builder,project_package as pp,safe_codec,ui
from plotting.plugins.mini_dma import core


def calibrated_record(tmp_path):
    folder=tmp_path/'Ni50Fe27Ga23 12_2 iso-stress';folder.mkdir()
    position=np.concatenate([np.linspace(0,.6,13),np.linspace(.6,0,13)[1:]])
    n=len(position)
    frame=pd.DataFrame({'elapsed_s':range(n),'automation_phase':['current']*n,
        'automation_target_value':[20.]*n,'plateau_index':[0]*n,
        'current_measured_mA':position*50,'current_mA':position*50,
        'position_mm':position,'raw_position_mm':12.-position,
        'strain_pct':100*position/40.,'current_l0_mm':[20.]*n,'resistance_ohm':[50.]*n})
    frame.to_csv(folder/'measurement.csv',index=False)
    (folder/'metadata.json').write_text(json.dumps({'initial_length_mm':20.,'sample_name':folder.name}),encoding='utf-8')
    (folder/'setup.txt').write_text('# Computed l0 mm\t40.0\n',encoding='utf-8')
    record=builder.MiniDmaRecord(path=folder,sample=folder.name,data=frame,key=('Ni50Fe27Ga23',12,2))
    record.initial_length_calibration=builder.capture_mini_dma_initial_length_calibration(record)
    return folder,record


def test_saved_verified_calibration_roundtrips_and_survives_unavailable_sources(tmp_path,monkeypatch):
    folder,record=calibrated_record(tmp_path)
    assert record.initial_length_calibration['initial_length_mm']==40.
    source_bytes={p.name:p.read_bytes() for p in folder.iterdir()}
    project=tmp_path/'portable.pydpj'
    pp.write_project_package(project,{'kind':pp.PROJECT_KIND,'sections':{'mini_dma':{
        'section':'mini_dma','columns':[],'rows':[],'index':[],
        'payloads':{'mini_dma_records':safe_codec.encode_envelope([record])}}}})
    restored=pp.ProjectPayloadResolver(pp.inspect_project_package(project,verify_entries=True)).load_record_list('mini_dma','mini_dma_records')[0]
    assert source_bytes=={p.name:p.read_bytes() for p in folder.iterdir()}
    folder.rename(tmp_path/'unavailable-original-source')
    monkeypatch.setattr(core,'load_run',lambda *a,**k:pytest.fail('Portable preview must not reread raw source'))
    run=ui._mini_dma_preview_run(restored,downsample=False)
    assert run.initial_length_mm==40.
    assert core.strain_from_global_minimum_length(run,[run.frame])[0].max()==pytest.approx(1.5)
    pd.testing.assert_frame_equal(restored.data,record.data,check_exact=True)
    assert restored.initial_length_calibration==record.initial_length_calibration
    assert builder._mini_dma_peak_strain_summary(restored)


def test_legacy_source_free_logged_length_is_not_promoted_to_verified_calibration(tmp_path):
    folder,record=calibrated_record(tmp_path)
    record.initial_length_calibration=None
    encoded=safe_codec.encode_envelope([record])
    state=encoded['value']['items'][0]['state']['items']
    state[:]=[pair for pair in state if pair[0]!='initial_length_calibration']
    legacy=safe_codec.decode_envelope(encoded)[0]
    folder.rename(tmp_path/'unavailable-original-source')
    assert legacy.initial_length_calibration is None
    assert builder.mini_dma_record_initial_length_mm(legacy) is None
    assert ui._mini_dma_preview_run(legacy,downsample=False).initial_length_mm is None


@pytest.mark.parametrize('change',['frame','length','path'])
def test_stale_or_inconsistent_saved_calibration_is_rejected(tmp_path,change):
    folder,record=calibrated_record(tmp_path)
    folder.rename(tmp_path/'unavailable-original-source')
    if change=='frame':
        frame=record.data.copy();frame.loc[2,'position_mm']+=.01
        record=replace(record,data=frame)
    elif change=='length':
        record.initial_length_calibration=copy.deepcopy(record.initial_length_calibration)
        record.initial_length_calibration['initial_length_mm']=80.
    else:record=replace(record,path=tmp_path/'different-source')
    assert builder.verified_mini_dma_initial_length_mm(record) is None
    assert builder.mini_dma_record_initial_length_mm(record) is None


def test_ambiguous_setup_cannot_create_saved_calibration(tmp_path):
    folder,record=calibrated_record(tmp_path)
    (folder/'setup.txt').write_text('# Computed l0 mm\t40.0001\n# Computed l0 mm\t40.0007\n',encoding='utf-8')
    assert builder.capture_mini_dma_initial_length_calibration(record) is None
    assert core.resolve_initial_length_mm(record.data,measurement_path=folder/'measurement.csv',require_verified=True) is None
    assert core.resolve_initial_length_mm(record.data,measurement_path=folder/'measurement.csv')==20.


def test_review_summary_clone_preserves_frame_calibration_and_strain_history(tmp_path):
    _,record=calibrated_record(tmp_path)
    record.strain_summary=('saved strain history',)
    record.transition_summary=('20 MPa: As=1mA Af=2mA',)
    target='20 MPa'
    rid=ui._mini_dma_review_record_id(record,target)
    reviews={rid:{'target_label':target,'status':ui.MINI_DMA_REVIEW_STATUS_ACCEPTED,'values':{'As':12.,'Af':18.}}}
    fake=SimpleNamespace(transition_reviews_snapshot=lambda:reviews)
    updated=ui.MiniDmaSection.records_with_reviewed_transitions(fake,[record])[0]
    assert updated is not record
    assert updated.data is record.data
    assert updated.initial_length_calibration==record.initial_length_calibration
    assert updated.strain_summary==record.strain_summary
    assert updated.transition_summary!=record.transition_summary
