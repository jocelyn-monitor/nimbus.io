# -*- coding: utf-8 -*-
"""
anti_entropy_server.py

Performs weekly or monthly consistency checks on every collection
Query each machine for a "database consistency check hash" 
(see below) for the collection.
Every machine on the network replies with it's consistency check hash for 
that collection.
If consistency hashes match, done, move on to next collection.
If consistency hashes don't match, schedule collection for recheck in an hour.
For any collection that misses 3 consistency checks in a row, 
do item level comparisons between nodes (see below.)

collection's "database consistency hash": 
Generated by querying the DB only on each machine for each collection. 
A hash is constructed from the sorted keys, 
adding the key, and the timestamp from the value, 
and the md5 from the stored value (if we have data) 
or a marker for a tombstone if we have one of those.

Item level comparisons: Pull all 10 databases. Iterate through them. 
(since they are all sorted, this doesn't require unbounded memory.) 
Ignore keys stored in the last hour, which may still be settling. 
Based on the timestamp values present for each key, 
you should be able to determine the "correct" state. 
I.e. if a tombstone is present, it means any earlier keys should not be there. 
If only some (but not all) shares are there, the remaining shares should be 
reconstructed and added. 
Any other situation would indicate a data integrity error 
that should be resolved.
"""
from base64 import b64encode
from collections import deque, namedtuple
import hashlib
import logging
import os
import random
import sys
import time

import zmq

from tools.zeromq_pollster import ZeroMQPollster
from tools.resilient_server import ResilientServer
from tools.event_push_client import EventPushClient, exception_event
from tools.pull_server import PULLServer
from tools.resilient_client import ResilientClient
from tools.deque_dispatcher import DequeDispatcher
from tools import time_queue_driven_process
from tools.data_definitions import create_timestamp, \
        parse_timestamp_repr
from tools.database_connection import get_central_connection, \
        get_node_local_connection

from web_server.central_database_util import get_cluster_row

from anti_entropy_server.common import max_retry_count, \
        retry_entry_tuple, \
        retry_time
from anti_entropy_server.audit_result_database import \
        AuditResultDatabase
from anti_entropy_server.collection_list_requestor import \
        CollectionListRequestor
from anti_entropy_server.consistency_check_starter import \
        ConsistencyCheckStarter
from anti_entropy_server.retry_manager import \
        RetryManager
from anti_entropy_server.state_cleaner import \
        StateCleaner

_node_names = os.environ['NIMBUSIO_NODE_NAME_SEQ'].split()
_local_node_name = os.environ["NIMBUSIO_NODE_NAME"]
_log_path = u"%s/nimbusio_anti_entropy_server_%s.log" % (
    os.environ["NIMBUSIO_LOG_DIR"], _local_node_name,
)
_client_tag = "anti-entropy-server-%s" % (_local_node_name, )
_anti_entropy_server_addresses = \
    os.environ["NIMBUSIO_ANTI_ENTROPY_SERVER_ADDRESSES"].split()
_anti_entropy_server_pipeline_address = os.environ.get(
    "NIMBUSIO_ANTI_ENTROPY_SERVER_PIPELINE_ADDRESS",
    "tcp://127.0.0.1:8650"
)
_request_timeout = 5.0 * 60.0
_error_reply = "*** error ***"

_request_state_tuple = namedtuple("RequestState", [ 
    "client_tag",
    "timestamp",
    "timeout",
    "retry_count",
    "replies",
    "row_id",
])

def _start_consistency_check(state, collection_id, row_id=None, retry_count=0):
    log = logging.getLogger("_start_consistency_check")

    timestamp = create_timestamp()
    state_key = (collection_id, timestamp, )

    database = AuditResultDatabase(state["central-database-connection"])
    if row_id is None:
        row_id = database.start_audit(collection_id, timestamp)
    else:
        database.restart_audit(row_id, timestamp)
    database.close()

    state["active-requests"][state_key] = _request_state_tuple(
        client_tag=None,
        timestamp=timestamp,
        timeout=time.time()+_request_timeout,
        retry_count=retry_count,
        replies=dict(), 
        row_id=row_id,
    )

    request = {
        "message-type"  : "consistency-check",
        "collection-id" : collection_id,
        "timestamp-repr": repr(timestamp),
    }
    for anti_entropy_client in state["anti-entropy-clients"]:
        anti_entropy_client.queue_message_for_send(request)

