"""
Multi-agent coordinator system for Cortex IDE.

Ported from Claude Code's coordinatorMode.ts.
Manages the full coordination workflow:
- Worker spawning and lifecycle
- Vision-first sequential coordination
- Parallel research workers
- Result aggregation and synthesis
- Scratchpad-based cross-worker communication

This is the main orchestration engine that connects:
- coordinator_prompt.py (system prompts)
- agent_context.py (worker prompt building)
- agent_tools.py (tool filtering)
- image_processing.py (image pipeline)
"""

import logging
import json
import time
import threading
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Tuple
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

from src.utils.agent_tools import (
    AgentType, AgentStatus, AgentTask,
    AgentLifecycleManager,
    build_worker_system_prompt,
    should_use_parallel,
    classify_task_type,
    get_tools_description_for_agent,
)

log = logging.getLogger("coordinator_system")


# ==================== SCRATCHPAD (from coordinatorMode.ts) ====================

class Scratchpad:
    """Cross-worker communication via shared directory.
    
    Ported from Claude Code's scratchpad directory concept.
    Workers can write results to the scratchpad for other workers
    or the coordinator to read.
    """
    
    def __init__(self, session_id: str, base_dir: str = None):
        self.session_id = session_id
        if base_dir:
            self._dir = Path(base_dir) / ".cortex" / "scratchpad" / session_id
        else:
            self._dir = Path(tempfile.gettempdir()) / "cortex_scratchpad" / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
    
    @property
    def path(self) -> str:
        return str(self._dir)
    
    def write(self, key: str, data: Any, worker_id: str = "coordinator"):
        """Write data to scratchpad."""
        with self._lock:
            filepath = self._dir / f"{key}.json"
            payload = {
                "key": key,
                "worker_id": worker_id,
                "timestamp": time.time(),
                "data": data,
            }
            filepath.write_text(json.dumps(payload, indent=2), encoding='utf-8')
            log.info(f"[Scratchpad] {worker_id} wrote '{key}'")
    
    def read(self, key: str) -> Optional[Any]:
        """Read data from scratchpad."""
        filepath = self._dir / f"{key}.json"
        if filepath.exists():
            try:
                payload = json.loads(filepath.read_text(encoding='utf-8'))
                return payload.get("data")
            except Exception as e:
                log.warning(f"[Scratchpad] Failed to read '{key}': {e}")
        return None
    
    def read_all(self) -> Dict[str, Any]:
        """Read all scratchpad entries."""
        results = {}
        for filepath in self._dir.glob("*.json"):
            try:
                payload = json.loads(filepath.read_text(encoding='utf-8'))
                results[payload.get("key", filepath.stem)] = payload.get("data")
            except Exception:
                continue
        return results
    
    def cleanup(self):
        """Clean up scratchpad directory."""
        try:
            import shutil
            if self._dir.exists():
                shutil.rmtree(self._dir, ignore_errors=True)
        except Exception as e:
            log.warning(f"[Scratchpad] Cleanup failed: {e}")


# ==================== VISION CONTEXT STORE ====================

