from flask_login import current_user  # type: ignore  # type: ignore
from flask_restful import Resource, marshal_with, reqparse  # type: ignore
from werkzeug.exceptions import NotFound

from controllers.console import api
from controllers.console.wraps import account_initialization_required, enterprise_license_required, setup_required
from fields.dataset_fields import dataset_metadata_fields
from libs.login import login_required
from services.dataset_service import DatasetService
from services.entities.knowledge_entities.knowledge_entities import (
    MetadataArgs,
    MetadataOperationData,
)
from services.metadata_service import MetadataService
from events.record_log import OperationRecordLog

class DatasetMetadataCreateApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    @marshal_with(dataset_metadata_fields)
    def post(self, dataset_id):
        parser = reqparse.RequestParser()
        parser.add_argument("type", type=str, required=True, nullable=True, location="json")
        parser.add_argument("name", type=str, required=True, nullable=True, location="json")
        args = parser.parse_args()
        metadata_args = MetadataArgs(**args)

        dataset_id_str = str(dataset_id)
        dataset = DatasetService.get_dataset(dataset_id_str)
        if dataset is None:
            raise NotFound("Dataset not found.")
        DatasetService.check_dataset_permission(dataset, current_user)

        metadata = MetadataService.create_metadata(dataset_id_str, metadata_args)
        OperationRecordLog.Operation_log(
            app=dataset,
            action="create",
            type="knowledge",
            remark="新增元数据"
        )
        return metadata, 201

    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    def get(self, dataset_id):
        dataset_id_str = str(dataset_id)
        dataset = DatasetService.get_dataset(dataset_id_str)
        if dataset is None:
            raise NotFound("Dataset not found.")
        return MetadataService.get_dataset_metadatas(dataset), 200


class DatasetMetadataApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    @marshal_with(dataset_metadata_fields)
    def patch(self, dataset_id, metadata_id):
        parser = reqparse.RequestParser()
        parser.add_argument("name", type=str, required=True, nullable=True, location="json")
        args = parser.parse_args()

        dataset_id_str = str(dataset_id)
        metadata_id_str = str(metadata_id)
        dataset = DatasetService.get_dataset(dataset_id_str)
        if dataset is None:
            raise NotFound("Dataset not found.")
        DatasetService.check_dataset_permission(dataset, current_user)

        metadata = MetadataService.update_metadata_name(dataset_id_str, metadata_id_str, args.get("name"))
        OperationRecordLog.Operation_log(
            app=dataset,
            action="update",
            type="knowledge",
            remark="更新元数据"
        )
        return metadata, 200

    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    def delete(self, dataset_id, metadata_id):
        dataset_id_str = str(dataset_id)
        metadata_id_str = str(metadata_id)
        dataset = DatasetService.get_dataset(dataset_id_str)
        if dataset is None:
            raise NotFound("Dataset not found.")
        DatasetService.check_dataset_permission(dataset, current_user)

        MetadataService.delete_metadata(dataset_id_str, metadata_id_str)
        OperationRecordLog.Operation_log(
            app=dataset,
            action="delete",
            type="knowledge",
            remark="删除元数据"
        )
        return {"result": "success"}, 204


class DatasetMetadataBuiltInFieldApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    def get(self):
        built_in_fields = MetadataService.get_built_in_fields()
        return {"fields": built_in_fields}, 200


class DatasetMetadataBuiltInFieldActionApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    def post(self, dataset_id, action):
        dataset_id_str = str(dataset_id)
        dataset = DatasetService.get_dataset(dataset_id_str)
        if dataset is None:
            raise NotFound("Dataset not found.")
        DatasetService.check_dataset_permission(dataset, current_user)

        if action == "enable":
            MetadataService.enable_built_in_field(dataset)
            OperationRecordLog.Operation_log(
                app=dataset,
                action="update",
                type="knowledge",
                remark="启用内置元数据"
            )
        elif action == "disable":
            MetadataService.disable_built_in_field(dataset)
            OperationRecordLog.Operation_log(
                app=dataset,
                action="update",
                type="knowledge",
                remark="停用内置元数据"
            )
        return 200


class DocumentMetadataEditApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @enterprise_license_required
    def post(self, dataset_id):
        dataset_id_str = str(dataset_id)
        dataset = DatasetService.get_dataset(dataset_id_str)
        if dataset is None:
            raise NotFound("Dataset not found.")
        DatasetService.check_dataset_permission(dataset, current_user)

        parser = reqparse.RequestParser()
        parser.add_argument("operation_data", type=list, required=True, nullable=True, location="json")
        args = parser.parse_args()
        metadata_args = MetadataOperationData(**args)

        MetadataService.update_documents_metadata(dataset, metadata_args)

        return 200


api.add_resource(DatasetMetadataCreateApi, "/datasets/<uuid:dataset_id>/metadata")
api.add_resource(DatasetMetadataApi, "/datasets/<uuid:dataset_id>/metadata/<uuid:metadata_id>")
api.add_resource(DatasetMetadataBuiltInFieldApi, "/datasets/metadata/built-in")
api.add_resource(DatasetMetadataBuiltInFieldActionApi, "/datasets/<uuid:dataset_id>/metadata/built-in/<string:action>")
api.add_resource(DocumentMetadataEditApi, "/datasets/<uuid:dataset_id>/documents/metadata")
