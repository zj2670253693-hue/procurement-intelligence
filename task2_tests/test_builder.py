import importlib
from pathlib import Path
import pytest
from task2_relationship_analysis.contracts import InputContractError, InputChangedError
from task2_tests.test_identities import BASE, table

def build(input_case, local_tmp, body=None):
    root,old,v11=input_case(extra={'project_name':'测试项目','procurement_unit':'采购甲','winning_supplier':'甲公司','project_amount':'100元','bidders':[]},body=body or BASE+table('甲公司'))
    out=local_tmp/'outputs'
    result=importlib.import_module('task2_relationship_analysis.builder').build_dataset(root,old,v11,out)
    return result,(root,old,v11,out)

def test_partial_failure_keeps_other_facts(input_case, local_tmp):
    result,paths=build(input_case,local_tmp)
    assert result.status=='completed' and result.manifest['new_model_calls']==0
    assert result.manifest['legacy_producer']=='legacy_model_unknown'
    verify=importlib.import_module('task2_relationship_analysis.publication').verify_artifacts
    assert verify(Path(result.output_dir))['valid']

def test_output_cannot_overlap_inputs(input_case):
    root,old,v11=input_case()
    builder=importlib.import_module('task2_relationship_analysis.builder')
    with pytest.raises(InputContractError): builder.build_dataset(root,old,v11,root)
    with pytest.raises(InputContractError): builder.build_dataset(root,old,v11,root.parent)

def test_implementation_change_changes_version(input_case, local_tmp, monkeypatch):
    result,paths=build(input_case,local_tmp)
    module=importlib.import_module('task2_relationship_analysis.builder')
    original=module.implementation_fingerprint
    monkeypatch.setattr(module,'implementation_fingerprint',lambda p:{**original(p),'test_variant':'changed'})
    second=module.build_dataset(*paths)
    assert result.dataset_id!=second.dataset_id

def test_one_large_html_at_a_time_and_input_change_aborts(input_case,local_tmp,monkeypatch):
    result,paths=build(input_case,local_tmp,BASE+table('甲公司')+'<p>'+'文'*1000000+'</p>')
    assert result.manifest['processing_mode']=='one_html_at_a_time'
    module=importlib.import_module('task2_relationship_analysis.builder')
    original=module.index_html
    def altered(aid,raw,ds):
        (paths[0]/(aid+'.html')).write_bytes(raw+b'<p>changed</p>')
        return original(aid,raw,ds)
    monkeypatch.setattr(module,'index_html',altered)
    with pytest.raises(InputChangedError): module.build_dataset(*paths)
