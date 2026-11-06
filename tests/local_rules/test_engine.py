import json
import unicodedata
from pathlib import Path
import pytest
from features.voice_assistant.local_rules.engine import IntentEngine, normalize, load_rules
from features.voice_assistant.local_rules.dialogue import Dialogue

CASES=json.loads(Path(__file__).with_name('golden.json').read_text(encoding='utf-8'))
ENGINE=IntentEngine()

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id']+'_'+c['group'])
def test_golden(case):
    value=ENGINE.parse(case['text'],case['context'],'final1')
    assert [a['intent'] for a in value['actions']]==case['expected_intents']
    assert [a['roles'] for a in value['actions']]==case['expected_roles']
    assert [a['needs_confirmation'] for a in value['actions']]==case['expected_confirmation']
    if 'expected_parameters' in case:
        assert [a['parameters'] for a in value['actions']]==case['expected_parameters']
    assert value['match_level']==case['expected_level']
    assert value['original_text']==case['text']
    assert all(a['rule_id'] and a['matched_text'] for a in value['actions'])

def test_normalizer():
    assert normalize(unicodedata.normalize('NFD','  TÔI CHƯA uống thuốc! '))=='tôi chưa uống thuốc'

def test_invalid_rules(tmp_path):
    cfg=ENGINE.config.copy();cfg['rules']=cfg['rules']+[cfg['rules'][0]]
    p=tmp_path/'rules.json';p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):load_rules(p)

def test_expiry_and_patient_switch():
    stamp=[0];d=Dialogue(60,clock=lambda:stamp[0]);ctx={'patient_id':'a','dose':'one'}
    d.offer({'actions':['send']},ctx,[]);stamp[0]=60
    assert d.approve(ctx) is None
    d.offer({},ctx,[]);assert d.approve(dict(ctx,patient_id='b')) is None
    d.offer({},ctx,[]);assert d.approve(dict(ctx,dose='two')) is None

def test_cancel_and_clarification_limit():
    d=Dialogue();d.offer({}, {}, []);d.clear();assert d.approve({}) is None
    assert 'Bác sĩ / Người nhà / Hủy' in [d.clarify() for _ in range(3)][-1]

@pytest.mark.parametrize('text',['Hình như tôi uống rồi','Tôi không nhớ đã uống thuốc','Tôi uống rồi?','Tôi không thở được?','Đọc câu cứu tôi'])
def test_uncertain_or_quoted_never_executes(text):
    value=ENGINE.parse(text,{'medication_occurrence_id':'d1'})
    assert not value['actions'];assert value['match_level']=='ambiguous'


def test_scope_is_clause_based_current_urgent_survives_past_clause():
    value=ENGINE.parse('Hôm qua tôi đau đầu, bây giờ tôi không thở được')
    assert [a['intent'] for a in value['actions']]==['urgent_help']


def test_conflicts_ask_instead_of_scoring():
    ctx={'running':True,'medication_occurrence_id':'d1'}
    for text in ['Cho tôi tập, dừng bài tập','Tôi uống rồi, tôi chưa uống thuốc']:
        value=ENGINE.parse(text,ctx);assert not value['actions'];assert value['match_level']=='ambiguous'