def _handle_anti_entropy_audit_request(state, message, _data):
    """handle a requst to audit a specific collection, not some random one"""
    log = logging.getLogger("_handle_anti_entropy_audit_request")

    timestamp = create_timestamp()
    state_key = (message["collection-id"], timestamp, )

    database = AuditResultDatabase(state["central-database-connection"])
    row_id = database.start_audit(message["collection-id"], timestamp)
    database.close()

    state["active-requests"][state_key] = _request_state_tuple(
        client_tag=message["client-tag"],
        timestamp=timestamp,
        timeout=time.time()+_request_timeout,
        retry_count=max_retry_count,
        replies=dict(), 
        row_id=row_id,
    )

    request = {
        "message-type"  : "consistency-check",
        "collection-id"     : message["collection-id"],
        "timestamp-repr": repr(timestamp),
    }
    for anti_entropy_client in state["anti-entropy-clients"]:
        anti_entropy_client.queue_message_for_send(request)

def _handle_database_collection_list_reply(state, message, _data):
    log = logging.getLogger("_handle_database_collection_list_reply")

    state["collection-ids"] = set(message["collection-id-list"])
    log.info("found %s collection ids" % (len(state["collection-ids"]), ))

def _handle_consistency_check(state, message, _data):
    log = logging.getLogger("_handle_consistency_check")

    reply = {
        "message-type"      : "consistency-check-reply",
        "client-tag"        : message["client-tag"],
        "node-name"         : _local_node_name,
        "collection-id"     : message["collection-id"],
        "timestamp-repr"    : message["timestamp-repr"],
        "result"            : None,
        "count"             : None,
        "encoded-md5-digest": None,
        "error-message"     : None
    }

    data_generator = state["local-database-connection"].generate_all_rows(
        """
        select key, timestamp, file_hash 
        from nimbusio_node.segment 
        where collection_id = %s 
        and status = 'A'
        and handoff_node_id is null
        order by key, timestamp
        """.strip(),
        [message["collection-id"], ]
    )

    count = 0
    md5 = hashlib.md5()
    for key, timestamp, file_hash in data_generator:
        count += 1
        md5.update(key)
        md5.update(repr(timestamp))
        md5.update(str(file_hash))

    log.info("found %s rows for collection %s %s" % (
        count, 
        message["collection-id"],             
        message["timestamp-repr"],
    ))

    reply["result"] = "success"
    reply["count"]  = count
    reply["encoded-md5-digest"] = b64encode(md5.digest())
    state["resilient-server"].send_reply(reply, None)

