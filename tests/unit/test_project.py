# -*- coding: utf-8 -*-
#
# This file is part of CDS.
# Copyright (C) 2016, 2017 CERN.
#
# CDS is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# CDS is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CDS; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""Test project."""

from __future__ import absolute_import, print_function

import mock
import pytest
import uuid
import json

from invenio_db import db
from copy import deepcopy
from flask_security import login_user
from cds.modules.records.permissions import has_update_permission
from cds.modules.deposit.api import (record_build_url, Project, Video,
                                     video_resolver, video_build_url,
                                     is_deposit, record_unbuild_url,
                                     project_resolver)
from invenio_accounts.models import User
from invenio_pidstore.providers.recordid import RecordIdProvider
from invenio_pidstore.errors import PIDInvalidAction
from jsonschema.exceptions import ValidationError
from cds.modules.deposit.errors import DiscardConflict
from cds.modules.webhooks.status import get_deposit_events
from invenio_records.models import RecordMetadata
from invenio_webhooks.models import Event

from helpers import workflow_receiver_video_failing, \
    get_indexed_records_from_mock, prepare_videos_for_publish


def test_is_deposit():
    """Test is deposit function."""
    assert is_deposit('/api/deposit/1') is True
    assert is_deposit('/record/1') is False


def test_record_unbuild_url(deposit_rest):
    """Test record unbuild url."""
    assert '1' == record_unbuild_url('/deposits/video/1')
    assert '1' == record_unbuild_url('/record/1')


def test_record_build_url(deposit_rest):
    """Test record build url."""
    assert '/record/1' == record_build_url(1)


def test_video_build_url(deposit_rest):
    """Test deposit build url."""
    assert '/deposits/video/1' == video_build_url(1)
    assert '/deposits/video/1' == video_build_url('1')
    assert '/deposits/video/95b0716a-c726-4481-96fe-2aa02c72cd41' == \
        video_build_url(uuid.UUID('95b0716a-c726-4481-96fe-2aa02c72cd41'))


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
def test_publish_all_videos(app, project):
    """Test video publish."""
    (project, video_1, video_2) = project

    # check video1 is not published
    assert video_1['_deposit']['status'] == 'draft'
    assert video_2['_deposit']['status'] == 'draft'
    assert project['_deposit']['status'] == 'draft'
    # publish project
    prepare_videos_for_publish(video_1, video_2)
    new_project = project.publish()
    # check project and all video are published
    assert new_project['_deposit']['status'] == 'published'
    videos = video_resolver(new_project.video_ids)
    assert len(videos) == 2
    for video in videos:
        assert video['_deposit']['status'] == 'published'


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
def test_publish_one_video(app, project):
    """Test video publish."""
    (project, video_1, video_2) = project

    # check video1 is not published
    assert video_1['_deposit']['status'] == 'draft'
    assert video_2['_deposit']['status'] == 'draft'
    assert project['_deposit']['status'] == 'draft'
    # [publish project]
    prepare_videos_for_publish(video_1, video_2)
    # publish one video
    video_1 = video_1.publish()
    project = video_1.project
    # publish the project (with one video still not publish)
    project = project.publish()
    # check project and all video are published
    assert project['_deposit']['status'] == 'published'
    videos = video_resolver(project.video_ids)
    assert len(videos) == 2
    for video in videos:
        assert video['_deposit']['status'] == 'published'


def test_find_refs(project):
    """Test find refs."""
    (project, video_1, video_2) = project
    assert project._find_refs([video_1.ref]) == {0: video_1.ref}
    assert project._find_refs([video_2.ref]) == {1: video_2.ref}
    assert project._find_refs([video_1.ref, video_2.ref]) == \
        {0: video_1.ref, 1: video_2.ref}
    assert project._find_refs(['not-found']) == {}


def test_update_videos(project):
    """Test update videos."""
    (project, video_1, video_2) = project
    new_ref_2 = '/deposit/456'
    project._update_videos([video_2.ref], [new_ref_2])
    assert project['videos'] == [
        {'$reference': video_1.ref}, {'$reference': new_ref_2}]
    project._update_videos(['not-found'], ['ref-not-found'])
    assert project['videos'] == [
        {'$reference': video_1.ref}, {'$reference': new_ref_2}]


