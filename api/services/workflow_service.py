import json
import time
from collections.abc import Callable, Generator, Sequence
from datetime import UTC, datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.app.apps.advanced_chat.app_config_manager import AdvancedChatAppConfigManager
from core.app.apps.workflow.app_config_manager import WorkflowAppConfigManager
from core.model_runtime.utils.encoders import jsonable_encoder
from core.variables import Variable
from core.workflow.entities.node_entities import NodeRunResult
from core.workflow.errors import WorkflowNodeRunFailedError
from core.workflow.graph_engine.entities.event import InNodeEvent
from core.workflow.nodes import NodeType
from core.workflow.nodes.base.node import BaseNode
from core.workflow.nodes.enums import ErrorStrategy
from core.workflow.nodes.event import RunCompletedEvent
from core.workflow.nodes.event.types import NodeEvent
from core.workflow.nodes.node_mapping import LATEST_VERSION, NODE_TYPE_CLASSES_MAPPING
from core.workflow.repository import RepositoryFactory
from core.workflow.workflow_entry import WorkflowEntry
from events.app_event import app_draft_workflow_was_synced, app_published_workflow_was_updated
from extensions.ext_database import db
from models.account import Account
from models.enums import CreatedByRole
from models.model import App, AppMode
from models.tools import WorkflowToolProvider
from models.workflow import (
    Workflow,
    WorkflowNodeExecution,
    WorkflowNodeExecutionStatus,
    WorkflowNodeExecutionTriggeredFrom,
    WorkflowType,
)
from services.errors.app import WorkflowHashNotEqualError
from services.workflow.workflow_converter import WorkflowConverter

from .errors.workflow_service import DraftWorkflowDeletionError, WorkflowInUseError
from events.record_log import OperationRecordLog


