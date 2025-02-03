import os
import time
import git
import boto3
import yaml
import subprocess
import datetime
import asyncio
import tempfile
import pkg_resources
from celery import Celery
from app.core.task_framework import TaskWrapper, Task, TaskStatus
from app.connection.establish_db_connection import get_node_db
from app.core.Settings import settings
from app.agents.agent_main import configure_node
from app.celery_app import celery_app as app, tenant_context, user_context
from app.knowledge.code_query import KnowledegeBuild
from app.utils.async_utils import async_to_sync
from app.services.notification_service import NotificationService
from app.models.notification_model import NotificationModel, CodeGenerationNotificationData
from app.core.constants import TASKS_COLLECTION_NAME
from app.connection.establish_db_connection import get_mongo_db

def generate_session_dir() -> str:
    session_root = "/tmp/kavia/code_gen"
    return session_root

# YM: The below tasks exist only for illustrative purposes
# Will be removed once we have at least one real task which
# can be triggered


@app.task(bind=True)
def processing_autoconfig(self,  node_id, node_type, user_level, request, tenant_id='neo4j', current_user='admin'):
    task_id = self.request.id
    try:
        # Set tenant context
        print("processing auto config", current_user, tenant_id)
        user_token = user_context.set(current_user)
        token = tenant_context.set(tenant_id)
        with TaskWrapper(task_id) as task:
            task.report_progress(50)
            result = async_to_sync(configure_node(node_id, node_type, user_level, request, task_id))
            print(result)
            task.report_progress(80)
            return result
    finally:
        # Reset tenant context
        user_context.reset(user_token)
        tenant_context.reset(token)
    
@app.task
def processing(x, y):
    task_id = processing.request.id
    with TaskWrapper(task_id) as task:
        task.report_progress(50)
        result = x + y
        task.report_progress(80)
        time.sleep(30)
        return result



@app.task
def report_result(result):
    task_id = report_result.request.id
    with TaskWrapper(task_id) as task:
        raise Exception('Unfortunate error!')
    
@app.task(bind=True)
def send_notification_task(self, task_id: str, content: str = None, tenant_id: str = None, current_user: str = None):
    """Celery task for sending notifications"""
    try:
        print("started ", (self.request.id))
        # Set tenant context
        user_token = user_context.set(current_user)
        token = tenant_context.set(tenant_id)
        db = get_mongo_db().db
        with TaskWrapper(self.request.id) as task:
            task.report_progress(20)
            
            # Get task info
            task_data = db[TASKS_COLLECTION_NAME].find_one(
                {"_id": task_id},
                projection={
                    "project_id": 1,
                    "architecture_id": 1,
                    "user_id": 1,
                    "_id": 0
                }
            )
            
            if task_data:
                task.report_progress(50)
                
                notification_data = NotificationModel(
                    receiver_id=task_data.get("user_id"),
                    type="code_generation",
                    action="code_generation",
                    data=CodeGenerationNotificationData(
                        message=f"Task ID: {task_id} - Waiting for user input to continue \n\n{content}",
                        link=f"/projects/{task_data.get('project_id')}/architecture/design?task_id={task_id}",
                        project_id=task_data.get('project_id'),
                        task_id=task_id,
                        design_id=task_data.get('architecture_id', 1) 
                    )
                )
                
                notification_service = NotificationService()
                async_to_sync(notification_service.send_notification(notification_data))
                
                task.report_progress(100)
                return True
                
    except Exception as e:
        print(f"Error in notification task: {e}")
        raise e
    finally:
        # Reset tenant context
        tenant_context.reset(token)

@app.task(bind=True)
def clone(self, project_id, build_session_id, data_dir, repository, tenant_id, current_user, upstream):
    user_token = user_context.set(current_user)
    token = tenant_context.set(tenant_id)
    try:
        
        kg = KnowledegeBuild()
        async_to_sync(kg.clone(project_id, build_session_id, current_user, data_dir, [repository], upstream))
    finally:
        user_context.reset(user_token)
        tenant_context.reset(token)
        
@app.task(bind=True)
def upstream(self, project_id, build_session_id, build_id, tenant_id, current_user):
    user_token = user_context.set(current_user)
    token = tenant_context.set(tenant_id)
    try:
        
        kg = KnowledegeBuild()
        async_to_sync(kg.upstream(project_id,build_session_id, build_id, current_user))
    finally:
        user_context.reset(user_token)
        tenant_context.reset(token)
    
@app.task(bind=True)
def send_notification(
    self, 
    task_id: str, 
    message: str,
    status: str = "info",
    notification_type: str = "code_generation",
    notification_action: str = "code_generation",
    tenant_id: str = None, 
    current_user: str = None
):
    """Celery task for sending task notifications
    
    Args:
        task_id: ID of the related task
        message: Notification message to display
        status: Status of the notification (e.g., 'success', 'failure', 'info', 'warning')
        notification_type: Type of notification (e.g., 'code_generation', 'deployment')
        notification_action: Action associated with the notification
        tenant_id: Tenant identifier
        current_user: Current user making the request
    """
    try:
        print("started ", (self.request.id))
        # Set tenant context
        user_token = user_context.set(current_user)
        token = tenant_context.set(tenant_id)
        db = get_mongo_db().db
        with TaskWrapper(self.request.id) as task:
            task.report_progress(20)
            
            # Get task info
            task_data = db[TASKS_COLLECTION_NAME].find_one(
                {"_id": task_id},
                projection={
                    "project_id": 1,
                    "architecture_id": 1,
                    "user_id": 1,
                    "_id": 0
                }
            )
            
            if task_data:
                task.report_progress(50)
                agent_name = task_data.get("agent_name")
                print("agent_name", agent_name)
                design_id = task_data.get("architecture_id")
                if not design_id:
                    design_id = 1
                if agent_name == "CodeMaintenance":
                    notification_data = NotificationModel(
                        receiver_id=task_data.get("user_id"),
                        type=notification_type,
                        action=notification_action,
                        data=CodeGenerationNotificationData(
                            message=f"Task ID: {task_id} - {message}",
                            link=f"/projects/{task_data.get('project_id')}/architecture/codemaintenance?task_id={task_id}",
                            project_id=task_data.get('project_id'),
                            task_id=task_id,
                            design_id=design_id
                        )
                    )
                else:
                    notification_data = NotificationModel(
                        receiver_id=task_data.get("user_id"),
                        type=notification_type,
                        action=notification_action,
                        data=CodeGenerationNotificationData(
                        message=f"Task ID: {task_id} - {message}",
                        link=f"/projects/{task_data.get('project_id')}/architecture/design?task_id={task_id}",
                        project_id=task_data.get('project_id'),
                        task_id=task_id,
                        design_id=design_id
                    )
                )
                
                notification_service = NotificationService()
                async_to_sync(notification_service.send_notification(notification_data))
                
                task.report_progress(100)
                return True
                
    except Exception as e:
        print(f"Error in notification task: {e}")
        raise e
    finally:
        # Reset tenant context
        tenant_context.reset(token)



