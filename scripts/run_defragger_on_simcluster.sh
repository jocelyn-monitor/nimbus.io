#!/bin/bash

# simple script to run the defragger against a running cluster sim.
# pass basedir of cluster sim as $1
# finds config for cluster under basedi

set -x
set -e

BASEDIR=$1

if [ ! -d $BASEDIR ]; then
    echo "basedir '$BASEDIR' does not exist"
    exit 1
fi

PYTHON="${HOME}/python_env/nimbus.io.0.0.1/bin/python3"

# pull in environment settings from the simulated cluster 
pushd "${BASEDIR}/config"
source node_01_config.sh
popd

# run unit tests with identity file
$PYTHON "${HOME}/nimbus.io/defragger/defragger_main.py"

