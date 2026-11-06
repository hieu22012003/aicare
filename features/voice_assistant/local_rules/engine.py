"""Pure clause-scoped matching. Never performs a business mutation."""
import json
import os
import re
import unicodedata
from pathlib import Path

RULES_PATH = Path(__file__).with_name('rules.json')


def normalize(text):
    text = unicodedata.normalize('NFC', text).lower()
    text = re.sub(r'[“”"\n;.!?]+', ',', text)
    return re.sub(r'\s+', ' ', text).strip(' ,')


def load_rules(path=RULES_PATH):
    rules = json.loads(Path(path).read_text(encoding='utf-8'))
    if rules.get('schema_version') != 1 or not isinstance(rules.get('rules'), list):
        raise ValueError('Invalid rules schema')
    ids = set()
    catalogue = {'urgent_help','doctor_contact','caregiver_contact','daily_assistance','companionship',
                 'medication_schedule','medication_taken','medication_not_taken','medication_snooze',
                 'medication_assistance','medication_question','medication_discomfort','rehab_start',
                 'rehab_stop','rehab_discomfort','health_report','ambiguous_help','confirm','cancel'}
    for r in rules['rules']:
        if set(r) != {'id', 'intent', 'patterns', 'roles', 'confirmation', 'context'} or r['id'] in ids:
            raise ValueError('Invalid/duplicate rule')
        if r['intent'] not in catalogue or not isinstance(r['id'],str):
            raise ValueError('Unsupported intent/rule ID')
        if (r['roles'] and r['intent']!='urgent_help' and not r['confirmation']) or (r['intent']=='medication_taken' and not r['confirmation']):
            raise ValueError('Approval cannot be disabled for mutations/ordinary notifications')
        ids.add(r['id'])
        if not r['patterns'] or r['context'] not in ('none', 'dose', 'exercise') or not isinstance(r['confirmation'], bool):
            raise ValueError('Invalid rule fields')
        if not set(r['roles']) <= {'doctor', 'caregiver'}:
            raise ValueError('Invalid recipient role')
        for pattern in r['patterns']:
            re.compile(pattern)
    for key in ('pending_seconds', 'repeat_seconds', 'retention_days','request_ttl_seconds','retry_seconds','retry_max','snooze_max_minutes'):
        if not isinstance(rules.get(key), (float, int)) or rules[key] <= 0:
            raise ValueError('Invalid policy duration')
    if not 1<=rules['snooze_max_minutes']<=30:
        raise ValueError('Snooze bound exceeds executor policy')
    if not isinstance(rules.get('snooze_duration_aliases'),dict):
        raise ValueError('Invalid duration aliases')
    for alias,minutes in rules['snooze_duration_aliases'].items():
        if not alias or type(minutes) is not int or not 1<=minutes<=rules['snooze_max_minutes']:
            raise ValueError('Invalid duration alias')
    return rules


