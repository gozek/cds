# -*- coding: utf-8 -*-
#
# This file is part of CERN Document Server.
# Copyright (C) 2017 CERN.
#
# CERN Document Server is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# CERN Document Server is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CERN Document Server; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""Migration CLI."""

from __future__ import absolute_import, print_function

from invenio_db import db
from invenio_migrator.cli import dumps
from invenio_pidstore.models import PersistentIdentifier
from invenio_sequencegenerator.api import Sequence
from datetime import datetime
from flask.cli import with_appcontext

from ...modules.deposit.api import Project


@dumps.command()
@with_appcontext
def sequence_generator():
    """Update sequences according to current report numbers in pidstore."""
    def get_pids(year):
        """Get project and video pids registered this year."""
        query = PersistentIdentifier.query.filter(
            PersistentIdentifier.pid_value.contains('-{0}-'.format(year)))
        pids = [pid.pid_value for pid in query.all()]
        videos = [pid for pid in pids if pid.count('-') == 4]
        projects = [pid for pid in pids if pid.count('-') == 3]
        # check no pids are lost
        assert sorted(pids) == sorted(projects + videos)
        return projects, videos

    def get_cats_types(projects, videos):
        """Get category/type list for projects and videos."""
        cats_types = set(
            ["-".join(pid.split('-')[0:2]) for pid in projects])
        cats_types_video = set(
            ["-".join(pid.split('-')[0:2]) for pid in videos])
        # check category/type list are the same
        assert all([ct in cats_types for ct in cats_types_video])
        return cats_types

    def find_next(cat_type, pids):
        max_count = max([
            int(pid.split('-')[-1])
            for pid in pids if pid.startswith(cat_type)])
        return max_count + 1

    def update_counter(next_counter, **sequence_kwargs):
        """Update sequence counter."""
        counter = Sequence(**sequence_kwargs).counter
        counter.counter = next_counter
        db.session.add(counter)
        return counter

    year = datetime.now().year
    project_pids, video_pids = get_pids(year=year)
    cats_types = get_cats_types(project_pids, video_pids)

    for cat_type in cats_types:
        [cat, type_] = cat_type.split('-')
        update_counter(
            next_counter=find_next(cat_type, project_pids), **{
                'template': Project.sequence_name, 'year': year,
                'category': cat, 'type': type_
            })

    db.session.commit()
