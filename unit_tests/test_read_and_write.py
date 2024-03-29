# -*- coding: utf-8 -*-
"""
test_read_and_write.py

test writing and reading back 
"""
from collections import namedtuple
import hashlib
import os
import os.path
import shutil
import unittest
import zlib

import psycopg2

from tools.standard_logging import initialize_logging
from tools.database_connection import get_node_local_connection

from web_server.local_database_util import most_recent_timestamp_for_key
from web_server.data_slicer import DataSlicer
from web_server.zfec_segmenter import ZfecSegmenter
from data_writer.output_value_file import OutputValueFile, \
        value_file_template
from data_writer.writer import Writer
from data_reader.reader import Reader
from tools.data_definitions import create_timestamp, \
        random_string

_log_path = "%s/test_read_and_write.log" % (os.environ["NIMBUSIO_LOG_DIR"], )
_test_dir = os.path.join("/tmp", "test_read_and_write")
_repository_path = os.path.join(_test_dir, "nimbusio")
_local_node_name = os.environ["NIMBUSIO_NODE_NAME"]

def _retrieve_value_file_row(connection, value_file_id):
    result = connection.fetch_one_row("""
        select %s from nimbusio_node.value_file 
        where id = %%s
    """ % (",".join(value_file_template._fields), ), [value_file_id, ])
    return value_file_template._make(result)

class TestReadAndWrite(unittest.TestCase):
    """test writing and reading back"""

    def setUp(self):
        self.tearDown()
        os.makedirs(_test_dir)

        self._database_connection = get_node_local_connection()

    def tearDown(self):
        if hasattr(self, "_database_connection") \
        and self._database_connection is not None:
            self._database_connection.close()
            self._database_connection = None

        if os.path.exists(_test_dir):
            shutil.rmtree(_test_dir)

    def test_simple_output_value_file(self):
        """test writing a simple output value file"""
        collection_id = 1001
        segment_id = 42
        data_size = 1024
        data = random_string(data_size)
        output_value_file = OutputValueFile(
            self._database_connection, _repository_path
        )
        self.assertEqual(output_value_file.size, 0)
        output_value_file.write_data_for_one_sequence(
            collection_id, segment_id, data
        )
        self.assertEqual(output_value_file.size, data_size)
        output_value_file.close()
        
        value_file_row = _retrieve_value_file_row(
            self._database_connection, output_value_file._value_file_id
        )

        self.assertEqual(value_file_row.size, data_size)
        data_md5_hash = hashlib.md5(data).digest()
        self.assertEqual(str(value_file_row.hash), data_md5_hash)
        self.assertEqual(value_file_row.sequence_count, 1)
        self.assertEqual(value_file_row.min_segment_id, segment_id)
        self.assertEqual(value_file_row.max_segment_id, segment_id)
        self.assertEqual(value_file_row.distinct_collection_count, 1)
        self.assertEqual(value_file_row.collection_ids, [collection_id, ])

    def test_simple_segment(self):
        """test writing an reading a simple segment of one sequence"""
        collection_id = 1001
        key = "aaa/bbb/ccc"
        timestamp = create_timestamp()
        segment_num = 42
        sequence_num = 0
        data_size = 1024
        data = random_string(data_size)
        data_adler32 = zlib.adler32(data)
        data_md5 = hashlib.md5(data)
        file_tombstone = False
 
        writer = Writer(self._database_connection, _repository_path)

        # clean out any segments that are laying around for this (test) keu
        reader = Reader(self._database_connection, _repository_path)

        writer.start_new_segment(collection_id, key, repr(timestamp), segment_num)
        writer.store_sequence(
            collection_id, key, repr(timestamp), segment_num, sequence_num, data
        )
        writer.finish_new_segment(
            collection_id, 
            key, 
            repr(timestamp), 
            segment_num,
            data_size,
            data_adler32,
            data_md5.digest(),
            file_tombstone,
            handoff_node_id=None,
        )
        writer.close()

        file_info = most_recent_timestamp_for_key(
            self._database_connection, collection_id, key
        )

        self.assertEqual(file_info.file_size, data_size) 
        self.assertEqual(file_info.file_adler32, data_adler32) 
        self.assertEqual(str(file_info.file_hash), data_md5.digest()) 
        self.assertEqual(file_info.file_tombstone, file_tombstone) 

        reader = Reader(self._database_connection, _repository_path)
        sequence_generator = reader.generate_all_sequence_rows_for_segment(
            collection_id, key, file_info.timestamp, file_info.segment_num
        )

        # first yield should be a count
        sequence_count = sequence_generator.next()
        self.assertEqual(sequence_count, 1) 

        sequence_data = sequence_generator.next()
        self.assertEqual(len(sequence_data), len(data))
        self.assertEqual(sequence_data, data)

if __name__ == "__main__":
    initialize_logging(_log_path)
    unittest.main()