def test_delete_videos(project):
    """Test update videos."""
    (project, video_1, video_2) = project
    project._delete_videos([video_2.ref])
    assert project['videos'] == [{'$reference': video_1.ref}]
    project._delete_videos(['not-found'])
    assert project['videos'] == [{'$reference': video_1.ref}]


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
def test_add_video(app, es, cds_jsonresolver, users, location,
                   deposit_metadata):
    """Test add video."""
    project_data = {
        'title': {
            'title': 'my project',
        },
        'videos': [],
    }
    project_data.update(deposit_metadata)

    login_user(User.query.get(users[0]))

    # create empty project
    project = Project.create(project_data).commit()

    # check project <--/--> video
    assert project['videos'] == []

    # create video
    project_video_1 = {
        'title': {
            'title': 'video 1',
        },
        '_project_id': project['_deposit']['id'],
    }
    video_1 = Video.create(project_video_1)

    # check project <----> video
    assert project._find_refs([video_1.ref])
    assert video_1.project.id == project.id


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
def test_project_discard(app, project_published):
    """Test project discard."""
    (project, video_1, video_2) = project_published

    # try successfully to discard a project
    original_title = project['title']['title']
    new_title = 'modified project'
    project = project.edit()
    project['title']['title'] = 'modified project'
    assert project['title']['title'] == new_title
    project = project.discard()
    assert project['title']['title'] == original_title

    # try to fail because a video added
    project = project.edit()
    project_video = {
        'title': {
            'title': 'video 1',
        },
        '_project_id': project['_deposit']['id'],
    }
    Video.create(project_video)
    with pytest.raises(DiscardConflict):
        project.discard()


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
def test_project_edit(app, project_published):
    """Test project edit."""
    (project, video_1, video_2) = project_published
    assert project.status == 'published'
    assert video_1.status == 'published'
    assert video_2.status == 'published'

    # Edit project (change project title)
    new_project = project.edit()
    assert new_project.status == 'draft'
    new_project.update(title={'title': 'My project'})

    # Edit videos inside project (change video titles)
    videos = video_resolver(new_project.video_ids)
    assert len(videos) == 2
    for i, video in enumerate(videos):
        assert video.status == 'published'
        new_video = video.edit()
        assert new_video.status == 'draft'
        new_video.update(title={'title': 'Video {}'.format(i + 1)})
        new_video.publish()

    # Publish all changes
    new_project.publish()

    # Check that everything is published
    videos = video_resolver(new_project.video_ids)
    assert new_project.status == 'published'
    assert all(video.status == 'published' for video in videos)

    # Check that all titles where properly changed
    assert new_project['title']['title'] == 'My project'
    assert videos[0]['title']['title'] in ['Video 1', 'Video 2']
    assert videos[1]['title']['title'] in ['Video 1', 'Video 2']
    assert videos[0]['title']['title'] != videos[1]['title']['title']


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
@pytest.mark.parametrize('force', [False, True])
def test_project_delete_not_published(app, project, force):
    """Test project delete when all is not published."""
    (project, video_1, video_2) = project

    project_id = project.id
    video_1_id = video_1.id
    video_2_id = video_2.id

    assert project.status == 'draft'
    assert video_1.status == 'draft'
    assert video_2.status == 'draft'

    project = project.delete(force=force)

    reclist = RecordMetadata.query.filter(RecordMetadata.id.in_(
        [project_id, video_1_id, video_2_id])).all()

    if force:
        assert len(reclist) == 0
    else:
        assert len(reclist) == 3
        for rec in reclist:
            assert rec.json is None


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
@pytest.mark.parametrize('force', [False])  # , True])
def test_project_delete_one_video_published(app, project, force):
    """Test project delete when one video is published."""
    def check_project(number_of_videos, video_2_status, video_1_ref,
                      video_2_ref, project_id):
        video_1_meta = RecordMetadata.query.filter_by(id=video_1_id).first()
        video_2_meta = RecordMetadata.query.filter_by(id=video_2_id).first()
        project_meta = RecordMetadata.query.filter_by(id=project_id).first()

        assert video_1_meta.json is not None
        assert video_2_meta.json is not None

        assert {'$reference': video_1_ref} in project_meta.json['videos']
        assert {'$reference': video_2_ref} in project_meta.json['videos']
        assert len(project_meta.json['videos']) == number_of_videos

        assert project.status == 'draft'
        assert video_1.status == 'draft'
        assert video_2.status == video_2_status

    (project, video_1, video_2) = project

    # prepare videos for publishing
    prepare_videos_for_publish(video_1, video_2)

    # publish video_2
    video_2 = video_2.publish()

    project_id = project.id
    video_1_id = video_1.id
    video_2_id = video_2.id

    video_1_ref = video_1.ref
    video_2_ref = video_2.ref

    assert project.status == 'draft'
    assert video_1.status == 'draft'
    assert video_2.status == 'published'

    # you can't delete because there is a video published!
    with pytest.raises(PIDInvalidAction):
        project.delete(force=force)

    check_project(2, 'published', video_1_ref, video_2_ref, project_id)

    # edit video_2
    video_2 = video_2.edit()
    video_2_id = video_2.id
    project_id = video_2.project.id
    video_1_ref = video_1.ref
    video_2_ref = video_2.ref

    # you can't delete because video_2 was previously published
    with pytest.raises(PIDInvalidAction):
        project.delete(force=force)

    check_project(2, 'draft', video_1_ref, video_2_ref, project_id)

    # discard video_2
    video_2 = video_2.discard()
    video_2_id = video_2.id
    project_id = video_2.project.id
    video_2_ref = video_2.ref

    # you can't delete because there is a video published!
    with pytest.raises(PIDInvalidAction):
        project.delete(force=force)

    check_project(2, 'published', video_1_ref, video_2_ref, project_id)

    # TODO delete video_2
    #  video_2.delete(force=force)
    #  project_id = video_1.project.id

    #  project.delete(force=force)


