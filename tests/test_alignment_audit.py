import copy

import pytest
from pydantic import ValidationError

from alignment_audit import AlignmentReview, audit, evidence_package, reconcile
from tasaa import analyze


def evidence():
    return evidence_package({'record': 1, 'status': 'ambiguous', 'issues': [], 'candidates': [
        {'id': 'a', 'certified': False, 'fields': [{'id': 'f1', 'hard_valid': True, 'start': 1, 'end': 2}],
         'byte_ownership': [{'start': 1, 'end': 2, 'kind': 'field'}]},
        {'id': 'b', 'certified': False, 'fields': [{'id': 'f2', 'hard_valid': False, 'start': 1, 'end': 2}],
         'byte_ownership': [{'start': 1, 'end': 2, 'kind': 'field'}]}]})


def review(e, **changes):
    data = {'evidence_id': e['evidence_id'], 'status': 'uncertain', 'summary': 'Boundary is uncertain',
            'preferred_candidate_ids': [], 'findings': [], 'contradictions': [],
            'spec_changes': [], 'rule_proposals': []}
    return AlignmentReview.model_validate({**data, **changes})


def test_missing_or_oversized_evidence_fails_closed():
    with pytest.raises(ValueError, match='required'):
        evidence_package({})
    with pytest.raises(ValueError, match='exceeds'):
        evidence_package({'candidates': [{'id': 'a', 'fields': [{'id': 'f1', 'hard_valid': True}],
                                          'raw': 'x' * 30000}]})
    with pytest.raises(ValueError, match='field evidence'):
        evidence_package({'candidates': [{'id': 'a'}]})


def test_unknown_candidate_and_field_ids_rejected():
    e = evidence()
    with pytest.raises(ValueError, match='candidate outside'):
        reconcile(e, review(e, preferred_candidate_ids=['ghost']))
    with pytest.raises(ValueError, match='field outside'):
        reconcile(e, review(e, findings=[{'candidate_id': 'a', 'field_id': 'f2', 'concern': 'wrong'}]))
    with pytest.raises(ValueError, match='field outside'):
        reconcile(e, review(e, spec_changes=[{'field_id': 'ghost', 'attribute': 'length',
                                               'proposed_value': '2', 'reason': 'observed'}]))


def test_stale_evidence_and_unsupported_rule_rejected():
    e = evidence()
    with pytest.raises(ValueError, match='stale'):
        reconcile(e, review(e, evidence_id='wrong'))
    with pytest.raises(ValidationError):
        review(e, rule_proposals=[{'field_id': 'f1', 'kind': 'execute_sql', 'value': 'DROP TABLE x',
                                   'reason': 'bad'}])


def test_disagreement_and_hard_failure_require_review_without_byte_change():
    e = evidence()
    result = reconcile(e, review(e, status='agree', preferred_candidate_ids=['b']))
    assert result['rejected_preferences'] == ['b']
    assert result['human_review_required']
    assert result['certified_candidate_ids'] == []
    assert result['bytes_modified'] is False


def test_provider_receives_typed_evidence_and_cannot_mutate_it():
    source = {'record': 1, 'status': 'ambiguous', 'issues': [], 'candidates': [
        {'id': 'a', 'fields': [{'id': 'f1', 'hard_valid': False}]}]}
    original = copy.deepcopy(source)

    class Provider:
        def invoke_structured(self, capability, context, response_model, instructions, scope):
            assert capability == 'studio_alignment'
            assert response_model is AlignmentReview
            assert context['candidates'] == source['candidates']
            assert scope == 'project/spec-hash/record-1'
            return {'result': review(context).model_dump(), 'usage': {}}

    run, result = audit(Provider(), source, 'project/spec-hash/record-1')
    assert run['result']['evidence_id'] == result['evidence_id']
    assert result['human_review_required']
    assert source == original


def test_actual_tasaa_result_is_auditable_without_record_bytes():
    spec = {'fields': [{'id': 'f1', 'field_name': 'Name', 'rules': {
        'start': 1, 'end': 3, 'length': 3, 'datatype': 'char', 'conversion': 'identity', 'required': True,
        'padding': {'side': 'right', 'char': ' '}}}]}
    layout = {'field_ids': ['f1'], 'record_length': 3}
    result = analyze(spec, layout, b'A  ')
    package = evidence_package(result)
    assert package['status'] == 'already_valid'
    assert package['candidates'][0]['id'] == 'initial'
    assert 'candidate' not in package
    assert reconcile(package, review(package, status='agree', preferred_candidate_ids=['initial']))[
        'human_review_required'] is False


def test_conflicts_and_uncertainty_reach_ai_without_granting_repair_authority():
    source = {'status': 'needs_review', 'structural_status': 'AMBIGUOUS',
              'validation_status': 'SPEC_CONFLICT', 'spec_conflicts': [{'field_id': 'f1', 'code': 'TYPE_MISMATCH'}],
              'anchor_evidence': [{'fieldId': 'f1', 'sourceStart': 0, 'sourceEnd': 2}],
              'ambiguity_regions': [{'start': 0, 'end': 2}], 'resource_limit_reached': True,
              'candidates': [{'id': 'a', 'fields': [{'id': 'f1', 'hard_valid': True}], 'certified': False}]}
    package = evidence_package(source)
    assert package['spec_conflicts'] == source['spec_conflicts']
    assert package['anchor_evidence'] == source['anchor_evidence']
    assert package['ambiguity_regions'] == source['ambiguity_regions']
    result = reconcile(package, review(package, status='agree', preferred_candidate_ids=['a']))
    assert result['human_review_required'] and result['bytes_modified'] is False


def test_large_evidence_has_explicit_partial_scope_and_requires_review():
    source = {'status': 'ambiguous', 'ledger': [{'offset': index, 'byte': 65} for index in range(4000)],
              'candidates': [{'id': 'a', 'fields': [{'id': 'f1', 'hard_valid': True}]}]}
    package = evidence_package(source)
    assert package['evidence_limited'] is True and not package['ledger']
    assert reconcile(package, review(package, status='agree'))['human_review_required']
