import random
import string
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from typing import List
from datetime import datetime
import os

from github import Github
from app.connection.establish_db_connection import get_mongo_db
from app.connection.tenant_middleware import get_tenant_id
from app.core.Settings import settings
from app.core.websocket.client import WebSocketClient
from app.knowledge.code_query import KnowledegeBuild
from app.utils.auth_utils import get_current_user
from git import Repo
from urllib.parse import urlparse
import logging
import time
import asyncio
from app.tasks import clone, upstream
from app.core.task_framework import Task
import shutil

from app.utils.kg_inspect.knowledge import Knowledge, KnowledgeCodeBase
from app.utils.kg_inspect.knowledge_helper import Knowledge_Helper
from app.utils.kg_inspect.knowledge_reporter import Reporter

router = APIRouter()

from pydantic import BaseModel
from typing import List
from app.utils.kg_build.import_codebase import _import_code, get_latest_commit_hash
class RepoBranchRequest(BaseModel):
    repo_name: str
    branch_name: str
    repo_type: str  # 'public' or 'private'
    repo_id: str
    associated: bool
class CodebaseImportRequest(BaseModel):
    project_id: int
    repositories: List[RepoBranchRequest]
    
def clone_repository(repo_path: str, git_url: str, token: str = None, branch: str = None) -> bool:
    try:
        # Check if repository already exists
        if os.path.exists(repo_path):
            logging.info(f"Repository already exists at: {repo_path}")
            return True
        
        if token:
            parsed_url = urlparse(git_url)
            auth_url = f"https://{token}@{parsed_url.netloc}{parsed_url.path}"
        else:
            auth_url = git_url
    
        # Create a temporary path for cloning
        temp_clone_path = f"{repo_path}_temp"
        
        try:
            # Attempt to clone to temporary location
            Repo.clone_from(auth_url, temp_clone_path, branch=branch)
            
            return True, temp_clone_path
            
        except Exception as e:
            # Clean up temporary directory if something went wrong
            if os.path.exists(temp_clone_path):
                shutil.rmtree(temp_clone_path)
            raise e
        
    except Exception as e:
        logging.error(f"Failed to clone repository: {str(e)}")
        
        return False, repo_path


from pydantic import BaseModel

class BuildUrlsRequest(BaseModel):
    build_ids: List[str]



def generate_build_id():
    """Generate a short, unique build ID"""
    # Get current timestamp in milliseconds (last 4 digits)
    timestamp = str(int(time.time() * 1000))[-4:]
    # Generate 2 random characters
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=2))
    # Combine to create a unique ID
    return f"b{timestamp}{random_chars}"

@router.post("/clone-nd-build")
async def import_codebase(request: CodebaseImportRequest, upstream=False, current_user=Depends(get_current_user)):
    try:
        project_id = request.project_id
        user_id = current_user.get("cognito:username")
        
        
        document = {
            'project_id': project_id,
            'created_at': datetime.now().isoformat(),
        }
        
        mongo_handler = get_mongo_db(
            db_name=settings.MONGO_DB_NAME,
            collection_name='project_repositories'
        )

        # Get existing document
        existing_doc = await mongo_handler.get_one(
            filter={'project_id': project_id},
            db=mongo_handler.db
        )
        
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(root_dir, 'data', get_tenant_id(), str(project_id))
        
        repositories = []
        tasks = []
        
        for repo_data in request.repositories:
            _git_url = f"https://github.com/{repo_data.repo_name}.git"
            build_session_id = uuid.uuid4().hex
            repository = {
                "service": "github",
                'repo_id': repo_data.repo_id,
                "repository_name": repo_data.repo_name,
                "associated": repo_data.associated,
                'git_url': _git_url,
                "repositoryStatus": "initialized",
                "clone_url_ssh": f"git@github.com:{repo_data.repo_name}.git",
                'repo_type': repo_data.repo_type,
                'branches': [
                    {
                        'name': repo_data.branch_name,
                        'latest_commit_hash': None,
                        'builds': {
                            'build_id': generate_build_id(),
                            'build_session_id':  build_session_id,
                            'path': data_dir + "/" + repo_data.repo_name,
                            'kg_creation_status': 1,
                            'build_info': {
                                'start_time': None,
                                'end_time': None,
                                'duration_seconds': None
                            },
                            'last_updated': datetime.now().isoformat(),
                            'user_id': user_id,
                            'error': None
                        }
                    }
                ]
            }
            repositories.append(repository)
            
            # Schedule individual task for each repository
            task = Task.schedule_task(
                clone,
                project_id=project_id,
                build_session_id=build_session_id,
                data_dir=data_dir,
                repository=repository,  # Pass single repository instead of list
                tenant_id=get_tenant_id(),
                current_user=user_id, 
                upstream=upstream
            )
            tasks.append(task.to_dict())

        # Update MongoDB with all repositories
        if existing_doc:
            existing_repos = existing_doc.get('repositories', [])
            for new_repo in repositories:
                repo_exists = False
                for existing_repo in existing_repos:
                    if existing_repo['git_url'].lower() == new_repo['git_url'].lower():
                        repo_exists = True
                        new_branch = new_repo['branches'][0]
                        branch_exists = False
                        for existing_branch in existing_repo['branches']:
                            if existing_branch['name'] == new_branch['name']:
                                branch_exists = True
                                break
                        if not branch_exists:
                            existing_repo['branches'].append(new_branch)
                        break
                if not repo_exists:
                    existing_repos.append(new_repo)
            repositories = existing_repos

        document['repositories'] = repositories

        await mongo_handler.update_one(
            filter={'project_id': project_id},
            element=document,
            upsert=True,
            db=mongo_handler.db
        )

        return {
            "status": "success",
            "message": f"Scheduled {len(tasks)} repository tasks successfully",
            "tasks": tasks,
            "data": document
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to import codebase: {str(e)}"
        )