def test_inheritance(app, project):
    """Test that videos inherit the proper fields from parent project."""
    (project, video, _) = project
    assert 'category' in project
    assert 'type' in project

    # Publish the video
    prepare_videos_for_publish(video)
    video = video.publish()
    assert 'category' in video
    assert 'type' in video
    assert video['category'] == project['category']
    assert video['type'] == project['type']


def test_project_partial_validation(
        app, db, cds_jsonresolver_required_fields, deposit_metadata, location,
        deposit_rest, video_deposit_metadata, project_deposit_metadata):
    """Test project create/publish with partial validation/validation."""
    # create a project/video without a required field
    if 'fuu' in deposit_metadata:
        del deposit_metadata['fuu']
    project_video_1 = deepcopy(video_deposit_metadata)
    # project_video_1 = deepcopy(video_metadata)
    project = Project.create(project_deposit_metadata)
    # insert a video
    project_video_1['_project_id'] = project['_deposit']['id']
    video = Video.create(project_video_1)
    prepare_videos_for_publish(video)
    video_id = video.id
    # check project
    id_ = project.id
    db.session.expire_all()
    project = Project.get_record(id_)
    assert project is not None
    # if publish, then generate an validation error
    with pytest.raises(ValidationError):
        project.publish()
    # patch project
    patch = [{
        'op': 'replace',
        'path': '/category',
        'value': 'bar',
    }]
    id_ = project.id
    db.session.expire_all()
    project = Project.get_record(id_)
    project.patch(patch).commit()
    # update project
    copy = deepcopy(project)
    copy['category'] = 'qwerty'
    id_ = project.id
    db.session.expire_all()
    project = Project.get_record(id_)
    project.update(copy)
    project.commit()
    # patch video
    patch = [{
        'op': 'replace',
        'path': '/category',
        'value': 'bar',
    }]
    id_ = video_id
    db.session.expire_all()
    video = Video.get_record(id_)
    video.patch(patch).commit()
    # update video
    copy = deepcopy(video)
    copy['category'] = 'qwerty'
    id_ = video_id
    db.session.expire_all()
    video = Video.get_record(id_)
    video.update(copy)
    video.commit()


@mock.patch('cds.modules.records.providers.CDSRecordIdProvider.create',
            RecordIdProvider.create)
