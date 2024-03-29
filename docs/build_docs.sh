#!/bin/bash

DOCS_DIR="$(dirname $0)"
CODEBASE="$(dirname $DOCS_DIR)"
CODEBASE="$(cd $CODEBASE ; pwd)"

export PYTHONPATH="${CODEBASE}"
export NIMBUSIO_NODE_NAME_SEQ=""
export NIMBUSIO_EVENT_PUBLISHER_PULL_ADDRESS=""
export NIMBUSIO_NODE_NAME=""
export NIMBUSIO_WEB_SERVER_PIPELINE_ADDRESS=""
export NIMBUSIO_DATA_READER_ADDRESSES=""
export NIMBUSIO_DATA_WRITER_ADDRESSES=""
export NIMBUSIO_SPACE_ACCOUNTING_SERVER_ADDRESS=""
export NIMBUSIO_SPACE_ACCOUNTING_PIPELINE_ADDRESS=""
export NIMBUSIO_WEB_SERVER_HOST=""
export NIMBUSIO_WEB_SERVER_PORT="8088"
export NIMBUSIO_EVENT_PUBLISHER_PULL_ADDRESS="" 
export NIMBUSIO_CLUSTER_NAME=""
export NIMBUSIO_LOG_DIR=""
export NIMBUSIO_EVENT_PUBLISHER_PUB_ADDRESS=""
export NIMBUSIO_ANTI_ENTROPY_SERVER_ADDRESSES=""
export NIMBUSIO_HANDOFF_SERVER_ADDRESSES=""
export NIMBUSIO_REPOSITORY_PATH=""

pushd "${CODEBASE}/docs"
make clean
make html
popd