@router.post("/do-sync-the-repo")
async def do_sync_the_repo(project_id: int, build_id: str, current_user=Depends(get_current_user)):
    user_id = current_user.get("cognito:username")
    tasks = []
    build_session_id = uuid.uuid4().hex
    
    # Schedule individual task for each repository
    task = Task.schedule_task(
        upstream,
        project_id=project_id,
        build_session_id=build_session_id,
        build_id=build_id,
        tenant_id=get_tenant_id(),
        current_user=user_id, 
    )
    tasks.append(task.to_dict())
    
    return {
        'Upstream': tasks,
        'build_session_id': build_session_id
    }
            

async def check_pr_existence(git_url: str, pr_details: dict, github_token: str = None) -> bool:
    
    print(pr_details)
    print(git_url)
    print(github_token)
    
    """Check if PR still exists on GitHub"""
    try:
        if not pr_details or not pr_details.get('pr_number'):
            return False
            
        g = Github(github_token) if github_token else Github()
        
        # Extract owner and repo name from git URL
        parsed_url = urlparse(git_url)
        path_parts = parsed_url.path.strip('/').split('/')
        repo_owner = path_parts[0]
        repo_name = path_parts[1].replace('.git', '')
        
        repo = g.get_repo(f"{repo_owner}/{repo_name}")
        try:
            pr = repo.get_pull(pr_details['pr_number'])
            
            print("PR STATE : ", pr)
            return pr.state == 'open'  # Returns False if PR is closed or merged
        except:
            return False
            
    except Exception as e:
        logging.error(f"Error checking PR existence: {str(e)}")
        return False
    
    
@router.get("/kg-info/{project_id}")
async def get_kg_status(
    project_id: int,
    associate: bool = False,
    current_user=Depends(get_current_user)
):
    mongo_handler = get_mongo_db(
        db_name=settings.MONGO_DB_NAME,
        collection_name='project_repositories'
    )

    _result = await mongo_handler.get_one(
        filter={
            'project_id': project_id,
        },
        db=mongo_handler.db
    )
        
    if not _result:
        raise HTTPException(status_code=200, detail="No repository is found")
    
    repositories = _result.get('repositories', [])
    if associate:
        repositories = [repo for repo in repositories if repo.get('associated') is True]
    
    user_github = get_mongo_db(
        db_name=settings.MONGO_DB_NAME,
        collection_name='users_github'
    )
    github_token = None
    try:
        user_data = await user_github.git_get_by_user_id(current_user.get("cognito:username"))
        if user_data:
            github_token = user_data["access_token"]
    except Exception as e:
        logging.warning(f"Failed to get GitHub token: {str(e)}")

    updates_needed = []

    for repo in repositories:
        git_url = repo.get('git_url')
        repo_type = repo.get('repo_type')

        if repo_type == "private":
            for branch in repo.get('branches', []):
                branch_name = branch.get('name')
                print("-------------------------------------------")
                
                # Check commit status
                if branch.get('upstream') is True:
                    continue
                    
                try: 
                    repo_path = branch.get('builds', {}).get('path')
                    os.chdir(repo_path)
                
                    # Get list of commits using git log
                    command = f"git log {branch_name} --format=%H"
                    result = os.popen(command).read().strip()
                    
                    # Split into list of commit hashes
                    local_commits = result.split('\n') if result else []

                    remote_latest_commit = get_latest_commit_hash(
                                git_url=git_url,
                                branch_name=branch['name'],
                                github_token=github_token if repo_type == 'private' else None
                            )
                    
                    if remote_latest_commit and local_commits:
                        
                        # Check if remote commit exists in local commits
                        is_up_to_date = remote_latest_commit in local_commits
                        print(local_commits)
                        
                        if is_up_to_date:
                            branch['upstream'] = False
                        else:
                            branch['upstream'] = is_up_to_date
                            branch['latest_commit_hash'] = remote_latest_commit
                            # Prepare database update
                            filter_query = {
                                "project_id": project_id,
                                "repositories.git_url": git_url,
                                "repositories.branches.name": branch['name']
                            }
                            
                            update = {
                                "repositories.$[repo].branches.$[branch].upstream": True,
                                "repositories.$[repo].branches.$[branch].latest_commit_hash": remote_latest_commit
                            }
                            
                            array_filters = [
                                {"repo.git_url": git_url},
                                {"branch.name": branch['name']}
                            ]

                            updates_needed.append({
                                "filter": filter_query,
                                "update": update,
                                "array_filters": array_filters
                            })
                        
                            
                except Exception as e:
                    logging.error(f"Error checking commit hash for {git_url} branch {branch['name']}: {str(e)}")
                    continue

    # Perform all updates in MongoDB
    for update_info in updates_needed:
        try:
            await mongo_handler.update_with_nested_object_and_filters(
                filter=update_info["filter"],
                update=update_info["update"],
                array_filters=update_info["array_filters"]
            )
        except Exception as e:
            logging.error(f"Error updating MongoDB: {str(e)}")

    return {
        "details": repositories,  # This will now contain all updated PR and commit information
        "created_at": _result.get('created_at')
    }
    
