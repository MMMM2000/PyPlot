from pathlib import Path
import json
import numpy as np
import pandas as pd
import pytest

from plotting.plugins.mini_dma import core


def run_sources(tmp_path: Path, *, stale=20.0, acquired=40.0, setup=None):
    folder=tmp_path/'Ni50Fe27Ga23 12_2 iso-stress';folder.mkdir()
    positions=np.array([0., .2, .4, .6, .4, .2])
    frame=pd.DataFrame({'elapsed_s':range(6),'automation_phase':['current']*6,
        'automation_target_value':[20.]*6,'plateau_index':[0]*6,
        'current_measured_mA':[0.,10.,20.,30.,20.,10.],
        'position_mm':positions,'raw_position_mm':12.-positions,
        'strain_pct':100*positions/acquired,'resistance_ohm':[50.]*6})
    frame.to_csv(folder/'measurement.csv',index=False)
    (folder/'metadata.json').write_text(json.dumps({'initial_length_mm':stale,'sample_name':folder.name}),encoding='utf-8')
    (folder/'setup.txt').write_text(setup or f'# Computed l0 mm\t{acquired}\n# Zero-load position mm\t12.0\n',encoding='utf-8')
    return folder,frame


def test_stale_metadata_uses_corroborated_setup_without_changing_positions(tmp_path):
    folder,frame=run_sources(tmp_path)
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    run=core.load_run(folder)
    assert run.initial_length_mm==pytest.approx(40.)
    np.testing.assert_array_equal(run.frame.position_mm,frame.position_mm)
    np.testing.assert_array_equal(run.frame.strain_pct,frame.strain_pct)
    y=core.strain_from_global_minimum_length(run,[run.frame])[0]
    assert y.max()==pytest.approx(1.5)
    assert before=={p.name:p.read_bytes() for p in folder.iterdir()}


def test_later_unrelated_setup_is_not_the_acquisition_reference(tmp_path):
    folder,_=run_sources(tmp_path,setup='# Computed l0 mm\t40.0004\n# Computed l0 mm\t60.0\n')
    assert core.load_run(folder).initial_length_mm==pytest.approx(40.)


@pytest.mark.parametrize('problem',['reference_reset','wrong_units','conflicting_setup','unstable_ratio'])
def test_ambiguous_evidence_keeps_metadata(tmp_path,problem):
    folder,frame=run_sources(tmp_path)
    if problem=='reference_reset':frame.loc[3:,'raw_position_mm']+=2
    elif problem=='wrong_units':(folder/'setup.txt').write_text('# Computed l0 cm\t4.0\n',encoding='utf-8')
    elif problem=='conflicting_setup':(folder/'setup.txt').write_text('# Computed l0 mm\t40.0001\n# Computed l0 mm\t40.0007\n',encoding='utf-8')
    else:frame.loc[3:,'strain_pct']*=1.2
    frame.to_csv(folder/'measurement.csv',index=False)
    assert core.load_run(folder).initial_length_mm==pytest.approx(20.)


def test_embedded_preview_uses_same_calibration_without_raw_csv_reload(tmp_path,monkeypatch):
    from microwire_data_builder import ui,core as builder
    folder,frame=run_sources(tmp_path)
    record=builder.MiniDmaRecord(path=folder,sample=folder.name,data=frame)
    monkeypatch.setattr(ui.mini_dma_core,'load_run',lambda *a,**k:pytest.fail('Embedded preview must not reload CSV'))
    run=ui._mini_dma_preview_run(record,downsample=False)
    assert run.initial_length_mm==pytest.approx(40.)
    pd.testing.assert_frame_equal(run.frame,frame)


def test_nonfinite_metadata_and_per_target_lengths_are_not_global_L0():
    frame=pd.DataFrame({'current_l0_mm':[30.,40.]})
    assert core.resolve_initial_length_mm(frame,metadata={'initial_length_mm':float('inf')}) is None
    assert core.resolve_initial_length_mm(frame.assign(current_l0_mm=30.),metadata={})==30.
