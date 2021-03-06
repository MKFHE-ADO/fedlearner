# Copyright 2020 The FedLearner Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# coding: utf-8

from fedlearner.common import common_pb2 as common_pb
from fedlearner.common import data_join_service_pb2 as dj_pb
from fedlearner.common import data_join_service_pb2_grpc as dj_grpc

from fedlearner.data_join.example_id_sync_follower import (
    ExampleIdSyncFollower
)
from fedlearner.data_join.example_join_leader import (
    ExampleJoinLeader
)

class DataJoinFollower(dj_grpc.DataJoinFollowerServiceServicer):
    def __init__(self, peer_client, master_client,
                 rank_id, etcd, data_source, options):
        super(DataJoinFollower, self).__init__()
        assert data_source.role == common_pb.FLRole.Follower
        self._peer_client = peer_client
        self._master_client = master_client
        self._rank_id = rank_id
        self._etcd = etcd
        self._data_source = data_source
        self._started = False
        self._example_id_sync_follower = ExampleIdSyncFollower(
                self._data_source
            )
        self._example_join_leader = ExampleJoinLeader(
                self._peer_client, self._master_client,
                self._rank_id, self._etcd,
                self._data_source, options
            )

    def start(self):
        self._example_id_sync_follower.start_dump_worker()
        self._example_join_leader.start_routine_workers()

    def stop(self):
        self._example_join_leader.stop_routine_workers()
        self._example_id_sync_follower.stop_dump_worker()

    def StartPartition(self, request, context):
        response = dj_pb.FollowerStartPartitionResponse()
        if not self._validate_data_source_meta(
                request.data_source_meta, self._data_source.data_source_meta):
            response.status.code = -1
            response.status.error_message = "data source meta mismtach"
            return response

        if request.partition_id < 0:
            response.status.code = -2
            response.status.error_message = (
                    "partition id {} illegal".format(request.partition_id)
                )
            return response

        manifest = self._query_raw_data_manifest(request.partition_id)
        if manifest.state > dj_pb.RawDataState.Syncing:
            response.finished = True
            return response

        rdr_req = dj_pb.RawDataRequest(
                data_source_meta=self._data_source.data_source_meta,
                rank_id=self._rank_id,
                sync_example_id=dj_pb.SyncExampleIdRequest(
                    partition_id=request.partition_id
                )
            )
        rdr_rsp = self._master_client.RequestJoinPartition(rdr_req)
        if rdr_rsp.status.code != 0:
            response.status.MergeFrom(rdr_rsp.status)
            return response
        if not rdr_rsp.HasField("manifest"):
            raise RuntimeError(
                    "unknow field for master raw data request response"
                )
        assert rdr_rsp.manifest.state == dj_pb.RawDataState.Syncing

        sync_follower = self._example_id_sync_follower
        next_index = sync_follower.start_dump_partition(
                request.partition_id
            )
        response.finished = False
        response.next_index = next_index
        return response

    def SyncExamples(self, request, context):
        response = common_pb.Status()
        response.code = 0
        if not self._validate_data_source_meta(
                request.data_source_meta, self._data_source.data_source_meta):
            response.code = -1
            response.error_message = "data source meta mismtach"
            return response
        sync_follower = self._example_id_sync_follower
        filled, next_index = sync_follower.add_synced_example_req(request)
        if not filled:
            response.code = -1
            response.error_message = (
                    "the follower required {}".format(next_index)
                )
        return response

    def FinishPartition(self, request, context):
        response = dj_pb.FollowerFinishPartitionResponse()
        response.status.code = 0
        response.finished = False
        if not self._validate_data_source_meta(
                request.data_source_meta, self._data_source.data_source_meta):
            response.status.code = -1
            response.status.error_message = "data source meta mismtach"
            return response

        sync_follower = self._example_id_sync_follower
        if (sync_follower.get_processing_partition_id() ==
                request.partition_id):
            finished = sync_follower.finish_sync_partition_example(
                    request.partition_id
                )
            if finished:
                req = dj_pb.FinishRawDataRequest(
                        data_source_meta=self._data_source.data_source_meta,
                        rank_id=self._rank_id,
                        sync_example_id=dj_pb.SyncExampleIdRequest(
                            partition_id=request.partition_id
                        )
                    )
                rsp = self._master_client.FinishJoinPartition(req)
                response.status.MergeFrom(rsp)
                if rsp.code == 0:
                    sync_follower.reset_dump_partition()
            response.finished = finished
        else:
            manifest = self._query_raw_data_manifest(request.partition_id)
            if manifest.state > dj_pb.RawDataState.Syncing:
                response.finished = True
            else:
                response.status.code = -2
                response.status.finished = False
                response.status.error_message = (
                        "partition {} at state {} but it is not "
                        "processing".format(request.partition_id,
                                            manifest.state)
                    )
        return response

    def _query_raw_data_manifest(self, partition_id):
        query_req = dj_pb.RawDataManifestRequest(
                data_source_meta=self._data_source.data_source_meta,
                partition_id=partition_id
            )
        query_rsp = self._master_client.QueryRawDataManifest(query_req)
        if query_rsp.status.code != 0:
            raise RuntimeError(
                    "Failed to get raw data manifest for "\
                    "partition {}".format(partition_id)
                )
        manifest = query_rsp.manifest
        assert (manifest is not None and
                    manifest.partition_id == partition_id)
        return manifest

    @staticmethod
    def _validate_data_source_meta(remote_meta, local_meta):
        return remote_meta == local_meta