from fastapi import Response
from fastapi.responses import StreamingResponse
import asyncio
import json

@router.get("/kg-sessions/{project_id}")
async def get_kg_sessions(
    project_id: int,
    current_user=Depends(get_current_user)
) -> StreamingResponse:
    async def event_generator():
        try:
            # Get the session collection
            session_handler = get_mongo_db(
                db_name=settings.MONGO_DB_NAME,
                collection_name='kg_sessions'
            )
            
            # Get initial sessions data
            sessions = await session_handler.get_latest(
                filter={'project_id': project_id},
                db=session_handler.db
            )
            
            if not sessions:
                yield "data: " + json.dumps({
                    "status": "no_sessions",
                    "message": "No knowledge graph sessions found for this project",
                    "sessions": []
                }) + "\n\n"
                return

            # Send initial session IDs and status
            formatted_sessions = []
            active_session_ids = []
            
            for session in sessions:
                session_data = {
                    "session_id": session["session_id"],
                    "status": session["session_status"],
                    "created_at": str(session["created_at"]),
                    "updated_at": str(session["updated_at"]),
                    "build_ids": session["build_ids"]
                }
                
                if session["session_status"] == "Progress":
                    active_session_ids.append(session["session_id"])
                    
                if session["session_status"] == "Failed" and "error" in session:
                    session_data["error"] = session["error"]
                    
                formatted_sessions.append(session_data)

            # Send initial data
            yield "data: " + json.dumps({
                "status": "success",
                "message": f"Found {len(formatted_sessions)} sessions",
                "sessions": formatted_sessions
            }) + "\n\n"

            # Continue streaming progress for active sessions
            while active_session_ids:
                completed_sessions = []
                
                for session_id in active_session_ids:
                    knowledge = Knowledge.getKnowledge(id=session_id)
                    if knowledge:
                        progress = knowledge.get_kg_progress()
                        
                        # Check if processing is complete
                        if progress["percentage_complete"] >= 100:
                            completed_sessions.append(session_id)
                            
                        yield "data: " + json.dumps({
                            "session_id": session_id,
                            "progress": {
                                "overall": {
                                    "total_files": progress["total_files"],
                                    "files_processed": progress["files_processed"],
                                    "percentage_complete": progress["percentage_complete"]
                                },
                                "by_codebase": progress["progress_by_codebase"]
                            }
                        }) + "\n\n"
                
                # Remove completed sessions
                active_session_ids = [sid for sid in active_session_ids 
                                    if sid not in completed_sessions]
                
                # Wait before next update
                await asyncio.sleep(2)  # Adjust interval as needed

        except Exception as e:
            logging.error(f"Error in streaming KG sessions: {str(e)}")
            yield "data: " + json.dumps({
                "status": "error",
                "message": f"Error streaming knowledge graph sessions: {str(e)}"
            }) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
       
@router.post("/create-tmp-files")
async def create_tmp_files():
    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(root_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        # Create two tmp files with different content
        tmp_files = {
            'tmp1.txt': 'This is temporary file 1',
            'tmp2.txt': 'This is temporary file 2'
        }
        
        created_files = []
        for filename, content in tmp_files.items():
            file_path = os.path.join(data_dir, filename)
            with open(file_path, 'w') as f:
                f.write(content)
            
            file_info = {
                "filename": filename,
                "path": file_path,
                "size": os.path.getsize(file_path),
                "created_at": datetime.fromtimestamp(os.path.getctime(file_path)).isoformat()
            }
            created_files.append(file_info)
        
        return {
            "message": "Created two temporary files successfully",
            "files": created_files
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating temporary files: {str(e)}")