def _handle_consistency_check_reply(state, message, _data):
    log = logging.getLogger("_handle_consistency_check_reply")
    
    timestamp = parse_timestamp_repr(message["timestamp-repr"])
    state_key = (message["collection-id"], timestamp, )

    try:
        request_state = state["active-requests"][state_key]
    except KeyError:
        log.warn("Unknown state_key %s from %s" % (
            state_key, message["node-name"]
        ))
        return

    if message["node-name"] in request_state.replies:
        error_message = "duplicate reply from %s %s" % (
            message["node-name"],
            state_key, 
        )
        log.error(error_message)
        return

    if message["result"] != "success":
        log.error("%s (%s) %s from %s" % (
            state_key, 
            message["result"],
            message["error-message"],
            message["node-name"],
        ))
        reply_value = _error_reply
    else:
        reply_value = (message["count"], message["encoded-md5-digest"], )

    request_state.replies[message["node-name"]] = reply_value

    # not done yet, wait for more replies
    if len(request_state.replies) < len(state["anti-entropy-clients"]):
        return

    # at this point we should have a reply from every node, so
    # we don't want to preserve state anymore
    del state["active-requests"][state_key]
    database = AuditResultDatabase(state["central-database-connection"])
    timestamp = create_timestamp()
    
    # push the results into a dict to see how many unique entries there are
    md5_digest_dict = dict()
    md5_digest_dict[_error_reply] = list()

    for node_name in request_state.replies.keys():
        node_reply = request_state.replies[node_name]
        if node_reply == _error_reply:
            md5_digest_dict[_error_reply].append(node_name)
            continue

        _count, encoded_md5_digest = node_reply
        if not encoded_md5_digest in md5_digest_dict:
            md5_digest_dict[encoded_md5_digest] = list()
        md5_digest_dict[encoded_md5_digest].append(node_name)

    # if this audit was started by an anti-entropy-audit-request message,
    # we want to send a reply
    if request_state.client_tag is not None:
        reply = {
            "message-type"  : "anti-entropy-audit-reply",
            "client-tag"    : request_state.client_tag,
            "collection-id" : message["collection-id"],
            "result"        : None,
            "error-message" : None,
        }
    else:
        reply = None

    error_reply_list = md5_digest_dict.pop(_error_reply)
    if reply is not None:
        reply["error-reply-nodes"] = error_reply_list


    if len(md5_digest_dict) > 1:
        log.error("found %s different hashes for (%s)" % (
            len(md5_digest_dict), 
            message["collection-id"],
        ))
        for index, value in enumerate(md5_digest_dict.values()):
            log.info(str(value))
            if reply is not None:
                reply["mistmatch-nodes-%s" % (index+1, )] = value
        
    # ok = no errors and all nodes have the same hash for every collection
    if len(error_reply_list) == 0 and len(md5_digest_dict) == 1:
        description = "collection %s compares ok" % (
            message["collection-id"], 
        )
        log.info(description)
        state["event-push-client"].info(
            "audit-ok", description, collection_id=message["collection-id"]
        )  
        database.successful_audit(request_state.row_id, timestamp)
        if reply is not None:
            reply["result"] = "success"
            state["resilient-server"].send_reply(reply)
        return

    # we have error(s), but the non-errors compare ok
    if len(error_reply_list) > 0 and len(md5_digest_dict) == 1:

        # if we come from anti-entropy-audit-request, don't retry
        if reply is not None:
            database.audit_error(request_state.row_id, timestamp)
            database.close()
            description = "There were error replies from %s nodes" % (
                len(error_reply_list) , 
            )
            log.error(description)
            state["event-push-client"].error(
                "consistency-check-errors-replies", 
                description, 
                collection_id=message["collection-id"],
                error_reply_nodes=error_reply_list
            )  
            reply["result"] = "error"
            reply["error-message"] = description
            state["resilient-server"].send_reply(reply)
            return
        
        if request_state.retry_count >= max_retry_count:
            description = "collection %s %s errors, too many retries" % (
                message["collection-id"], 
                len(error_reply_list) 
            )
            log.error(description)
            state["event-push-client"].error(
                "audit-errors", 
                description, 
                collection_id=message["collection-id"]
            )  
            database.audit_error(request_state.row_id, timestamp)
            # TODO: needto do something here
        else:
            description = "%s Error replies from %s nodes, will retry" % (
                message["collection-id"], 
                len(error_reply_list) 
            )
            log.warn(description)
            state["event-push-client"].warn(
                "audit-retry", 
                description, 
                collection_id=message["collection-id"]
            )  
            state["retry-list"].append(
                retry_entry_tuple(
                    retry_time=retry_time(), 
                    collection_id=message["collection-id"],
                    row_id=request_state.row_id,
                    retry_count=request_state.retry_count, 
                )
            )
            database.wait_for_retry(request_state.row_id)
        database.close()
        return

    # if we make it here, we have some form of mismatch, possibly mixed with
    # errors
    description = "%s error replies from %s nodes; hash mismatch(es) = %r" % (
        message["collection-id"], 
        len(error_reply_list),
        md5_digest_dict.values()
    )
    log.error(description)
    state["event-push-client"].warn(
        "audit-retry", 
        description, 
        collection_id=message["collection-id"]
    )  

    # if we come from anti-entropy-audit-request, don't retry
    if reply is not None:
        database.audit_error(request_state.row_id, timestamp)
        database.close()
        reply["result"] = "audit-error"
        reply["error-message"] = description
        state["resilient-server"].send_reply(reply)
        return

    if request_state.retry_count >= max_retry_count:
        log.error("%s too many retries" % (message["collection-id"], ))
        database.audit_error(request_state.row_id, timestamp)
        # TODO: need to do something here
    else:
        state["retry-list"].append(
            retry_entry_tuple(
                retry_time=retry_time(), 
                collection_id=message["collection-id"],
                row_id=request_state.row_id,
                retry_count=request_state.retry_count, 
            )
        )
        database.wait_for_retry(request_state.row_id)

    database.close()

_dispatch_table = {
    "anti-entropy-audit-request"    :  _handle_anti_entropy_audit_request,
    "consistency-check"             :  _handle_consistency_check,
    "consistency-check-reply"       :  _handle_consistency_check_reply,
}

