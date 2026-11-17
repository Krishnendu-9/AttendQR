"""Initials in avatars and name circles: titles and a leading role word in an email are not initials."""
import re

import pytest

from attendqr.access import avatar_initials
from attendqr.extensions import db
from attendqr.models import Teacher
from conftest import make_account


@pytest.mark.parametrize(('name', 'from_email', 'expected'), [
    ('Dr. Manas Ghosh', False, 'MG'),
    ('Prof. Asha Rao', False, 'AR'),
    ('Smt. Ritika Sharma', False, 'RS'),
    ('Aditi Kundu', False, 'AK'),
    ('Chocolate', False, 'C'),
    ('hod.partha.sarkar', True, 'PS'),   # the role word starting the email is not a name
    ('teacher.ravi', True, 'R'),
    ('principal', True, 'P'),            # nothing else to go on: keep it
    ('hod.partha.sarkar', False, 'HP'),  # only email names drop a leading role word
    ('Dr.', False, 'D'),
    ('', False, '?'),
])
def test_avatar_initials(name, from_email, expected):
    assert avatar_initials(name, from_email=from_email) == expected


def avatar(html):
    return re.search(r'<span class="avatar">([^<]*)</span>', html).group(1)


def test_a_teacher_called_doctor_gets_their_own_initials(data, client_for):
    db.session.get(Teacher, data.teacher_id).name = 'Dr. Manas Ghosh'
    db.session.commit()
    assert avatar(client_for(data.teacher).get('/').get_data(as_text=True)) == 'MG'


def test_an_hod_named_by_their_email_skips_the_role_word(data, client_for):
    make_account('hod.partha.sarkar@rcciit.org.in', 'hod', department_id=data.mca_id)
    db.session.commit()
    assert avatar(client_for('hod.partha.sarkar@rcciit.org.in').get('/').get_data(as_text=True)) == 'PS'


def test_the_teachers_list_uses_the_same_initials(data, client_for):
    db.session.get(Teacher, data.teacher_id).name = 'Dr. Manas Ghosh'
    db.session.commit()
    html = client_for(data.hod).get('/teachers').get_data(as_text=True)
    assert '<span class="monogram " aria-hidden="true">MG</span>' in html