def test_project_publish_with_workflow(app, users, project, webhooks, es):
    """Test publish a project with a workflow."""
    project, video_1, video_2 = project
    prepare_videos_for_publish(video_1, video_2)
    project_depid = project['_deposit']['id']
    project_id = str(project.id)
    video_1_depid = video_1['_deposit']['id']
    video_1_id = str(video_1.id)
    video_2_depid = video_2['_deposit']['id']

    sse_channel = 'mychannel'
    receiver_id = 'test_project_publish_with_workflow'
    workflow_receiver_video_failing(
        app, db, video_1, receiver_id=receiver_id, sse_channel=sse_channel)

    headers = [('Content-Type', 'application/json')]
    payload = json.dumps(dict(somekey='somevalue'))
    with mock.patch('invenio_sse.ext._SSEState.publish') as mock_sse, \
            mock.patch('invenio_indexer.api.RecordIndexer.bulk_index') \
            as mock_indexer, \
            app.test_request_context(headers=headers, data=payload):
        event = Event.create(receiver_id=receiver_id)
        db.session.add(event)
        event.process()

        # check messages are sent to the sse channel
        assert mock_sse.called is True
        args = list(mock_sse.mock_calls[0])[2]
        assert args['channel'] == sse_channel
        assert args['type_'] == 'update_deposit'
        assert args['data']['meta']['payload']['deposit_id'] == video_1_depid
        args = list(mock_sse.mock_calls[1])[2]
        assert args['channel'] == sse_channel
        assert args['type_'] == 'update_deposit'
        assert args['data']['meta']['payload']['deposit_id'] == project_depid

        # check video and project are indexed
        assert mock_indexer.called is True
        ids = get_indexed_records_from_mock(mock_indexer)
        assert video_1_id == ids[0]
        assert project_id == ids[1]
    db.session.commit()

    # check tasks status is propagated to video and project
    video_1 = video_resolver([video_1_depid])[0]
    expected = {u'add': u'SUCCESS', u'failing': u'FAILURE'}
    assert video_1['_deposit']['state'] == expected
    assert video_1.project['_deposit']['state'] == expected

    events = get_deposit_events(deposit_id=video_1_depid)
    assert len(events) == 1

    assert project.status == 'draft'
    assert video_1.status == 'draft'
    assert video_2.status == 'draft'

    video_2 = video_resolver([video_2_depid])[0]
    video_2.publish()

    assert project.status == 'draft'
    assert video_1.status == 'draft'
    assert video_2.status == 'published'

    video_1 = video_resolver([video_1_depid])[0]
    with pytest.raises(PIDInvalidAction):
        video_1.publish()

    assert project.status == 'draft'
    assert video_1.status == 'draft'
    assert video_2.status == 'published'

    with pytest.raises(PIDInvalidAction):
        project.publish()

    assert project.status == 'draft'
    assert video_1.status == 'draft'
    assert video_2.status == 'published'


def test_project_record_schema(app, db, project):
    """Test project record schema."""
    (project, video_1, video_2) = project
    assert project.record_schema == Project.get_record_schema()


def test_project_deposit(es, location, deposit_metadata):
    """Test CDS deposit creation."""
    deposit = Project.create(deposit_metadata)
    id_ = deposit.id
    db.session.expire_all()
    deposit = Project.get_record(id_)
    assert deposit['_deposit']['state'] == {}
    assert project_resolver(deposit['_deposit']['id']) is not None
    assert '_buckets' in deposit


def test_project_permissions(es, location, deposit_metadata, users):
    """Test deposit permissions."""
    deposit = Project.create(deposit_metadata)
    deposit.commit()
    user = User.query.get(users[0])
    login_user(user)
    assert not has_update_permission(user, deposit)
    deposit['_deposit']['owners'].append(user.id)
    assert has_update_permission(user, deposit)


def test_deposit_partial_validation(
        api_app, db, api_cds_jsonresolver_required_fields, deposit_metadata,
        location, video_deposit_metadata):
    """Test project create/publish with partial validation/validation."""
    video_1 = deepcopy(video_deposit_metadata)
    # create a deposit without a required field
    if 'fuu' in deposit_metadata:
        del deposit_metadata['fuu']
    with api_app.test_request_context():
        project = Project.create(deposit_metadata)
        video_1['_project_id'] = project['_deposit']['id']
        video_1 = Video.create(video_1)
        prepare_videos_for_publish(video_1)
        video_1.commit()
        id_ = project.id
        db.session.expire_all()
        project = Project.get_record(id_)
        assert project is not None
        # if publish, then generate an validation error
        with pytest.raises(ValidationError):
            project.publish()
        # patch project
        patch = [{
            'op': 'replace',
            'path': '/category',
            'value': 'bar',
        }]
        id_ = project.id
        db.session.expire_all()
        project = Project.get_record(id_)
        project.patch(patch).commit()
        # update project
        copy = deepcopy(project)
        copy['category'] = 'qwerty'
        id_ = project.id
        db.session.expire_all()
        project = Project.get_record(id_)
        project.update(copy)
        project.commit()
