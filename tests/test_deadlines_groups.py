from datetime import date
import pytest
from capa_map.application.deadlines import timing
from capa_map.application.grouping import THEMES, parent_theme, suggested_group


def fields(**updates):
    return {'status': 'Closed - Done', 'is_closed': '1.0', 'date_opened': '2026-01-01',
            'date_to_close': '2026-01-10', 'date_closed': '2026-01-10', **updates}


@pytest.mark.parametrize(('updates', 'expected'), [
    ({}, 'closed_on_time'),
    ({'date_closed': '2026-01-10T23:59:59'}, 'closed_on_time'),
    ({'date_closed': '2026-01-11'}, 'closed_late'),
    ({'status': 'Open', 'is_closed': '0', 'date_closed': ''}, 'open_overdue'),
    ({'status': 'Open', 'is_closed': '0', 'date_closed': '', 'date_to_close': '2026-01-20'}, 'open_within_due'),
    ({'status': 'Closed - Cancelled'}, 'cancelled'),
    ({'status': 'Closed - Canceled', 'date_to_close': ''}, 'cancelled'),
    ({'date_to_close': ''}, 'unavailable'),
    ({'date_to_close': '2026-02-30'}, 'unavailable'),
    ({'date_closed': '2025-12-31'}, 'unavailable'),
    ({'date_to_close': '2025-12-31'}, 'unavailable'),
    ({'date_closed': '2026-01-21'}, 'unavailable'),
    ({'date_closed': ''}, 'unavailable'),
    ({'is_closed': '0'}, 'unavailable'),
    ({'status': 'Open', 'is_closed': '1'}, 'unavailable'),
    ({'status': 'Not provided', 'is_closed': ''}, 'unavailable'),
])
def test_recorded_deadlines(updates, expected):
    assert timing(fields(**updates), date(2026, 1, 20))['dueStatus'] == expected


def test_unavailable_dates_are_null_and_no_default_sla_is_invented():
    assert timing({}, date(2026, 1, 20)) == {'dueDate': None, 'closedDate': None, 'dueStatus': 'unavailable'}
    result = timing(fields(date_to_close='', due_date='2026-01-12'), date(2026, 1, 20))
    assert result['dueDate'] == '2026-01-12' and result['dueStatus'] == 'closed_on_time'


def test_cluster_groups_are_bounded_deterministic_and_use_saved_analysis_context():
    assert len(THEMES) == len(set(THEMES)) == 12
    assert parent_theme('HPLC system suitability', []) == 'Analytical testing'
    assert parent_theme('Packaging label mix-up', []) == 'Packaging and labelling'
    assert parent_theme('No recognized title', []) == 'Other / needs classification'
    assert parent_theme('No recognized title', [{'drType': 'Product Complaint'}]) == 'Product complaints and adverse events'
    cluster = {'title': 'HPLC system suitability', 'analysisContext': {'title': 'Packaging label mix-up'}}
    before = repr(cluster)
    assert suggested_group(cluster, []) == 'Packaging and labelling'
    assert repr(cluster) == before