class VisionContextStore:
    """Stores vision analysis results for cross-agent access.
    
    When Vision Agent completes, its results are stored here so
    the Main Agent and other workers can access them.
    """
    
    def __init__(self):
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def store(self, session_id: str, context: Dict[str, Any]):
        """Store vision context for a session."""
        with self._lock:
            self._contexts[session_id] = {
                "timestamp": time.time(),
                "context": context,
            }
            log.info(f"[VisionStore] Stored context for session {session_id}")
    
    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get vision context for a session."""
        entry = self._contexts.get(session_id)
        if entry:
            return entry["context"]
        return None
    
    def has_context(self, session_id: str) -> bool:
        """Check if vision context exists for session."""
        return session_id in self._contexts
    
    def clear(self, session_id: str):
        """Clear vision context for a session."""
        self._contexts.pop(session_id, None)


# Singleton vision store
_vision_store = VisionContextStore()

def get_vision_store() -> VisionContextStore:
    return _vision_store


# ==================== COORDINATION ENGINE ====================

@dataclass
class CoordinationResult:
    """Result from a coordination workflow."""
    success: bool
    response: str
    vision_context: Optional[Dict[str, Any]] = None
    worker_results: List[Dict[str, Any]] = field(default_factory=list)
    total_duration: float = 0.0
    mode: str = "single"


class CoordinationEngine:
    """Main multi-agent coordination engine.
    
    Orchestrates the full workflow from Claude Code's coordinatorMode.ts:
    1. Classify task and determine execution strategy
    2. Spawn appropriate workers (sequential or parallel)
    3. Collect and aggregate results
    4. Synthesize into final response
    
    Usage:
        engine = CoordinationEngine(project_path="/path/to/project")
        result = engine.coordinate(
            text="Fix the null pointer error",
            images=[...],
            mode="performance",
            call_llm=my_llm_function
        )
    """
    
    def __init__(self, project_path: str = None, session_id: str = None):
        self.project_path = project_path
        self.session_id = session_id or str(int(time.time()))
        self.lifecycle = AgentLifecycleManager()
        self.scratchpad = Scratchpad(self.session_id, project_path)
        self.vision_store = get_vision_store()
    
    def coordinate(
        self,
        text: str,
        images: List[Dict[str, Any]] = None,
        mode: str = "performance",
        call_vision: Callable = None,
        call_llm: Callable = None,
        get_code_context: Callable = None,
    ) -> CoordinationResult:
        """Execute full coordination workflow.
        
        Args:
            text: User's message
            images: List of image dicts (from frontend)
            mode: Performance mode (efficient/auto/performance/ultimate)
            call_vision: Function to call vision API (images, text) -> str
            call_llm: Function to call main LLM (messages, model) -> str
            get_code_context: Function to get current code context
        
        Returns:
            CoordinationResult with aggregated response
        """
        start_time = time.time()
        images = images or []
        has_images = len(images) > 0
        
        log.info(f"[Coordinator] Starting coordination: mode={mode}, images={len(images)}")
        
        try:
            # Determine execution strategy
            use_parallel = should_use_parallel(has_images, text, mode)
            
            if has_images:
                # VISION WORKFLOW: Always sequential (vision first)
                result = self._coordinate_vision_workflow(
                    text, images, mode, call_vision, call_llm, get_code_context
                )
            elif mode == "ultimate" and use_parallel:
                # PARALLEL WORKFLOW: Multiple research workers
                result = self._coordinate_parallel_workflow(
                    text, mode, call_llm, get_code_context
                )
            else:
                # SEQUENTIAL WORKFLOW: Single or sequential multi-agent
                result = self._coordinate_sequential_workflow(
                    text, mode, call_llm, get_code_context
                )
            
            result.total_duration = time.time() - start_time
            result.mode = mode
            
            log.info(f"[Coordinator] Coordination complete: {result.total_duration:.1f}s, success={result.success}")
            return result
            
        except Exception as e:
            log.error(f"[Coordinator] Coordination error: {e}", exc_info=True)
            return CoordinationResult(
                success=False,
                response=f"Coordination error: {str(e)}",
                total_duration=time.time() - start_time,
                mode=mode,
            )
        finally:
            # Cleanup
            self.lifecycle.cleanup()
    
    def _coordinate_vision_workflow(
        self,
        text: str,
        images: List[Dict[str, Any]],
        mode: str,
        call_vision: Callable,
        call_llm: Callable,
        get_code_context: Callable,
    ) -> CoordinationResult:
        """Vision-first sequential workflow.
        
        From Claude Code's coordinator:
        Step 1: Vision Agent analyzes image (MUST complete first)
        Step 2: Store vision context
        Step 3: Main Agent uses vision context to respond
        Step 4: Synthesize final response
        """
        worker_results = []
        
        # Step 1: Spawn Vision Agent
        vision_task = self.lifecycle.spawn_agent(
            AgentType.VISION_WORKER,
            f"Analyze the provided image(s). User query: {text}"
        )
        self.lifecycle.start_agent(vision_task.task_id)
        
        vision_context = None
        if call_vision:
            try:
                vision_result = call_vision(images, text)
                if isinstance(vision_result, dict):
                    vision_context = vision_result
                    self.lifecycle.complete_agent(vision_task.task_id, str(vision_result))
                elif isinstance(vision_result, str):
                    vision_context = {"description": vision_result, "ocr_text": "", "raw": vision_result}
                    self.lifecycle.complete_agent(vision_task.task_id, vision_result)
                else:
                    self.lifecycle.fail_agent(vision_task.task_id, "Invalid vision result type")
            except Exception as e:
                self.lifecycle.fail_agent(vision_task.task_id, str(e))
                log.error(f"[Coordinator] Vision agent failed: {e}")
        
        worker_results.append(vision_task.to_result_dict())
        
        # Step 2: Store vision context
        if vision_context:
            self.vision_store.store(self.session_id, vision_context)
            self.scratchpad.write("vision_analysis", vision_context, vision_task.task_id)
        
        # Step 3: Build enhanced prompt with vision context
        enhanced_prompt = self._build_enhanced_prompt(text, vision_context, get_code_context)
        
        # Step 4: Main Agent responds
        if call_llm:
            try:
                main_task = self.lifecycle.spawn_agent(
                    AgentType.CODE_WORKER,
                    f"Respond to user with vision context: {text}"
                )
                self.lifecycle.start_agent(main_task.task_id)
                
                response = call_llm(enhanced_prompt)
                self.lifecycle.complete_agent(main_task.task_id, response)
                worker_results.append(main_task.to_result_dict())
                
                return CoordinationResult(
                    success=True,
                    response=response,
                    vision_context=vision_context,
                    worker_results=worker_results,
                )
            except Exception as e:
                log.error(f"[Coordinator] Main agent failed: {e}")
                # Fallback: return vision analysis directly
                if vision_context:
                    raw = vision_context.get("raw", vision_context.get("description", ""))
                    return CoordinationResult(
                        success=True,
                        response=raw,
                        vision_context=vision_context,
                        worker_results=worker_results,
                    )
        
        return CoordinationResult(
            success=False,
            response="Vision analysis completed but no LLM available for response generation.",
            vision_context=vision_context,
            worker_results=worker_results,
        )
    
    def _coordinate_parallel_workflow(
        self,
        text: str,
        mode: str,
        call_llm: Callable,
        get_code_context: Callable,
    ) -> CoordinationResult:
        """Parallel worker workflow for Ultimate mode.
        
        Spawns multiple research/context workers in parallel,
        then synthesizes their results.
        """
        worker_results = []
        contexts = {}
        
        # Define parallel tasks
        def run_code_context():
            task = self.lifecycle.spawn_agent(AgentType.CONTEXT_WORKER, "Extract code context")
            self.lifecycle.start_agent(task.task_id)
            try:
                if get_code_context:
                    ctx = get_code_context()
                    self.lifecycle.complete_agent(task.task_id, ctx or "No context")
                    return {"type": "code", "data": ctx, "task": task}
                self.lifecycle.complete_agent(task.task_id, "No context function")
                return {"type": "code", "data": None, "task": task}
            except Exception as e:
                self.lifecycle.fail_agent(task.task_id, str(e))
                return {"type": "code", "data": None, "task": task}
        
        def run_project_context():
            task = self.lifecycle.spawn_agent(AgentType.CONTEXT_WORKER, "Extract project structure")
            self.lifecycle.start_agent(task.task_id)
            try:
                if self.project_path:
                    # Quick project summary
                    project_info = f"Project: {Path(self.project_path).name}\nPath: {self.project_path}"
                    self.lifecycle.complete_agent(task.task_id, project_info)
                    return {"type": "project", "data": project_info, "task": task}
                self.lifecycle.complete_agent(task.task_id, "No project path")
                return {"type": "project", "data": None, "task": task}
            except Exception as e:
                self.lifecycle.fail_agent(task.task_id, str(e))
                return {"type": "project", "data": None, "task": task}
        
        # Execute in parallel
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(run_code_context),
                executor.submit(run_project_context),
            ]
            
            for future in as_completed(futures, timeout=30):
                try:
                    result = future.result()
                    contexts[result["type"]] = result["data"]
                    worker_results.append(result["task"].to_result_dict())
                except Exception as e:
                    log.warning(f"[Coordinator] Parallel worker error: {e}")
        
        # Build enhanced prompt
        enhanced = self._build_enhanced_prompt(text, None, None, extra_contexts=contexts)
        
        # Call main LLM
        if call_llm:
            try:
                response = call_llm(enhanced)
                return CoordinationResult(
                    success=True,
                    response=response,
                    worker_results=worker_results,
                )
            except Exception as e:
                return CoordinationResult(
                    success=False,
                    response=f"LLM error: {str(e)}",
                    worker_results=worker_results,
                )
        
        return CoordinationResult(
            success=False,
            response="No LLM function provided.",
            worker_results=worker_results,
        )
    
    def _coordinate_sequential_workflow(
        self,
        text: str,
        mode: str,
        call_llm: Callable,
        get_code_context: Callable,
    ) -> CoordinationResult:
        """Simple sequential workflow for Performance mode (no images)."""
        enhanced = self._build_enhanced_prompt(text, None, get_code_context)
        
        if call_llm:
            try:
                response = call_llm(enhanced)
                return CoordinationResult(success=True, response=response)
            except Exception as e:
                return CoordinationResult(success=False, response=f"Error: {str(e)}")
        
        return CoordinationResult(success=False, response="No LLM function provided.")
    
    def _build_enhanced_prompt(
        self,
        text: str,
        vision_context: Optional[Dict[str, Any]] = None,
        get_code_context: Callable = None,
        extra_contexts: Dict[str, Any] = None,
    ) -> str:
        """Build enhanced prompt with all available context."""
        parts = []
        
        # Vision context
        if vision_context:
            parts.append("### Vision Analysis:")
            if isinstance(vision_context, dict):
                desc = vision_context.get("description", "")
                ocr = vision_context.get("ocr_text", "")
                raw = vision_context.get("raw", "")
                if desc:
                    parts.append(f"**Description:** {desc}")
                if ocr:
                    parts.append(f"**OCR Text:** {ocr}")
                if not desc and not ocr and raw:
                    parts.append(raw)
            else:
                parts.append(str(vision_context))
            parts.append("")
        
        # Code context
        if get_code_context:
            try:
                ctx = get_code_context()
                if ctx:
                    parts.append(f"### Code Context:\n{ctx}\n")
            except Exception:
                pass
        
        # Extra contexts from parallel workers
        if extra_contexts:
            for ctx_type, ctx_data in extra_contexts.items():
                if ctx_data:
                    parts.append(f"### {ctx_type.title()} Context:\n{ctx_data}\n")
        
        # User question
        parts.append(f"### User Question:\n{text}")
        
        return "\n".join(parts)