class WorkflowService:
    """
    Workflow Service
    """

    def get_draft_workflow(self, app_model: App) -> Optional[Workflow]:
        """
        Get draft workflow
        """
        # fetch draft workflow by app_model
        workflow = (
            db.session.query(Workflow)
            .filter(
                Workflow.tenant_id == app_model.tenant_id, Workflow.app_id == app_model.id, Workflow.version == "draft"
            )
            .first()
        )

        # return draft workflow
        return workflow

    def get_published_workflow(self, app_model: App) -> Optional[Workflow]:
        """
        Get published workflow
        """

        if not app_model.workflow_id:
            return None

        # fetch published workflow by workflow_id
        workflow = (
            db.session.query(Workflow)
            .filter(
                Workflow.tenant_id == app_model.tenant_id,
                Workflow.app_id == app_model.id,
                Workflow.id == app_model.workflow_id,
            )
            .first()
        )

        return workflow

    def get_all_published_workflow(
        self,
        *,
        session: Session,
        app_model: App,
        page: int,
        limit: int,
        user_id: str | None,
        named_only: bool = False,
    ) -> tuple[Sequence[Workflow], bool]:
        """
        Get published workflow with pagination
        """
        if not app_model.workflow_id:
            return [], False

        stmt = (
            select(Workflow)
            .where(Workflow.app_id == app_model.id)
            .order_by(Workflow.version.desc())
            .limit(limit + 1)
            .offset((page - 1) * limit)
        )

        if user_id:
            stmt = stmt.where(Workflow.created_by == user_id)

        if named_only:
            stmt = stmt.where(Workflow.marked_name != "")

        workflows = session.scalars(stmt).all()

        has_more = len(workflows) > limit
        if has_more:
            workflows = workflows[:-1]

        return workflows, has_more

    def sync_draft_workflow(
        self,
        *,
        app_model: App,
        graph: dict,
        features: dict,
        unique_hash: Optional[str],
        account: Account,
        environment_variables: Sequence[Variable],
        conversation_variables: Sequence[Variable],
    ) -> Workflow:
        """
        Sync draft workflow
        :raises WorkflowHashNotEqualError
        """
        # fetch draft workflow by app_model
        workflow = self.get_draft_workflow(app_model=app_model)
        original_workflow = self.get_draft_workflow(app_model=app_model) if workflow else None # 获取当前草稿
        if workflow and workflow.unique_hash != unique_hash:
            raise WorkflowHashNotEqualError()

        # validate features structure
        self.validate_features_structure(app_model=app_model, features=features)

        # create draft workflow if not found
        if not workflow:
            workflow = Workflow(
                tenant_id=app_model.tenant_id,
                app_id=app_model.id,
                type=WorkflowType.from_app_mode(app_model.mode).value,
                version="draft",
                graph=json.dumps(graph),
                features=json.dumps(features),
                created_by=account.id,
                environment_variables=environment_variables,
                conversation_variables=conversation_variables,
            )
            db.session.add(workflow)
            # 记录恢复工作流历史版本操作记录
            #OperationRecordLog.Operation_log(app_model, "create", "workflow", "创建一个workflow应用")
        # update draft workflow if found
        else:
            changes = []
            # 检测graph变更
            if original_workflow and json.loads(original_workflow.graph) != graph:
                changes.append("结构")

            # 检测features变更
            if original_workflow and json.loads(original_workflow.features) != features:
                changes.append("特性")

            # 检测环境变量变更
            if original_workflow and original_workflow.environment_variables != environment_variables:
                changes.append("环境变量")

            # 检测对话变量变更
            if original_workflow and original_workflow.conversation_variables != conversation_variables:
                changes.append("对话变量")

            # 生成备注信息
            if changes:
                remark = f"更新工作流（变更: {', '.join(changes)}）"
                OperationRecordLog.Operation_log(app_model, "update", "workflow", remark=remark)

            workflow.graph = json.dumps(graph)
            workflow.features = json.dumps(features)
            workflow.updated_by = account.id
            workflow.updated_at = datetime.now(UTC).replace(tzinfo=None)
            workflow.environment_variables = environment_variables
            workflow.conversation_variables = conversation_variables


        # commit db session changes
        db.session.commit()

        # trigger app workflow events
        app_draft_workflow_was_synced.send(app_model, synced_draft_workflow=workflow)



        # return draft workflow
        return workflow

    def publish_workflow(
        self,
        *,
        session: Session,
        app_model: App,
        account: Account,
        marked_name: str = "",
        marked_comment: str = "",
    ) -> Workflow:
        draft_workflow_stmt = select(Workflow).where(
            Workflow.tenant_id == app_model.tenant_id,
            Workflow.app_id == app_model.id,
            Workflow.version == "draft",
        )
        draft_workflow = session.scalar(draft_workflow_stmt)
        if not draft_workflow:
            raise ValueError("No valid workflow found.")

        # create new workflow
        workflow = Workflow.new(
            tenant_id=app_model.tenant_id,
            app_id=app_model.id,
            type=draft_workflow.type,
            version=str(datetime.now(UTC).replace(tzinfo=None)),
            graph=draft_workflow.graph,
            features=draft_workflow.features,
            created_by=account.id,
            environment_variables=draft_workflow.environment_variables,
            conversation_variables=draft_workflow.conversation_variables,
            marked_name=marked_name,
            marked_comment=marked_comment,
        )

        # commit db session changes
        session.add(workflow)

        # trigger app workflow events
        app_published_workflow_was_updated.send(app_model, published_workflow=workflow)

        # return new workflow
        return workflow

    def get_default_block_configs(self) -> list[dict]:
        """
        Get default block configs
        """
        # return default block config
        default_block_configs = []
        for node_class_mapping in NODE_TYPE_CLASSES_MAPPING.values():
            node_class = node_class_mapping[LATEST_VERSION]
            default_config = node_class.get_default_config()
            if default_config:
                default_block_configs.append(default_config)

        return default_block_configs

    def get_default_block_config(self, node_type: str, filters: Optional[dict] = None) -> Optional[dict]:
        """
        Get default config of node.
        :param node_type: node type
        :param filters: filter by node config parameters.
        :return:
        """
        node_type_enum = NodeType(node_type)

        # return default block config
        if node_type_enum not in NODE_TYPE_CLASSES_MAPPING:
            return None

        node_class = NODE_TYPE_CLASSES_MAPPING[node_type_enum][LATEST_VERSION]
        default_config = node_class.get_default_config(filters=filters)
        if not default_config:
            return None

        return default_config

    def run_draft_workflow_node(
        self, app_model: App, node_id: str, user_inputs: dict, account: Account
    ) -> WorkflowNodeExecution:
        """
        Run draft workflow node
        """
        # fetch draft workflow by app_model
        draft_workflow = self.get_draft_workflow(app_model=app_model)
        if not draft_workflow:
            raise ValueError("Workflow not initialized")

        # run draft workflow node
        start_at = time.perf_counter()

        workflow_node_execution = self._handle_node_run_result(
            getter=lambda: WorkflowEntry.single_step_run(
                workflow=draft_workflow,
                node_id=node_id,
                user_inputs=user_inputs,
                user_id=account.id,
            ),
            start_at=start_at,
            tenant_id=app_model.tenant_id,
            node_id=node_id,
        )

        workflow_node_execution.app_id = app_model.id
        workflow_node_execution.created_by = account.id
        workflow_node_execution.workflow_id = draft_workflow.id

        # Use the repository to save the workflow node execution
        repository = RepositoryFactory.create_workflow_node_execution_repository(
            params={
                "tenant_id": app_model.tenant_id,
                "app_id": app_model.id,
                "session_factory": db.session.get_bind(),
            }
        )
        repository.save(workflow_node_execution)

        #记录运行工作流中的节点
        #OperationRecordLog.Operation_log(app_model, "run_draft_workflow", "workflow")

        return workflow_node_execution

    def run_free_workflow_node(
        self, node_data: dict, tenant_id: str, user_id: str, node_id: str, user_inputs: dict[str, Any]
    ) -> WorkflowNodeExecution:
        """
        Run draft workflow node
        """
        # run draft workflow node
        start_at = time.perf_counter()

        workflow_node_execution = self._handle_node_run_result(
            getter=lambda: WorkflowEntry.run_free_node(
                node_id=node_id,
                node_data=node_data,
                tenant_id=tenant_id,
                user_id=user_id,
                user_inputs=user_inputs,
            ),
            start_at=start_at,
            tenant_id=tenant_id,
            node_id=node_id,
        )
        #OperationRecordLog.Operation_log( "run_draft_workflow", "workflow",app=None)

        return workflow_node_execution

    def _handle_node_run_result(
        self,
        getter: Callable[[], tuple[BaseNode, Generator[NodeEvent | InNodeEvent, None, None]]],
        start_at: float,
        tenant_id: str,
        node_id: str,
    ) -> WorkflowNodeExecution:
        """
        Handle node run result

        :param getter: Callable[[], tuple[BaseNode, Generator[RunEvent | InNodeEvent, None, None]]]
        :param start_at: float
        :param tenant_id: str
        :param node_id: str
        """
        try:
            node_instance, generator = getter()

            node_run_result: NodeRunResult | None = None
            for event in generator:
                if isinstance(event, RunCompletedEvent):
                    node_run_result = event.run_result

                    # sign output files
                    node_run_result.outputs = WorkflowEntry.handle_special_values(node_run_result.outputs)
                    break

            if not node_run_result:
                raise ValueError("Node run failed with no run result")
            # single step debug mode error handling return
            if node_run_result.status == WorkflowNodeExecutionStatus.FAILED and node_instance.should_continue_on_error:
                node_error_args: dict[str, Any] = {
                    "status": WorkflowNodeExecutionStatus.EXCEPTION,
                    "error": node_run_result.error,
                    "inputs": node_run_result.inputs,
                    "metadata": {"error_strategy": node_instance.node_data.error_strategy},
                }
                if node_instance.node_data.error_strategy is ErrorStrategy.DEFAULT_VALUE:
                    node_run_result = NodeRunResult(
                        **node_error_args,
                        outputs={
                            **node_instance.node_data.default_value_dict,
                            "error_message": node_run_result.error,
                            "error_type": node_run_result.error_type,
                        },
                    )
                else:
                    node_run_result = NodeRunResult(
                        **node_error_args,
                        outputs={
                            "error_message": node_run_result.error,
                            "error_type": node_run_result.error_type,
                        },
                    )
            run_succeeded = node_run_result.status in (
                WorkflowNodeExecutionStatus.SUCCEEDED,
                WorkflowNodeExecutionStatus.EXCEPTION,
            )
            error = node_run_result.error if not run_succeeded else None
        except WorkflowNodeRunFailedError as e:
            node_instance = e.node_instance
            run_succeeded = False
            node_run_result = None
            error = e.error

        workflow_node_execution = WorkflowNodeExecution()
        workflow_node_execution.id = str(uuid4())
        workflow_node_execution.tenant_id = tenant_id
        workflow_node_execution.triggered_from = WorkflowNodeExecutionTriggeredFrom.SINGLE_STEP.value
        workflow_node_execution.index = 1
        workflow_node_execution.node_id = node_id
        workflow_node_execution.node_type = node_instance.node_type
        workflow_node_execution.title = node_instance.node_data.title
        workflow_node_execution.elapsed_time = time.perf_counter() - start_at
        workflow_node_execution.created_by_role = CreatedByRole.ACCOUNT.value
        workflow_node_execution.created_at = datetime.now(UTC).replace(tzinfo=None)
        workflow_node_execution.finished_at = datetime.now(UTC).replace(tzinfo=None)
        if run_succeeded and node_run_result:
            # create workflow node execution
            inputs = WorkflowEntry.handle_special_values(node_run_result.inputs) if node_run_result.inputs else None
            process_data = (
                WorkflowEntry.handle_special_values(node_run_result.process_data)
                if node_run_result.process_data
                else None
            )
            outputs = WorkflowEntry.handle_special_values(node_run_result.outputs) if node_run_result.outputs else None

            workflow_node_execution.inputs = json.dumps(inputs)
            workflow_node_execution.process_data = json.dumps(process_data)
            workflow_node_execution.outputs = json.dumps(outputs)
            workflow_node_execution.execution_metadata = (
                json.dumps(jsonable_encoder(node_run_result.metadata)) if node_run_result.metadata else None
            )
            if node_run_result.status == WorkflowNodeExecutionStatus.SUCCEEDED:
                workflow_node_execution.status = WorkflowNodeExecutionStatus.SUCCEEDED.value
            elif node_run_result.status == WorkflowNodeExecutionStatus.EXCEPTION:
                workflow_node_execution.status = WorkflowNodeExecutionStatus.EXCEPTION.value
                workflow_node_execution.error = node_run_result.error
        else:
            # create workflow node execution
            workflow_node_execution.status = WorkflowNodeExecutionStatus.FAILED.value
            workflow_node_execution.error = error

        return workflow_node_execution

    def convert_to_workflow(self, app_model: App, account: Account, args: dict) -> App:
        """
        Basic mode of chatbot app(expert mode) to workflow
        Completion App to Workflow App

        :param app_model: App instance
        :param account: Account instance
        :param args: dict
        :return:
        """
        # chatbot convert to workflow mode
        workflow_converter = WorkflowConverter()

        if app_model.mode not in {AppMode.CHAT.value, AppMode.COMPLETION.value}:
            raise ValueError(f"Current App mode: {app_model.mode} is not supported convert to workflow.")

        # convert to workflow
        new_app: App = workflow_converter.convert_to_workflow(
            app_model=app_model,
            account=account,
            name=args.get("name", "Default Name"),
            icon_type=args.get("icon_type", "emoji"),
            icon=args.get("icon", "🤖"),
            icon_background=args.get("icon_background", "#FFEAD5"),
        )
        # 记录更新工作流
        OperationRecordLog.Operation_log(app_model, "update", "workflow", "把app应用迁移变为工作流应用")

        return new_app

    def validate_features_structure(self, app_model: App, features: dict) -> dict:
        if app_model.mode == AppMode.ADVANCED_CHAT.value:
            return AdvancedChatAppConfigManager.config_validate(
                tenant_id=app_model.tenant_id, config=features, only_structure_validate=True
            )
        elif app_model.mode == AppMode.WORKFLOW.value:
            return WorkflowAppConfigManager.config_validate(
                tenant_id=app_model.tenant_id, config=features, only_structure_validate=True
            )
        else:
            raise ValueError(f"Invalid app mode: {app_model.mode}")

    def update_workflow(
        self, *, session: Session, workflow_id: str, tenant_id: str, account_id: str, data: dict
    ) -> Optional[Workflow]:
        """
        Update workflow attributes

        :param session: SQLAlchemy database session
        :param workflow_id: Workflow ID
        :param tenant_id: Tenant ID
        :param account_id: Account ID (for permission check)
        :param data: Dictionary containing fields to update
        :return: Updated workflow or None if not found
        """
        stmt = select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
        workflow = session.scalar(stmt)

        if not workflow:
            return None

        allowed_fields = ["marked_name", "marked_comment"]

        for field, value in data.items():
            if field in allowed_fields:
                setattr(workflow, field, value)

        workflow.updated_by = account_id
        workflow.updated_at = datetime.now(UTC).replace(tzinfo=None)

        # 记录更新工作流
        #OperationRecordLog.Operation_log(app_model, "update_node_workflow", "workflow")

        return workflow

    def delete_workflow(self, *, session: Session, workflow_id: str, tenant_id: str) -> bool:
        """
        Delete a workflow

        :param session: SQLAlchemy database session
        :param workflow_id: Workflow ID
        :param tenant_id: Tenant ID
        :return: True if successful
        :raises: ValueError if workflow not found
        :raises: WorkflowInUseError if workflow is in use
        :raises: DraftWorkflowDeletionError if workflow is a draft version
        """
        stmt = select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
        workflow = session.scalar(stmt)

        if not workflow:
            raise ValueError(f"Workflow with ID {workflow_id} not found")

        # Check if workflow is a draft version
        if workflow.version == "draft":
            raise DraftWorkflowDeletionError("Cannot delete draft workflow versions")

        # Check if this workflow is currently referenced by an app
        stmt = select(App).where(App.workflow_id == workflow_id)
        app = session.scalar(stmt)
        if app:
            # Cannot delete a workflow that's currently in use by an app
            raise WorkflowInUseError(f"Cannot delete workflow that is currently in use by app '{app.name}'")

        # Don't use workflow.tool_published as it's not accurate for specific workflow versions
        # Check if there's a tool provider using this specific workflow version
        tool_provider = (
            session.query(WorkflowToolProvider)
            .filter(
                WorkflowToolProvider.tenant_id == workflow.tenant_id,
                WorkflowToolProvider.app_id == workflow.app_id,
                WorkflowToolProvider.version == workflow.version,
            )
            .first()
        )

        if tool_provider:
            # Cannot delete a workflow that's published as a tool
            raise WorkflowInUseError("Cannot delete workflow that is published as a tool")

        session.delete(workflow)

        return True
