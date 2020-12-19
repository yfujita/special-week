#!/bin/bash

QUERY_FILE=$1

curl -H "Content-Type:application/json" "localhost:9200/horses/_search?pretty" --data-binary @$QUERY_FILE > ./outputs/out-$QUERY_FILE