class IntentEngine:
    def __init__(self, path=None):
        self.config = load_rules(path or os.getenv('VOICE_RULES_PATH') or RULES_PATH)
        self.rules = [(r, [re.compile(p) for p in r['patterns']]) for r in self.config['rules']]

    def parse(self, text, context=None, utterance_id=None):
        ctx = context or {}
        normalized = normalize(text)
        # Preserve all lexical negation/time/subject tokens. Aliases are explicit regex rules.
        result = dict(utterance_id=utterance_id, patient_id=ctx.get('patient_id'), original_text=text,
                      normalized_text=normalized, match_level='unknown', actions=[], rule_ids=[],
                      context_refs={k: ctx[k] for k in ('session_id', 'medication_occurrence_id', 'exercise_session_id') if ctx.get(k)},
                      clarification=None)
        scope_pattern = r'\b(nếu|giả sử|hôm qua|ngày mai|trước đây|lúc trước|ông ấy|bà ấy|bác sĩ nói|(?:con|cháu|mẹ|bố) tôi (?:bị|nói|đau|không thở)|đọc câu|nói câu)\b'
        uncertainty = r'\b(hình như|không nhớ|có lẽ|chắc là)\b'
        if re.search(uncertainty, normalized):
            result.update(match_level='ambiguous',clarification='Bác có chắc về yêu cầu này không? Hãy nói rõ hoặc dùng nút chọn.')
            return result
        scope_blocked = False
        clauses = re.split(r',|\b(?:nhưng|chỉ|rồi gọi|và)\b', normalized)
        last_contact_verb = None
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            if re.search(r'\b(hôm nay|bây giờ|hiện tại)\b',clause):
                scope_blocked = False
            if re.search(scope_pattern,clause):
                scope_blocked = True
                result['clarification'] = 'Bác đang cần hỗ trợ cho chính bác lúc này hay đang hỏi về tình huống khác?'
            if scope_blocked:
                continue
            if re.search(r'\b(đừng|không muốn|không cần|chưa muốn|không gọi)\b', clause):
                continue
            for rule, patterns in self.rules:
                match_clause = re.sub(r'^(?:bây giờ|hiện tại|hôm nay)\s+','',clause) if rule['intent']=='urgent_help' else clause
                match = next((m for p in patterns if (m := p.search(match_clause))), None)
                if not match and last_contact_verb and rule['intent'] in ('doctor_contact', 'caregiver_contact'):
                    synthetic = f"{last_contact_verb} {match_clause}"
                    match = next((m for p in patterns if (m := p.search(synthetic))), None)
                if not match:
                    continue
                if re.search(r'\b(không|chưa|đừng)\b', match_clause[:match.start()]):
                    continue
                intent = rule['intent']
                if rule['context'] == 'dose' and not ctx.get('medication_occurrence_id'):
                    result['clarification'] = 'Hãy chọn đúng lượt thuốc đang được hỏi; tôi không thể đoán lượt thuốc.'
                    continue
                if rule['context'] == 'exercise' and not ctx.get('running'):
                    result['clarification'] = 'Bác muốn dừng thao tác nào?'
                    continue
                if intent in [a['intent'] for a in result['actions']]:
                    continue
                if intent in ('doctor_contact', 'caregiver_contact'):
                    v_m = re.search(r'\b(gọi|báo|nhờ|nhắn|gặp|liên hệ|cần)\b', match_clause)
                    if v_m:
                        last_contact_verb = v_m.group()
                parameters = {}
                if intent=='medication_snooze':
                    for alias, minutes in self.config['snooze_duration_aliases'].items():
                        if re.search(r'\b'+re.escape(alias)+r'\b',clause):
                            parameters['snooze_minutes'] = minutes
                result['actions'].append(dict(parameters=parameters,intent=intent, priority='high' if intent == 'urgent_help' else 'normal',
                                              needs_confirmation=rule['confirmation'], roles=rule['roles'],
                                              rule_id=rule['id'], matched_text=match.group(), context_refs=result['context_refs']))
                result['rule_ids'].append(rule['id'])
        actions = result['actions']
        if len(actions)>1:
            actions[:] = [a for a in actions if a['intent'] not in ('confirm','cancel')]
        if any(a['intent'] in ('medication_discomfort','rehab_discomfort') for a in actions):
            actions[:] = [a for a in actions if a['intent']!='health_report']
        if any(a['intent']=='rehab_start' for a in actions) and any(a['intent']=='rehab_stop' for a in actions):
            actions[:] = []
            result['clarification'] = 'Bác muốn bắt đầu hay dừng bài tập?'
        if any(a['intent'] == 'rehab_discomfort' for a in actions) and ctx.get('running') and not any(a['intent']=='rehab_stop' for a in actions):
            actions.insert(0, dict(intent='rehab_stop', priority='normal', needs_confirmation=False,
                                  roles=[], rule_id='discomfort_stop', matched_text=normalized, context_refs=result['context_refs']))
        if any(a['intent'] == 'urgent_help' for a in actions):
            actions[:] = [a for a in actions if a['intent'] in ('urgent_help', 'rehab_stop')]
        else:
            contacts = [a for a in actions if a['intent'] in ('doctor_contact', 'caregiver_contact')]
            if contacts:
                # Explicit recipient wins over inferred symptom routing.
                actions[:] = [a for a in actions if a['intent'] not in ('health_report','medication_discomfort','rehab_discomfort','ambiguous_help','companionship')]
            if any(a['intent'] == 'medication_taken' for a in actions) and any(a['intent'] == 'medication_not_taken' for a in actions):
                actions[:] = []
                result['clarification'] = 'Bác đã uống hay chưa uống lượt thuốc đang hỏi?'
        actions.sort(key=lambda a: 0 if a['intent'] == 'rehab_stop' else 1)
        if '?' in text and any(a['intent'] in ('urgent_help','health_report','medication_taken') for a in actions):
            actions[:] = []
            result['clarification'] = 'Bác đang báo tình trạng hiện tại hay đang hỏi về tình huống này?'
        if actions:
            result['match_level'] = 'strong'
        elif result['clarification']:
            result['match_level'] = 'ambiguous'
        return result
