# -*- coding: utf-8 -*-
"""
destroyer.py

A class that performs a destroy query on all data writers.
"""
import logging
import os

import gevent
import gevent.pool

from web_server.exceptions import (
    AlreadyInProgress,
    DestroyFailedError,
)

from web_server.local_database_util import current_status_of_key, \
        current_status_of_version

_local_node_name = os.environ["NIMBUSIO_NODE_NAME"]

class Destroyer(object):
    """Performs a destroy query on all data writers."""
    def __init__(
        self, 
        node_local_connection,
        data_writers,
        collection_id, 
        key,
        unified_id_to_delete,
        unified_id,
        timestamp        
    ):
        self.log = logging.getLogger('Destroyer')
        self.log.info('collection_id=%d, key=%r' % (collection_id, key, ))
        self._node_local_connection = node_local_connection
        self.data_writers = data_writers
        self.collection_id = collection_id
        self.key = key
        self.unified_id_to_delete = unified_id_to_delete
        self._unified_id = unified_id
        self.timestamp = timestamp
        self._pending = gevent.pool.Group()
        self._done = []

    def _join(self, timeout):
        self._pending.join(timeout, True)
        # make sure _done_link gets run first by cooperating
        gevent.sleep(0)
        if not self._pending:
            return
        raise DestroyFailedError()

    def _done_link(self, task):
        if isinstance(task.value, gevent.GreenletExit):
            return
        self._done.append(task)

    def _spawn(self, run, *args):
        task = self._pending.spawn(run, *args)
        task.rawlink(self._done_link)
        return task

    def destroy(self, timeout=None):
        if self._pending:
            raise AlreadyInProgress()

        # TODO: find a non-blocking way to do this
        if self.unified_id_to_delete is None:
            status_rows = current_status_of_key(
                self._node_local_connection, 
                self.collection_id,
                self.key
            )
        else:
            status_rows = current_status_of_version(
                self._node_local_connection, 
                self.unified_id_to_delete
            )

        if len(status_rows) == 0:
            raise DestroyFailedError("no status rows found")

        file_size = sum([row.seg_file_size for row in status_rows])

        for i, data_writer in enumerate(self.data_writers):
            segment_num = i + 1
            self._spawn(
                data_writer.destroy_key,
                self.collection_id,
                self.key,
                self.unified_id_to_delete,
                self._unified_id,
                self.timestamp,
                segment_num,
                _local_node_name,
            )
        self._join(timeout)
        self._done = []

        return file_size

