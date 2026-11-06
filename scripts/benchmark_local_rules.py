"""Measure pure parser only; no audio, network, patient database or notification adapter."""
import json
import math
import platform
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from features.voice_assistant.local_rules.engine import IntentEngine


def main():
    engine=IntentEngine()
    cases=json.loads(Path('tests/local_rules/golden.json').read_text(encoding='utf-8'))
    groups=defaultdict(list);correct=defaultdict(int);counts=defaultdict(int)
    for case in cases:
        value=engine.parse(case['text'],case['context'],case['id'])
        group=case['group'];counts[group]+=1
        correct[group]+=int([a['intent'] for a in value['actions']]==case['expected_intents'] and
                           [a['roles'] for a in value['actions']]==case['expected_roles'] and
                           [a['needs_confirmation'] for a in value['actions']]==case['expected_confirmation'] and
                           value['match_level']==case['expected_level'])
    for _ in range(30):
        for case in cases:
            start=time.perf_counter_ns();engine.parse(case['text'],case['context'],case['id'])
            groups[case['group']].append((time.perf_counter_ns()-start)/1e6)
    def measure(values):
        ordered=sorted(values)
        return dict(samples=len(values),p50_ms=round(statistics.median(values),4),p95_ms=round(ordered[math.ceil(.95*len(values))-1],4),max_ms=round(max(values),4))
    report=dict(machine=platform.platform(),python=platform.python_version(),processor=platform.processor(),
                dataset=f'{len(cases)} synthetic golden cases; no field accuracy claim',stt='not part of parser timing',network='not used',
                groups={g:dict(cases=counts[g],correct=correct[g],**measure(v)) for g,v in groups.items()},
                total=measure([x for values in groups.values() for x in values]))
    out=Path('docs/test-reports/local_rules/benchmark.json');out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    assert all(correct[g]==counts[g] for g in counts)
    assert report['total']['p95_ms']<100

if __name__=='__main__':main()
