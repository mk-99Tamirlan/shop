#!/bin/sh
set -eu
NODES="172.30.0.11:6379 172.30.0.12:6379 172.30.0.13:6379 172.30.0.14:6379 172.30.0.15:6379 172.30.0.16:6379"
for n in $NODES; do
  host=${n%:*}
  i=0
  until redis-cli -h "$host" ping 2>/dev/null | grep -q PONG; do
    i=$((i+1)); [ "$i" -lt 60 ] || exit 1
    sleep 1
  done
done
if redis-cli -h 172.30.0.11 cluster info | grep -q 'cluster_state:ok'; then
  echo 'Cluster already initialized'
else
  redis-cli --cluster create $NODES --cluster-replicas 1 --cluster-yes
fi
i=0
until redis-cli -h 172.30.0.11 cluster info | grep -q 'cluster_state:ok'; do
  i=$((i+1)); [ "$i" -lt 60 ] || exit 1
  sleep 1
done
redis-cli -h 172.30.0.11 cluster nodes
