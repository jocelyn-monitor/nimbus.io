# -*- coding: utf-8 -*-
"""
forwarder_coroutine.py

a coroutine that handles message traffic for retrieving and
re-archiving a segment that was handed off to us
"""
from base64 import b64encode
import logging
import uuid

from tools.data_definitions import create_priority

def forwarder_coroutine(
    node_name_dict, 
    segment_row, 
    source_node_names, 
    writer_client, 
    reader_client
):
    """
    manage the message traffic for retrieving and re-archiving 
    a segment that was handed off to us
    """
    log = logging.getLogger("forwarder_coroutine")
    archive_priority = create_priority()

    # start retrieving from our reader
    message_id = uuid.uuid1().hex
    message = {
        "message-type"      : "retrieve-key-start",
        "message-id"        : message_id,
        "segment-unified-id": segment_row.unified_id,
        "segment-num"       : segment_row.segment_num,
    }

    log.debug("sending retrieve-key-start %s %s" % (
        segment_row.unified_id, 
        segment_row.segment_num
    ))
    
    reader_client.queue_message_for_send(message, data=None)
    reply, data = yield

    assert reply["message-type"] == "retrieve-key-reply", reply
    assert reply["result"] == "success", reply
    completed = reply["completed"]

    sequence = 1

    message_id = uuid.uuid1().hex
    if completed:
        message = {
            "message-type"      : "archive-key-entire",
            "message-id"        : message_id,
            "priority"          : archive_priority,
            "collection-id"     : segment_row.collection_id,
            "key"               : segment_row.key, 
            "unified-id"        : segment_row.unified_id,
            "conjoined-part"    : segment_row.conjoined_part,
            "timestamp-repr"    : repr(segment_row.timestamp),
            "segment-num"       : segment_row.segment_num,
            "segment-size"      : reply["segment-size"],
            "zfec-padding-size" : reply["zfec-padding-size"],
            "segment-adler32"   : reply["segment-adler32"],
            "segment-md5-digest": reply["segment-md5-digest"],
            "file-size"         : segment_row.file_size,
            "file-adler32"      : segment_row.file_adler32,
            "file-hash"         : b64encode(segment_row.file_hash),
            "source-node-name"  : node_name_dict[segment_row.source_node_id],
            "handoff-node-name" : None,
        }
    else:
        message = {
            "message-type"      : "archive-key-start",
            "message-id"        : message_id,
            "priority"          : archive_priority,
            "collection-id"     : segment_row.collection_id,
            "key"               : segment_row.key, 
            "unified-id"        : segment_row.unified_id,
            "conjoined-part"    : segment_row.conjoined_part,
            "timestamp-repr"    : repr(segment_row.timestamp),
            "segment-num"       : segment_row.segment_num,
            "segment-size"      : reply["segment-size"],
            "zfec-padding-size" : reply["zfec-padding-size"],
            "segment-adler32"   : reply["segment-adler32"],
            "segment-md5-digest": reply["segment-md5-digest"],
            "sequence-num"      : sequence,
            "source-node-name"  : node_name_dict[segment_row.source_node_id],
            "handoff-node-name" : None,
        }
            
    writer_client.queue_message_for_send(message, data=data)
    reply = yield

    if completed:
        # we give back the segment_row and source node names as our last yield
        yield (segment_row, source_node_names, )
        return 

    assert reply["message-type"] == "archive-key-start-reply", reply
    assert reply["result"] == "success", reply

    # send the intermediate segments
    while not completed:
        sequence += 1

        message_id = uuid.uuid1().hex
        message = {
            "message-type"      : "retrieve-key-next",
            "message-id"        : message_id,
            "segment-unified-id": segment_row.unified_id,
            "segment-num"       : segment_row.segment_num,
        }
        reader_client.queue_message_for_send(message, data=None)
        reply, data = yield
        assert reply["message-type"] == "retrieve-key-reply", reply
        assert reply["result"] == "success", reply
        completed = reply["completed"]

        message_id = uuid.uuid1().hex
        if completed:
            message = {
                "message-type"      : "archive-key-final",
                "message-id"        : message_id,
                "priority"          : archive_priority,
                "collection-id"     : segment_row.collection_id,
                "key"               : segment_row.key,
                "unified-id"        : segment_row.unified_id,
                "conjoined-part"    : segment_row.conjoined_part,
                "timestamp-repr"    : repr(segment_row.timestamp),
                "segment-num"       : segment_row.segment_num,
                "segment-size"      : reply["segment-size"],
                "zfec-padding-size" : reply["zfec-padding-size"],
                "segment-adler32"   : reply["segment-adler32"],
                "segment-md5-digest": reply["segment-md5-digest"],
                "sequence-num"      : sequence,
                "file-size"         : segment_row.file_size,
                "file-adler32"      : segment_row.file_adler32,
                "file-hash"         : b64encode(segment_row.file_hash),
                "source-node-name"  : node_name_dict[
                    segment_row.source_node_id],
                "handoff-node-name" : None,
            }
        else:
            message = {
                "message-type"      : "archive-key-next",
                "message-id"        : message_id,
                "priority"          : archive_priority,
                "collection-id"     : segment_row.collection_id,
                "key"               : segment_row.key,
                "unified-id"        : segment_row.unified_id,
                "conjoined-part"    : segment_row.conjoined_part,
                "timestamp-repr"    : repr(segment_row.timestamp),
                "segment-num"       : segment_row.segment_num,
                "segment-size"      : reply["segment-size"],
                "zfec-padding-size" : reply["zfec-padding-size"],
                "segment-adler32"   : reply["segment-adler32"],
                "segment-md5-digest": reply["segment-md5-digest"],
                "sequence-num"      : sequence,
                "source-node-name"  : node_name_dict[
                    segment_row.source_node_id],
                "handoff-node-name" : None,
            }
        
        writer_client.queue_message_for_send(message, data=data)
        reply = yield
        assert reply["result"] == "success", reply

    # we give back the segment_row and source node names as our last yield
    yield (segment_row, source_node_names, )