def _create_state():
    return {
        "central-database-connection": None,
        "local-database-connection" : None,
        "zmq-context"               : zmq.Context(),
        "pollster"                  : ZeroMQPollster(),
        "resilient-server"          : None,
        "event-push-client"         : None,
        "pull-server"               : None,
        "anti-entropy-clients"      : None,
        "collection-list-requestor" : None,
        "consistency-check-starter" : None,
        "retry_manager"             : None,
        "state-cleaner"             : None,
        "receive-queue"             : deque(),
        "queue-dispatcher"          : None,
        "active-requests"           : dict(),
        "retry-list"                : list(),
        "collection-ids"            : set(),
        "cluster-row"               : None,
    }

def _setup(_halt_event, state):
    log = logging.getLogger("_setup")
    status_checkers = list()

    # do the event push client first, because we may need to
    # push an execption event from setup
    state["event-push-client"] = EventPushClient(
        state["zmq-context"],
        "anti_entropy_server"
    )

    state["central-database-connection"] = get_central_connection()
    state["local-database-connection"] = get_node_local_connection()

    state["cluster-row"] = get_cluster_row(
        state["central-database-connection"] 
    )

    local_anti_entropy_server_address = None
    for node_name, address in zip(_node_names, _anti_entropy_server_addresses):
        if node_name == _local_node_name:
            local_anti_entropy_server_address = address
            break
    assert local_anti_entropy_server_address is not None

    log.info("binding resilient-server to %s" % (
        local_anti_entropy_server_address, 
    ))
    state["resilient-server"] = ResilientServer(
        state["zmq-context"],
        local_anti_entropy_server_address,
        state["receive-queue"]
    )
    state["resilient-server"].register(state["pollster"])

    log.info("binding pull-server to %s" % (
        _anti_entropy_server_pipeline_address, 
    ))
    state["pull-server"] = PULLServer(
        state["zmq-context"],
        _anti_entropy_server_pipeline_address,
        state["receive-queue"]
    )
    state["pull-server"].register(state["pollster"])

    state["anti-entropy-clients"] = list()
    for node_name, anti_entropy_server_address in zip(
        _node_names, _anti_entropy_server_addresses
    ):
        resilient_client = ResilientClient(
                state["zmq-context"],
                state["pollster"],
                node_name,
                anti_entropy_server_address,
                _client_tag,
                _anti_entropy_server_pipeline_address
            )
        state["anti-entropy-clients"].append(resilient_client)
        status_checkers.append(
            (resilient_client.run, time.time() + random.random() * 60.0, )
        )        

    state["queue-dispatcher"] = DequeDispatcher(
        state,
        state["receive-queue"],
        _dispatch_table
    )

    state["collection-list-requestor"] = CollectionListRequestor(state)
    state["consistency-check-starter"] = ConsistencyCheckStarter(
        state, _start_consistency_check
    )
    state["retry-manager"] = RetryManager(
        state, _start_consistency_check
    )
    state["state-cleaner"] = StateCleaner(state)

    state["event-push-client"].info(
        "program-start", "anti_entropy_server starts"
    )  

    # start the collection list requestor right away
    # start the consistency check starter a little later, when
    # we presumably have some collection ids
    timer_driven_callbacks = [
        (state["pollster"].run, time.time(), ), 
        (state["queue-dispatcher"].run, time.time(), ), 
        (state["collection-list-requestor"].run, time.time(), ), 
        (state["consistency-check-starter"].run, time.time()+60.0, ), 
        (state["retry-manager"].run, state["retry-manager"].next_run(), ), 
        (state["state-cleaner"].run, state["state-cleaner"].next_run(), ), 
    ] 
    timer_driven_callbacks.extend(status_checkers)
    return timer_driven_callbacks

def _tear_down(_state):
    log = logging.getLogger("_tear_down")

    log.debug("stopping server")
    state["resilient-server"].close()

    log.debug("stopping anti entropy clients")
    state["pull-server"].close()
    for anti_entropy_client in state["anti-entropy-clients"]:
        anti_entropy_client.close()

    state["event-push-client"].close()

    state["zmq-context"].term()
    state["local-database-connection"].close()
    state["central-database-connection"].close()

    log.debug("teardown complete")

if __name__ == "__main__":
    state = _create_state()
    sys.exit(
        time_queue_driven_process.main(
            _log_path,
            state,
            pre_loop_actions=[_setup, ],
            post_loop_actions=[_tear_down, ],
            exception_action=exception_event
        )
    )

