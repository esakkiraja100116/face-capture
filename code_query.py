import asyncio
from asyncio import subprocess
from datetime import datetime
import hashlib
import os
from typing import List
import uuid
from git import Repo
from urllib.parse import urlparse
import logging
import shutil

from github import Github
from app.core.Settings import settings
from fastapi import HTTPException

from app.connection.establish_db_connection import get_mongo_db
from app.core.websocket.client import WebSocketClient
from app.utils.kg_build.import_codebase import get_latest_commit_hash
from app.utils.kg_inspect.knowledge import Knowledge, KnowledgeCodeBase
from app.utils.kg_inspect.knowledge_helper import Knowledge_Helper
from app.utils.kg_inspect.knowledge_reporter import Reporter

class KnowledegeBuild:

    
    async def generate_unique_hash(self) -> str:
        """Generate a unique hash for branch name"""
        # Combine current timestamp and UUID for uniqueness
        unique_string = f"{datetime.utcnow().timestamp()}-{uuid.uuid4()}"
        # Create SHA-1 hash and take first 7 characters
        hash_object = hashlib.sha1(unique_string.encode())
        return hash_object.hexdigest()[:7]

    async def create_pull_request(self, github_token: str, repo_owner: str, repo_name: str, branch_name: str, base_branch: str, project_id, build_ids) -> dict:
        
        logging.info(github_token)
        
        """Create a pull request using GitHub API"""
        try:
            g = Github(github_token)
            repo = g.get_repo(f"{repo_owner}/{repo_name}")
            
            pr = repo.create_pull(
                title="Knowledge Graph Creation Updates",
                body="This PR contains updates from the knowledge graph creation process.",
                head=branch_name,
                base=base_branch
            )
            if (pr):
            
                return {
                    "pr_url": pr.html_url,
                    "pr_number": pr.number,
                    "branch_name": branch_name
                }
            else:
                await self.update_kg_status(-1, project_id, build_ids)
                
        except Exception as e:
            
            await self.update_kg_status(-1, project_id, build_ids)
            # Log the error to MongoDB
            error_handler = get_mongo_db(
                db_name=settings.MONGO_DB_NAME,
                collection_name='kg_errors'  # New collection for errors
            )
            
            error_data = {
                "error_type": "pr_creation_error",
                "error_message": str(e),
                "repository": f"{repo_owner}/{repo_name}",
                "branch_name": branch_name,
                "timestamp": datetime.utcnow(),
                "status_code": 403 if "forbidden" in str(e).lower() else None,
                "retry_count": 0  # Track number of retries
            }
            
            await error_handler.insert(error_data, error_handler.db)
            
            logging.error(f"Error creating PR: {str(e)}")
            raise e

    async def update_pr_details(self, project_id: int, build_id: str, pr_details: dict):
        """Update PR details in database"""
        kg_handler = get_mongo_db(
            db_name=settings.MONGO_DB_NAME,
            collection_name='project_repositories'
        )

        filter = {
            "project_id": project_id,
            "repositories.branches.builds.build_id": build_id
        }
        
        update = {
            f"repositories.$[].branches.$[branch].builds.pr_created": 1,
            f"repositories.$[].branches.$[branch].builds.pr_details": pr_details
        }
            
        array_filters = [
            {"branch.builds.build_id": build_id}
        ]

        result = await kg_handler.update_with_nested_object_and_filters(
            filter=filter,
            update=update,
            array_filters=array_filters,
            db=kg_handler.db
        )

        print(f"Modified {result.modified_count} documents")

    async def create_and_push_branch(self, repo_path: str, base_branch: str) -> tuple[str, bool]:
        """Create new branch and push changes"""
        try:
            # Generate unique hash for branch name
            unique_hash = await self.generate_unique_hash()
            new_branch = f"knowledge-creation-{unique_hash}"
            
            os.chdir(repo_path)
            
            # Create and checkout new branch
            os.system(f'git checkout -b {new_branch}')
            
            # Add and commit changes
            os.system('git add .')
            os.system('git commit -m "Knowledge graph creation completed"')
            
            # Try pushing the branch
            push_result = os.system(f'git push -u origin {new_branch}')
            
            return new_branch, push_result == 0
            
        except Exception as e:
            logging.error(f"Error in branch operations: {str(e)}")
            return None, False
        finally:
            os.chdir(os.path.dirname(os.path.dirname(repo_path)))
        
    async def check_pr_status(self, project_id: int, repo_name: str) -> bool:
        """Check if there's any pending PR for this repository"""
        kg_handler = get_mongo_db(
            db_name=settings.MONGO_DB_NAME,
            collection_name='project_repositories'
        )
        
        result = await kg_handler.get_one(
            {"project_id": project_id, "repositories.repository_name": repo_name},
            kg_handler.db
        )
        
        if result:
            for repo in result.get('repositories', []):
                if repo['repository_name'] == repo_name:
                    for branch in repo.get('branches', []):
                        if branch.get('builds', {}).get('pr_created') == 1:
                            return True
        return False

    async def try_to_commit(self, repo_path: str, branch_name: str) -> bool:
        """Attempt to push changes and return whether direct push is allowed"""
        try:
           # Change directory to build path
            os.chdir(repo_path)
            os.system(f'git switch {branch_name}')
            # Add and commit changes
            os.system('git add .')
            os.system('git commit -m "Knowledge graph creation completed"')
            # os.system('git push')
            
            return True
        
        except Exception as e:
            logging.error("Error getting latest commit hash: %s", str(e))
            return False            
            
    async def clone_repository(self, repo_path: str, git_url: str, token: str = None, branch: str = None) -> bool:
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
    
    async def update_kg_status(self, status: int, project_id: int, build_ids: list, session_id=None, upstream=False):
        
        for build_id in build_ids:
            kg_handler = get_mongo_db(
                db_name=settings.MONGO_DB_NAME,
                collection_name='project_repositories'
            )

            filter = {
                "project_id": project_id,
                "repositories.branches.builds.build_id": build_id
            }
            
            update = {
                f"repositories.$[].branches.$[branch].builds.kg_creation_status": status
            }
            
            if session_id is not None:
                update[f"repositories.$[].branches.$[branch].builds.build_session_id"] = session_id

            if upstream is not None:
                update[f"repositories.$[].branches.$[branch].upstream"] = upstream
                
            array_filters = [
                {"branch.builds.build_id": build_id}
            ]

            result = await kg_handler.update_with_nested_object_and_filters(
                filter=filter,
                update=update,
                array_filters=array_filters
            )

            print(f"Modified {result.modified_count} documents")
    
    async def update_commit_hash(self, hash: str, project_id: int, build_id: str):
        
        kg_handler = get_mongo_db(
            db_name=settings.MONGO_DB_NAME,
            collection_name='project_repositories'
        )

        filter = {
            "project_id": project_id,
            "repositories.branches.builds.build_id": build_id
        }
        
        update = {
            f"repositories.$[].branches.$[branch].latest_commit_hash": hash
        }
            
        array_filters = [
            {"branch.builds.build_id": build_id}
        ]

        result = await kg_handler.update_with_nested_object_and_filters(
            filter=filter,
            update=update,
            array_filters=array_filters
        )
        logging.info("Updated")
        print(f"Modified {result.modified_count} documents")
        
    async def update_kg_status_by_id(self, status: int, project_id: int, build_id: str, session_id=None, upstream=False):
        
        kg_handler = get_mongo_db(
            db_name=settings.MONGO_DB_NAME,
            collection_name='project_repositories'
        )

        filter = {
            "project_id": project_id,
            "repositories.branches.builds.build_id": build_id
        }
        
        update = {
            f"repositories.$[].branches.$[branch].builds.kg_creation_status": status
        }
        
        if session_id is not None:
            update[f"repositories.$[].branches.$[branch].builds.build_session_id"] = session_id

        if upstream is not None:
            update[f"repositories.$[].branches.$[branch].upstream"] = upstream
            
        array_filters = [
            {"branch.builds.build_id": build_id}
        ]

        result = await kg_handler.update_with_nested_object_and_filters(
            filter=filter,
            update=update,
            array_filters=array_filters
        )

        print(f"Modified {result.modified_count} documents")

    async def build_knowledge_graph(self, reporter, session_id: str, user_id: str, project_id: int, repo: dict, build_ids: list, codebases):
        try:
            logging.info("Before the knowledge class")
            reporter.send_message("code_ingestion", { 'status': 'checking for status'})
            
            # Store session information
            session_handler = get_mongo_db(
                db_name=settings.MONGO_DB_NAME,
                collection_name='kg_sessions'
            )
            
            session_data = {
                "project_id": project_id,
                "session_id": session_id,
                "session_status": "Progress",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "build_ids": build_ids
            }
            
            await session_handler.insert(session_data, session_handler.db)
            
            # Initialize Knowledge_Helper with project-specific configuration
            knowledge_helper = await asyncio.to_thread(
                lambda: Knowledge_Helper(session_id, reporter, os.getcwd(), codebases, user_id, project_id)
            )
            
            knowledge = Knowledge.getKnowledge(id=session_id)
        
            # Start knowledge processing
            await asyncio.to_thread(knowledge.start)
            await self.update_kg_status(1, project_id, build_ids, session_id)
            
            while(True):
                logging.info("Knowledge information: ", knowledge.get_kg_progress())
                logging.info(f"Knowledge state: {knowledge._state}")
                if knowledge._state != 2:
                    reporter.send_message("code_ingestion", knowledge.get_kg_progress())
                
                if knowledge._state == 2:
                    
                    if repo['repo_type'] == "private":
                        for branch in repo['branches']:
                            if branch['builds']['build_id'] in build_ids:
                                build_path = branch['builds']['path']
                                if build_path:
                                    # Try direct push first
                                    
                                    can_push = await self.try_to_commit(build_path, branch['name'])
                                    if can_push:
                                        
                                        # Update commit hash
                                        os.chdir(build_path)
                                        _hash = os.popen('git rev-parse HEAD').read().strip()
                                        await self.update_commit_hash(_hash, project_id, branch['builds']['build_id'])
                                        logging.info(f"Successfully pushed changes to {repo['repository_name']}")
                                        await self.update_kg_status(2, project_id, build_ids)
                                    else:
                                        logging.info("Error while pushing the code")
                                        await self.update_kg_status(-1, project_id, build_ids)

                    else:
                        
                        await self.update_kg_status(2, project_id, build_ids)
                        reporter.send_message("code_ingestion", knowledge.get_kg_progress())
                                    
                    # Update session status to completed
                    await session_handler.update_one(
                        filter={"session_id": session_id},
                        element={
                            "session_status": "Completed",
                            "updated_at": datetime.utcnow()
                        },db=session_handler.db
                    )
                    print("Knowledge ingestion completed. Exiting...")
                    break
                
                await asyncio.sleep(1)
            
            # Cleanup after completion
            Knowledge_Helper.cleanup(str(session_id))
            
            logging.info("Knowledge graph generation completed successfully")
            
        except Exception as e:
            # Update session status to failed
            await session_handler.update_one(
                        filter={"session_id": session_id},
                        element={
                            "session_status": "Failed",
                            "updated_at": datetime.utcnow()
                        },db=session_handler.db
                    )
            logging.error(f"Error in build_knowledge_graph: {str(e)}")

    def check_existing_knowledge_graph(self, repo_path: str) -> bool:
        """
        Check if knowledge graph already exists for repository
        
        Args:
            repo_path: Path to repository directory

        Returns:
            bool: True if knowledge graph exists, False otherwise
        """
        try:
            knowledge_path = os.path.join(repo_path, '.knowledge')
            return os.path.exists(knowledge_path) and os.path.isdir(knowledge_path)
        except Exception as e:
            logging.error(f"Error checking knowledge graph: {str(e)}")
            return False

    async def get_branch_details_by_build_id(self, project_id: int, build_id: str) -> dict:
        """
        Retrieve branch name and path for a given build_id
        
        Args:
            project_id: The ID of the project
            build_id: The ID of the build
            
        Returns:
            dict: Contains branch_name and path, or None if not found
        """
        try:
            kg_handler = get_mongo_db(
                db_name=settings.MONGO_DB_NAME,
                collection_name='project_repositories'
            )
            
            # Query to find the specific project and build_id
            result = await kg_handler.get_one(
                {
                    "project_id": project_id,
                    "repositories.branches.builds.build_id": build_id
                },
                kg_handler.db
            )
            
            print(result)
            
            if result and 'repositories' in result:
                for repo in result['repositories']:
                    if 'branches' in repo:
                        for branch in repo['branches']:
                            if 'builds' in branch and branch['builds'].get('build_id') == build_id:
                                return {
                                    "branch_name": branch['name'],
                                    "path": branch['builds']['path']
                                }
            
            return None
            
        except Exception as e:
            logging.error(f"Error retrieving branch details: {str(e)}")
            return None
    
    async def upstream(self, project_id,build_session_id,  build_id, user_id):
        branch_details = await self.get_branch_details_by_build_id(project_id=project_id, build_id=build_id)

        branch_name = branch_details["branch_name"]
        build_path = branch_details["path"]
            
        ws_client = WebSocketClient(build_session_id, settings.WEBSOCKET_URI)
        reporter = Reporter(ws_client)
        reporter.send_message("code_ingestion", {
            'status': 'updating'
        }) 
        
        # Change directory to the build path
        os.chdir(build_path)

        try:
            logging.info("Try to switch to Build Path  ") 
            # Change directory to the build path
            os.chdir(build_path)
            # Switch to the specified branch
            os.system(f'git switch {branch_name}')
            # Pull latest changes
            os.system('git pull --rebase')            
            # Return to original directory
            os.chdir(os.path.dirname(os.path.dirname(build_path)))
            
            codebases = [KnowledgeCodeBase(build_path,build_id)]
            
            
            knowledge_helper = await asyncio.to_thread(
                lambda: Knowledge_Helper(build_session_id, reporter, os.getcwd(), codebases, user_id, project_id)
            )
            
            knowledge = Knowledge.getKnowledge(id=build_session_id)
        
            # Start knowledge processing
            await asyncio.to_thread(knowledge.start)
            await self.update_kg_status(1, project_id, [build_id], build_session_id)
            
            while(True):
                logging.info("Knowledge information: ", knowledge.get_kg_progress())
                logging.info(f"Knowledge state: {knowledge._state}")
                if knowledge._state != 2:
                    reporter.send_message("code_ingestion", knowledge.get_kg_progress())
                
                if knowledge._state == 2:
                    logging.info(f"Knowlege updation completed")
                    can_push = await self.try_to_commit(build_path, branch_name)
                    if can_push:
                        
                        # Update commit hash
                        os.chdir(build_path)
                        _hash = os.popen('git rev-parse HEAD').read().strip()
                        logging.info(_hash)
                        await self.update_commit_hash(_hash, project_id,build_id)
                        await self.update_kg_status(2, project_id, [build_id])
                    else:
                        logging.info("Error while pushing the code")
                        await self.update_kg_status(-1, project_id, [build_id])
                    
                    reporter.send_message("code_ingestion", knowledge.get_kg_progress())
                    break
                
                await asyncio.sleep(1)
            
            
            logging.info("Knowledge graph generation completed successfully")
            
        except Exception as e:
            logging.error(f"Error during git operations: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to update repository: {str(e)}")

    async def build(
        self,
        reporter,
        build_session_id: str,
        build_ids: List,
        project_id: int,
        repo: dict,
        user_id: str,
    ):
        logging.info("Build Started ")
        project_repositories = get_mongo_db(
            db_name=settings.MONGO_DB_NAME,
            collection_name='project_repositories'
        )
        
        codebase = []
        skipped_builds = []
        for branch in repo['branches']:
            if branch['builds']['build_id'] in build_ids:
                build_path = branch['builds']['path']
                if build_path:
                    
                    codebase.append(KnowledgeCodeBase(build_path, branch.get('builds', {}).get('build_id')))
                            
        print(codebase)
        logging.info(codebase)
        
        if not codebase:
            return {
                "status": "skipped",
                "message": "All requested builds already have knowledge graphs",
                "data": {"skipped_builds": skipped_builds}
            }
                    
        try:
            logging.info("Build Started - 2")
            await self.build_knowledge_graph(
                reporter=reporter,
                session_id=build_session_id,
                user_id=user_id,
                repo=repo,
                project_id=project_id,
                codebases=codebase,
                build_ids=build_ids,
            )
        except Exception as e:
            await self.update_kg_status(-1, project_id, build_ids)
            logging.error(f"Background task failed: {str(e)}")
        
        return {
            "message": "Knowledge graph generation started",
            "build_session_id": build_session_id
        }

    async def clone(self, project_id, build_session_id, user_id, data_dir, repositories, upstream):

        ws_client = WebSocketClient(build_session_id, settings.WEBSOCKET_URI)
        reporter = Reporter(ws_client)
        reporter.send_message("code_ingestion", {
            'status': 'cloning'
        })    

        build_ids = []
        ready_to_build = False
        
        # Now repositories contains only one repository
        repo = repositories[0]
        branch_name = repo['branches'][0]['name']
        _url = repo['git_url']
        checking_path = os.path.join(data_dir, repo['repository_name'])
        
        for branch in repo['branches']:
            branch_name = branch['name']
            build_ids.append(branch['builds']['build_id'])
                                
            if not os.path.exists(checking_path):
                try:
                    os.makedirs(data_dir, exist_ok=True)
                    os.makedirs(os.path.dirname(checking_path), exist_ok=True)
                    
                    branch['builds']['path'] = checking_path
                    
                    if repo['repo_type'] == 'private':
                        
                        user_github = get_mongo_db(
                            db_name=settings.MONGO_DB_NAME,
                            collection_name='users_github'
                        )
                        
                        print(user_id)
                        user_data = await user_github.git_get_by_user_id(user_id)
                        if not user_data:
                            raise HTTPException(status_code=404, detail="GitHub token not found. Please login again.")
                        
                        print(user_data)
                        github_token = user_data["access_token"]
                        latest_commit_hash = get_latest_commit_hash(_url, branch_name, github_token)
                        clone_successful, _path = await self.clone_repository(checking_path, _url, token=github_token, branch=branch_name)
                    else:
                        latest_commit_hash = get_latest_commit_hash(_url, branch_name)
                        clone_successful, _path = await self.clone_repository(checking_path, _url, branch=branch_name)

                    if clone_successful:
                        logging.info("Clone Successful")
                        await self.update_commit_hash(latest_commit_hash, project_id, branch['builds']['build_id'])
                        ready_to_build = True
                        os.rename(_path, checking_path)
                        branch['builds']['path'] = checking_path
                    else:          
                        await self.update_kg_status_by_id(-1, project_id, branch['builds']['build_id'])
                        if os.path.exists(_path):
                            shutil.rmtree(_path)
                        repo_folder = os.path.dirname(os.path.dirname(_path))
                        if os.path.exists(repo_folder):
                            shutil.rmtree(repo_folder)
                                
                except Exception as e:
                    await self.update_kg_status_by_id(-1, project_id, branch['builds']['build_id'])
                    logging.error(f"Failed to clone repository {_url} branch {branch_name}: {str(e)}")
                    
            else:
                os.makedirs(data_dir, exist_ok=True)
                os.makedirs(os.path.dirname(checking_path), exist_ok=True)
                ready_to_build = True
                    
        if ready_to_build: 
        
            reporter.send_message("code_ingestion", {
                'status': 'building'
            }) 
            logging.info(await self.build(reporter, build_session_id, build_ids, project_id, repo, user_id))
                