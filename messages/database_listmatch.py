# -*- coding: utf-8 -*-
"""
database_listmatch.py

DatabaseListMatch message
"""
import struct

from tools.marshalling import marshall_string, unmarshall_string

# 32s - request-id 32 char hex uuid
# Q   - avatar_id 
_header_format = "!32sQ"
_header_size = struct.calcsize(_header_format)

class DatabaseListMatch(object):
    """AMQP message to request a (partial) list of keys"""

    routing_key = "database_server.key_lookup"

    def __init__(
        self, 
        request_id, 
        avatar_id, 
        reply_exchange, 
        reply_routing_header, 
        prefix 
    ):
        self.request_id = request_id
        self.avatar_id = avatar_id
        self.reply_exchange = reply_exchange
        self.reply_routing_header = reply_routing_header
        self.prefix = prefix

    @classmethod
    def unmarshall(cls, data):
        """return a DatabaseListMatch message"""
        pos = 0
        (request_id, avatar_id, ) = struct.unpack(
            _header_format, data[pos:pos+_header_size]
        )
        pos += _header_size
        (reply_exchange, pos) = unmarshall_string(data, pos)
        (reply_routing_header, pos) = unmarshall_string(data, pos)
        (prefix, pos) = unmarshall_string(data, pos)
        return DatabaseListMatch(
            request_id, 
            avatar_id,
            reply_exchange, 
            reply_routing_header, 
            prefix 
        )

    def marshall(self):
        """return a data string suitable for transmission"""
        header = struct.pack(_header_format, self.request_id, self.avatar_id)
        packed_reply_exchange = marshall_string(self.reply_exchange)
        packed_reply_routing_header = marshall_string(self.reply_routing_header)
        packed_prefix = marshall_string(self.prefix)
        return "".join(
            [
                header,
                packed_reply_exchange,
                packed_reply_routing_header,
                packed_prefix,
            ]
        )
