"""Counts shown in small spaces: in full below a million, shortened from there, never overstated."""
import pytest

from attendqr.utils import compact_number, full_number


@pytest.mark.parametrize(('value', 'expected'), [
    (0, '0'),
    (7, '7'),
    (48_250, '48,250'),
    (999_999, '999,999'),       # the largest number shown in full
    (1_000_000, '1M'),
    (1_200_000, '1.2M'),
    (6_000_000, '6M'),          # no trailing ".0"
    (1_999_999, '1.9M'),        # rounded down: never more than there is
    (9_999_999, '9.9M'),
    (10_000_000, '10M'),        # from 10 of a unit on, whole numbers
    (999_999_999, '999M'),      # not "1000M"
    (1_000_000_000, '1B'),
    (3_450_000_000, '3.4B'),
    (2_000_000_000_000, '2T'),
    (-2_500_000, '-2.5M'),
])
def test_compact_number(value, expected):
    assert compact_number(value) == expected


def test_full_number_adds_separators():
    assert full_number(6_000_000) == '6,000,000'
    assert full_number(42) == '42'


@pytest.mark.parametrize('value', [None, '12', 1.5, True])
def test_anything_but_a_whole_number_is_left_alone(value):
    assert compact_number(value) == value
    assert full_number(value) == value


def test_the_filters_are_available_in_templates(app):
    rendered = app.jinja_env.from_string('{{ 6000000|compact_number }} / {{ 6000000|full_number }}').render()
    assert rendered == '6M / 6,000,000'
