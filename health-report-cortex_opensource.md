# Project Health Report - cortex_opensource

- Project root: `C:\Users\Hakeem1\OneDrive\Desktop\Cortex_Ai_Agent\cortex_project\cortex_opensource`
- Generated: 2026-10-22 16:29:50
- Scan: 7438.8 ms, full re-parse
- Files: 1311 - symbols: 5649 - import edges: 781

## Summary

| Status | Count |
|---|---|
| Broken | 0 |
| Warning | 0 |
| Affected | 0 |
| New | 1 |
| Healthy | 1310 |
| Unused symbols | 400 |
| Orphan files | 0 |
| Unimported modules | 0 |

## Broken (0 problem(s) in 0 file(s))

Nothing is broken. Every deeply-parsed file parsed cleanly.

## Warnings (0 problem(s) in 0 file(s))

No warnings.

## Affected files (0)

No healthy file depends on a broken one.

## Dead code (heuristic)

Unused symbols: 400 - orphan files: 0. A symbol is listed only when its name appears nowhere in ANY project text file (Python, JavaScript, HTML, CSS, config, build scripts) beyond its own definition lines. Test symbols, framework-invoked methods, dispatch-by-name methods (`visit_*` in an `ast.NodeVisitor` subclass, `handle_*` in an `HTMLParser` subclass) and file-object protocol methods are exempt. Dynamic loading and `import *` can still hide real usage, so review before deleting.

### Unused symbols (400 listed)

- function `regenerateSessionId` - `src/agent/src/app_state/state.py:40`
- function `setAdditionalDirectoriesForCortexMd` - `src/agent/src/app_state/state.py:114`
- function `getProjectRoot` - `src/agent/src/app_state/state.py:141`
- function `setProjectRoot` - `src/agent/src/app_state/state.py:145`
- function `setOriginalCwd` - `src/agent/src/app_state/state.py:153`
- function `setKairosActive` - `src/agent/src/app_state/state.py:161`
- function `getUserMsgOptIn` - `src/agent/src/app_state/state.py:165`
- function `setUserMsgOptIn` - `src/agent/src/app_state/state.py:169`
- method `DiffDataService.get_cached_diff_data` - `src/agent/src/hooks/use_diff_data.py:159`
- method `CrossProjectMemoryManager.get_global_memory_stats` - `src/agent/src/project_memory/cross_project_memory.py:444`
- method `CrossProjectMemoryManager.list_shared_projects` - `src/agent/src/project_memory/cross_project_memory.py:466`
- function `reset_cross_project_manager` - `src/agent/src/project_memory/cross_project_memory.py:515`
- function `consolidate_project_memories` - `src/agent/src/project_memory/memory_consolidation.py:513`
- method `SemanticMemorySearcher.format_results_for_prompt` - `src/agent/src/project_memory/semantic_search.py:444`
- function `stripProtoFields` - `src/agent/src/services/analytics/index.py:21`
- function `roughTokenCountEstimationForFileType` - `src/agent/src/services/token_estimation.py:54`
- function `register_batch_skill` - `src/agent/src/skills/bundled/batch.py:182`
- function `register_simplify_skill` - `src/agent/src/skills/bundled/simplify.py:103`
- function `registerBundledSkill` - `src/agent/src/skills/bundled_skills.py:71`
- function `getBundledSkills` - `src/agent/src/skills/bundled_skills.py:131`
- function `clearBundledSkills` - `src/agent/src/skills/bundled_skills.py:139`
- method `SkillsManager.remove_skill` - `src/agent/src/skills/cortex_skill.py:492`
- function `getSkillsPath` - `src/agent/src/skills/load_skills_dir.py:131`
- function `estimateSkillFrontmatterTokens` - `src/agent/src/skills/load_skills_dir.py:150`
- function `clearSkillCaches` - `src/agent/src/skills/load_skills_dir.py:849`
- function `onDynamicSkillsLoaded` - `src/agent/src/skills/load_skills_dir.py:874`
- function `discoverSkillDirsForPaths` - `src/agent/src/skills/load_skills_dir.py:891`
- function `addSkillDirectories` - `src/agent/src/skills/load_skills_dir.py:952`
- function `getDynamicSkills` - `src/agent/src/skills/load_skills_dir.py:1004`
- function `activateConditionalSkillsForPaths` - `src/agent/src/skills/load_skills_dir.py:1012`
- function `getConditionalSkillCount` - `src/agent/src/skills/load_skills_dir.py:1093`
- function `clearDynamicSkills` - `src/agent/src/skills/load_skills_dir.py:1100`
- function `getMCPSkillBuilders` - `src/agent/src/skills/mcp_skill_builders.py:47`
- method `RulesManager.get_rule` - `src/agent/src/skills/rules.py:174`
- method `RulesManager.add_rule` - `src/agent/src/skills/rules.py:178`
- method `RulesManager.remove_rule` - `src/agent/src/skills/rules.py:182`
- method `RulesManager.enable_rule` - `src/agent/src/skills/rules.py:189`
- method `RulesManager.disable_rule` - `src/agent/src/skills/rules.py:197`
- method `RulesManager.global_rules` - `src/agent/src/skills/rules.py:221`
- method `RulesManager.project_rules` - `src/agent/src/skills/rules.py:225`
- function `atomic_edit` - `src/agent/src/tools/file_edit_tool/file_edit_tool.py:263`
- function `verify_edit` - `src/agent/src/tools/file_edit_tool/file_edit_tool.py:288`
- method `FileEditTool.validate_input` - `src/agent/src/tools/file_edit_tool/file_edit_tool.py:695`
- method `FileReadTool.validate_input` - `src/agent/src/tools/file_read_tool/file_read_tool.py:1331`
- method `FileWriteTool.validate_input` - `src/agent/src/tools/file_write_tool/file_write_tool.py:481`
- method `GlobTool.validate_input` - `src/agent/src/tools/glob_tool/glob_tool.py:352`
- method `GrepTool.validate_input` - `src/agent/src/tools/grep_tool/grep_tool.py:950`
- method `SementicSearchTool.validate_input` - `src/agent/src/tools/sementic_search/sementic_search_tool.py:321`
- method `SementicSearchTool.is_large_codebase` - `src/agent/src/tools/sementic_search/sementic_search_tool.py:561`
- function `format_deferred_tool_line` - `src/agent/src/tools/tool_search_tool/prompt.py:24`
- function `generateProgressiveArgumentHint` - `src/agent/src/utils/argument_substitution.py:60`
- function `resetTrustDialogAcceptedCacheForTesting` - `src/agent/src/utils/config.py:344`
- function `_getConfigForTesting` - `src/agent/src/utils/config.py:1227`
- function `_wouldLoseAuthStateForTesting` - `src/agent/src/utils/config.py:1232`
- function `_setGlobalConfigCacheForTesting` - `src/agent/src/utils/config.py:1237`
- class `CortexError` - `src/agent/src/utils/errors.py:18`
- class `MalformedCommandError` - `src/agent/src/utils/errors.py:26`
- function `is_abort_error` - `src/agent/src/utils/errors.py:109`
- function `to_error` - `src/agent/src/utils/errors.py:171`
- function `get_errno_path` - `src/agent/src/utils/errors.py:245`
- function `short_error_stack` - `src/agent/src/utils/errors.py:265`
- function `classify_axios_error` - `src/agent/src/utils/errors.py:350`
- function `getGlobalGitignorePath` - `src/agent/src/utils/git/gitignore.py:48`
- function `logForTelemetry` - `src/agent/src/utils/log.py:63`
- method `SandboxManager.get_fs_read_config` - `src/agent/src/utils/sandbox/sandbox_adapter.py:110`
- method `SandboxManager.get_fs_write_config` - `src/agent/src/utils/sandbox/sandbox_adapter.py:121`
- method `SandboxManager.get_network_restriction_config` - `src/agent/src/utils/sandbox/sandbox_adapter.py:132`
- method `SandboxManager.get_allow_unix_sockets` - `src/agent/src/utils/sandbox/sandbox_adapter.py:136`
- method `SandboxManager.get_ignore_violations` - `src/agent/src/utils/sandbox/sandbox_adapter.py:140`
- method `SandboxManager.are_unsandboxed_commands_allowed` - `src/agent/src/utils/sandbox/sandbox_adapter.py:148`
- method `SandboxManager.wrap_with_sandbox` - `src/agent/src/utils/sandbox/sandbox_adapter.py:156`
- method `SandboxManager.isSandboxingEnabled` - `src/agent/src/utils/sandbox/sandbox_adapter.py:171`
- method `SandboxManager.isAutoAllowBashIfSandboxedEnabled` - `src/agent/src/utils/sandbox/sandbox_adapter.py:175`
- method `SandboxManager.setSandboxSettings` - `src/agent/src/utils/sandbox/sandbox_adapter.py:179`
- function `getManagedFileSettingsPresence` - `src/agent/src/utils/settings/settings.py:254`
- function `updateSettingsForSource` - `src/agent/src/utils/settings/settings.py:520`
- function `getManagedSettingsKeysForLogging` - `src/agent/src/utils/settings/settings.py:632`
- function `getSettingsWithSources` - `src/agent/src/utils/settings/settings.py:849`
- function `hasSkipDangerousModePermissionPrompt` - `src/agent/src/utils/settings/settings.py:886`
- function `getUseAutoModeDuringPlan` - `src/agent/src/utils/settings/settings.py:926`
- function `getAutoModeConfig` - `src/agent/src/utils/settings/settings.py:948`
- function `rawSettingsContainsKey` - `src/agent/src/utils/settings/settings.py:996`
- method `ThinkingConfig.to_api_params` - `src/agent/src/utils/thinking.py:45`
- method `ChatMessage.get_display_content` - `src/ai/agent_bridge.py:485`
- method `CortexToolContext.set_app_state` - `src/ai/agent_bridge.py:1181`
- method `CortexToolContext.update_app_state` - `src/ai/agent_bridge.py:1184`
- method `CortexToolContext.wait_for_event` - `src/ai/agent_bridge.py:1192`
- method `CortexToolContext.resume_execution` - `src/ai/agent_bridge.py:1196`
- method `CortexToolContext.register_mcp_hook` - `src/ai/agent_bridge.py:1204`
- method `CortexToolContext.unregister_mcp_hook` - `src/ai/agent_bridge.py:1207`
- method `CortexToolContext.trigger_mcp_hook` - `src/ai/agent_bridge.py:1210`
- method `CortexToolContext.get_session_state` - `src/ai/agent_bridge.py:1224`
- method `CortexToolContext.set_session_state` - `src/ai/agent_bridge.py:1227`
- method `CortexToolContext.subscribe_session_state` - `src/ai/agent_bridge.py:1230`
- method `CortexToolContext.unsubscribe_session_state` - `src/ai/agent_bridge.py:1233`
- method `CortexToolContext.get_permission_context` - `src/ai/agent_bridge.py:1237`
- method `CortexToolContext.is_file_known` - `src/ai/agent_bridge.py:1251`
- method `CortexToolContext.get_known_files_summary` - `src/ai/agent_bridge.py:1255`
- method `AgentSafetyGuard.reset_files_read` - `src/ai/agent_safety.py:189`
- method `AgentSafetyGuard.was_file_read` - `src/ai/agent_safety.py:337`
- method `AgentSafetyGuard.get_state_summary` - `src/ai/agent_safety.py:654`
- method `DiffRenderer.render_inline_diff` - `src/ai/changes/diff_renderer.py:231`
- method `ChangeAnalyzer.analyze_semantic_changes` - `src/ai/changes/diff_renderer.py:269`
- method `ToolCircuitBreaker.total_calls` - `src/ai/circuit_breaker.py:85`
- method `ToolCircuitBreaker.exceeded_repetitive_limit` - `src/ai/circuit_breaker.py:89`
- method `ToolCircuitBreaker.all_disabled` - `src/ai/circuit_breaker.py:93`
- function `get_auto_compact_threshold` - `src/ai/conversation_compactor.py:195`
- method `ProjectContext.to_system_prompt_block` - `src/ai/project_context.py:66`
- method `ProjectContext.to_warmup_summary` - `src/ai/project_context.py:125`
- method `ProviderRegistry.set_provider` - `src/ai/providers/__init__.py:678`
- method `ProviderRegistry.validate_all_keys` - `src/ai/providers/__init__.py:695`
- function `get_alibaba_provider` - `src/ai/providers/alibaba_provider.py:843`
- function `get_anthropic_provider` - `src/ai/providers/anthropic_provider.py:727`
- method `DeepSeekProvider._get_display_name` - `src/ai/providers/deepseek_provider.py:175`
- function `get_deepseek_provider` - `src/ai/providers/deepseek_provider.py:799`
- method `MistralProvider.chat_structured` - `src/ai/providers/mistral_provider.py:902`
- function `get_mistral_provider` - `src/ai/providers/mistral_provider.py:969`
- function `get_openrouter_provider` - `src/ai/providers/openrouter_provider.py:1323`
- function `get_siliconflow_provider` - `src/ai/providers/siliconflow_provider.py:350`
- function `get_provider_meta` - `src/ai/remote_models.py:580`
- method `StreamingEventEmitter.emit_tool_call_start` - `src/ai/streaming.py:37`
- method `StreamingEventEmitter.emit_tool_progress` - `src/ai/streaming.py:42`
- method `StreamingEventEmitter.emit_tool_result` - `src/ai/streaming.py:46`
- method `StreamingEventEmitter.emit_llm_token` - `src/ai/streaming.py:51`
- method `StreamingEventEmitter.emit_session_update` - `src/ai/streaming.py:66`
- method `StreamingEventEmitter.emit_error` - `src/ai/streaming.py:70`
- method `StreamingEventEmitter.emit_thought_start` - `src/ai/streaming.py:75`
- method `StreamingEventEmitter.emit_thought_delta` - `src/ai/streaming.py:80`
- method `StreamingEventEmitter.emit_thought_end` - `src/ai/streaming.py:84`
- method `ToolExecutionEngine.get_activity_name` - `src/ai/tool_executor.py:249`
- method `UsageTracker.set_avatar` - `src/ai/usage_tracker.py:221`
- method `UsageTracker.record_session_start` - `src/ai/usage_tracker.py:351`
- method `UsageTracker.record_fast_mode` - `src/ai/usage_tracker.py:362`
- method `UsageTracker.record_plugin_used` - `src/ai/usage_tracker.py:384`
- method `UsageTracker.get_current_limits` - `src/ai/usage_tracker.py:456`
- method `UsageTracker.get_model_breakdown` - `src/ai/usage_tracker.py:617`
- method `UsageTracker.get_insights` - `src/ai/usage_tracker.py:648`
- method `UsageTracker.get_streaks` - `src/ai/usage_tracker.py:652`
- method `UsageTracker.export_csv` - `src/ai/usage_tracker.py:706`
- method `UsageTracker.export_json` - `src/ai/usage_tracker.py:721`
- function `reset_usage_tracker` - `src/ai/usage_tracker.py:764`
- method `PointsManager.get_tokens_equivalent` - `src/config/points_manager.py:161`
- method `PointsManager.purchase_points` - `src/config/points_manager.py:165`
- method `PointsManager.consume_points` - `src/config/points_manager.py:225`
- method `PointsManager.get_transaction_history` - `src/config/points_manager.py:285`
- method `PointsManager.reset_balance` - `src/config/points_manager.py:304`
- function `reset_points_manager` - `src/config/points_manager.py:343`
- method `Scratchpad.read_all` - `src/coordinator/coordinator_system.py:86`
- method `VisionContextStore.has_context` - `src/coordinator/coordinator_system.py:136`
- function `_deserialize_debug_loop` - `src/core/agent_session_manager.py:42`
- method `AutonomyManager.set_level_from_string` - `src/core/autonomy_manager.py:202`
- method `AutonomyManager.get_levels` - `src/core/autonomy_manager.py:212`
- method `AutonomyManager.register_plan` - `src/core/autonomy_manager.py:231`
- method `AutonomyManager.record_user_decision` - `src/core/autonomy_manager.py:405`
- method `AutonomyManager.classify_tool` - `src/core/autonomy_manager.py:452`
- function `reset_autonomy_manager` - `src/core/autonomy_manager.py:477`
- method `ChangeOrchestrator.set_file_manager` - `src/core/change_orchestrator.py:69`
- method `ChangeOrchestrator.register_change_callback` - `src/core/change_orchestrator.py:73`
- method `ChangeOrchestrator.add_file_change` - `src/core/change_orchestrator.py:82`
- method `ChangeOrchestrator.preview_changes` - `src/core/change_orchestrator.py:122`
- method `ChangeOrchestrator.apply_pending_changes` - `src/core/change_orchestrator.py:126`
- method `ChangeOrchestrator.discard_pending_changes` - `src/core/change_orchestrator.py:174`
- method `ChangeOrchestrator.get_undo_preview` - `src/core/change_orchestrator.py:272`
- method `ChangeOrchestrator.get_redo_preview` - `src/core/change_orchestrator.py:279`
- method `ChangeOrchestrator.get_change_history` - `src/core/change_orchestrator.py:286`
- method `ChangeOrchestrator.clear_history` - `src/core/change_orchestrator.py:301`
- method `ChatHistoryManager.search_messages` - `src/core/chat_history.py:144`
- method `ChatHistoryManager.migrate_from_json` - `src/core/chat_history.py:205`
- method `ChatHistoryManager.export_to_json` - `src/core/chat_history.py:253`
- method `ChatHistoryManager.get_recent_context` - `src/core/chat_history.py:270`
- method `ChatHistoryManager.clear_project_history` - `src/core/chat_history.py:293`
- method `ChatHistoryManager.update_part` - `src/core/chat_history.py:349`
- method `ChatHistoryManager.restore_timeline_from_parts` - `src/core/chat_history.py:434`
- method `ChatHistoryManager.reset_all_chat_history` - `src/core/chat_history.py:460`
- method `ChatHistoryManager.clean_broken_conversations` - `src/core/chat_history.py:476`
- method `ChatHistoryManager.get_chat_stats` - `src/core/chat_history.py:511`
- method `CodeChunker.chunk_file` - `src/core/code_chunker.py:215`
- method `CodeChunker.get_chunk_summary` - `src/core/code_chunker.py:562`
- method `CodeChunker.extract_dependencies` - `src/core/code_chunker.py:579`
- function `get_chunker` - `src/core/code_chunker.py:591`
- method `Symbol.fully_qualified` - `src/core/codebase_index.py:43`
- method `CodebaseIndex.find_references` - `src/core/codebase_index.py:234`
- method `CodebaseIndex.get_file_symbols` - `src/core/codebase_index.py:239`
- method `CodebaseIndex.get_project_stats` - `src/core/codebase_index.py:246`
- method `CortexAPIClient.invalidate_subscription_cache` - `src/core/cortex_api.py:564`
- method `CortexAPIClient.get_credits` - `src/core/cortex_api.py:573`
- method `CortexAPIClient.get_payment_history` - `src/core/cortex_api.py:577`
- method `CrashStore.save_thinking` - `src/core/crash_persistence.py:338`
- class `ProjectMemory` - `src/core/database.py:65`
- method `CortexDatabase.get_file` - `src/core/database.py:477`
- method `CortexDatabase.get_file_content` - `src/core/database.py:487`
- method `CortexDatabase.get_all_files` - `src/core/database.py:497`
- method `CortexDatabase.upsert_chunk` - `src/core/database.py:514`
- method `CortexDatabase.get_chunks_by_file` - `src/core/database.py:583`
- method `CortexDatabase.get_chunks_by_type` - `src/core/database.py:593`
- method `CortexDatabase.search_chunks_text` - `src/core/database.py:606`
- method `CortexDatabase.store_embedding` - `src/core/database.py:652`
- method `CortexDatabase.get_embedding` - `src/core/database.py:683`
- method `CortexDatabase.get_all_embeddings` - `src/core/database.py:701`
- method `CortexDatabase.get_session_messages` - `src/core/database.py:1578`
- method `CortexDatabase.get_session_info` - `src/core/database.py:1634`
- method `CortexDatabase.get_recent_sessions` - `src/core/database.py:1660`
- method `CortexDatabase.delete_session` - `src/core/database.py:1687`
- method `CortexDatabase.set_memory` - `src/core/database.py:1698`
- method `CortexDatabase.get_memory` - `src/core/database.py:1709`
- method `CortexDatabase.get_all_memory` - `src/core/database.py:1720`
- method `CortexDatabase.clear_memory` - `src/core/database.py:1730`
- method `CortexDatabase.search_code` - `src/core/database.py:1740`
- method `CortexDatabase.find_functions` - `src/core/database.py:1798`
- method `CortexDatabase.find_classes` - `src/core/database.py:1830`
- method `CortexDatabase.clear_project` - `src/core/database.py:1866`
- method `DebugLoop.record_fix_attempt` - `src/core/debug_loop.py:76`
- method `DebugLoop.build_escalation_message` - `src/core/debug_loop.py:223`
- method `EmbeddingsGenerator.find_similar` - `src/core/embeddings.py:286`
- method `CodeEmbeddings.embed_code_chunk` - `src/core/embeddings.py:383`
- method `CodeEmbeddings.embed_query` - `src/core/embeddings.py:424`
- function `get_code_embeddings` - `src/core/embeddings.py:461`
- class `EditorEventData` - `src/core/event_bus.py:89`
- class `DebugEventData` - `src/core/event_bus.py:111`
- class `TerminalEventData` - `src/core/event_bus.py:121`
- class `OutlineEventData` - `src/core/event_bus.py:129`
- class `AIEventData` - `src/core/event_bus.py:137`
- class `CompletionEventData` - `src/core/event_bus.py:147`
- method `ComponentEventBus.get_recent_events` - `src/core/event_bus.py:227`
- method `ComponentEventBus.clear_history` - `src/core/event_bus.py:231`
- function `reset_event_bus` - `src/core/event_bus.py:248`
- class `FileReadWorker` - `src/core/file_manager.py:61`
- method `FileManager.prefetch_viewport` - `src/core/file_manager.py:337`
- method `FileManager.prefetch_files` - `src/core/file_manager.py:355`
- method `FileManager.has_file_changed` - `src/core/file_manager.py:362`
- method `FileManager.get_trash_count` - `src/core/file_manager.py:804`
- method `FileManager.clear_trash` - `src/core/file_manager.py:807`
- method `GitManager.reload_settings` - `src/core/git_manager.py:76`
- method `GitManager.is_repo` - `src/core/git_manager.py:125`
- method `GitManager.discard_changes` - `src/core/git_manager.py:230`
- method `GitManager.get_default_branch` - `src/core/git_manager.py:299`
- method `GitManager.get_commits` - `src/core/git_manager.py:303`
- method `GitManager.create_branch` - `src/core/git_manager.py:374`
- method `GitManager.get_file_status_icon` - `src/core/git_manager.py:406`
- function `current_reason` - `src/core/gpu_compat.py:70`
- class `StoredKey` - `src/core/key_manager.py:34`
- method `KeyManager.export_credentials` - `src/core/key_manager.py:659`
- method `KeyManager.reset_credentials` - `src/core/key_manager.py:728`
- method `KeyManager.auto_recover_keys` - `src/core/key_manager.py:755`
- method `KeyManager.get_security_info` - `src/core/key_manager.py:771`
- method `ProjectManager.get_recent` - `src/core/project_manager.py:49`
- method `SandboxManager.wrap_command` - `src/core/sandbox_manager.py:210`
- function `reset_sandbox_manager` - `src/core/sandbox_manager.py:409`
- method `SecurityAuditLogger.get_events` - `src/core/security_audit.py:251`
- method `SecurityAuditLogger.verify_integrity` - `src/core/security_audit.py:314`
- function `_now_iso` - `src/core/security_toolkit/recon.py:65`
- method `Engagement.is_empty` - `src/core/security_toolkit/report.py:87`
- class `ScopeEntry` - `src/core/security_toolkit/scope.py:127`
- method `Requester.check_scope` - `src/core/security_toolkit/web_probe.py:136`
- method `SemanticMemoryIndex.get_summary_stats` - `src/core/semantic_memory.py:327`
- function `reset_semantic_memory_index` - `src/core/semantic_memory.py:460`
- method `SemanticSearch.clear_index` - `src/core/semantic_search.py:769`
- method `SiliconFlowEmbeddings.find_similar` - `src/core/siliconflow_embeddings.py:403`
- function `index_to_col` - `src/core/spreadsheet_reader.py:74`
- method `StabilityEngine.current_pressure` - `src/core/stability_engine.py:140`
- method `StabilityEngine.is_breathing` - `src/core/stability_engine.py:144`
- method `StabilityEngine.last_health` - `src/core/stability_engine.py:148`
- method `StabilityEngine.should_pause` - `src/core/stability_engine.py:166`
- method `TaskNode.is_pending` - `src/core/task_graph.py:66`
- method `TaskNode.is_in_progress` - `src/core/task_graph.py:70`
- method `TaskGraph.get_node` - `src/core/task_graph.py:141`
- method `TaskGraph.get_ancestors` - `src/core/task_graph.py:212`
- method `TaskGraph.get_task_depth` - `src/core/task_graph.py:229`
- method `TaskGraph.get_ready_tasks` - `src/core/task_graph.py:253`
- method `TaskGraph.get_dependency_chain` - `src/core/task_graph.py:267`
- method `TaskGraph.has_cycles` - `src/core/task_graph.py:406`
- method `TaskGraph.get_all_tasks` - `src/core/task_graph.py:424`
- method `EditorTabWidget.open_diff_tab` - `src/main_window.py:440`
- method `EditorTabWidget.save_current` - `src/main_window.py:676`
- method `CortexMainWindow._kill_orphaned_chromium` - `src/main_window.py:817`
- method `CortexMainWindow._make_spacer` - `src/main_window.py:2580`
- method `CortexMainWindow.colorize_code` - `src/main_window.py:3096`
- method `CortexMainWindow.colorize_markdown` - `src/main_window.py:3110`
- method `CortexMainWindow._new_file` - `src/main_window.py:3431`
- method `CortexMainWindow._new_project` - `src/main_window.py:3534`
- method `CortexMainWindow._csv_to_html` - `src/main_window.py:4449`
- method `PluginAPI.show_message` - `src/plugin/plugin_manager.py:57`
- method `PluginManager.enable_plugin` - `src/plugin/plugin_manager.py:284`
- method `PluginManager.disable_plugin` - `src/plugin/plugin_manager.py:290`
- method `PluginManager.get_plugin` - `src/plugin/plugin_manager.py:294`
- method `PluginManager.get_all_plugins` - `src/plugin/plugin_manager.py:298`
- method `PluginManager.get_loaded_plugins` - `src/plugin/plugin_manager.py:302`
- method `PluginManager.is_loaded` - `src/plugin/plugin_manager.py:306`
- method `MCPManager.is_mcp_tool` - `src/services/mcp_manager.py:1138`
- method `MCPManager.tool_schema` - `src/services/mcp_manager.py:1174`
- function `_cross_fade_replace` - `src/ui/chat_panel.py:806`
- method `ChatStore.save_turn` - `src/ui/chat_store.py:54`
- method `ChatStore.list_chats` - `src/ui/chat_store.py:171`
- method `ChatStore.get_title` - `src/ui/chat_store.py:199`
- method `ChatStore.update_title` - `src/ui/chat_store.py:215`
- function `build_user_entry` - `src/ui/chat_store.py:242`
- function `build_assistant_entry` - `src/ui/chat_store.py:247`
- function `build_thinking_part` - `src/ui/chat_store.py:252`
- function `build_prose_part` - `src/ui/chat_store.py:256`
- function `build_tool_group_part` - `src/ui/chat_store.py:260`
- function `build_tool_entry` - `src/ui/chat_store.py:264`
- function `build_edited_files_part` - `src/ui/chat_store.py:268`
- function `build_file_entry` - `src/ui/chat_store.py:272`
- method `ModernMessageBubble.set_timestamp` - `src/ui/components/chat_enhanced/components.py:161`
- method `ModernMessageBubble.add_metadata_widget` - `src/ui/components/chat_enhanced/components.py:166`
- method `ModernMessageBubble.get_message_type` - `src/ui/components/chat_enhanced/components.py:170`
- method `ToolExecutionCard.set_parameters` - `src/ui/components/chat_enhanced/components.py:258`
- method `AgentSelectorWidget.get_selected_agent` - `src/ui/components/chat_enhanced/components.py:343`
- method `AgentSelectorWidget.set_agent` - `src/ui/components/chat_enhanced/components.py:347`
- class `CodePreview` - `src/ui/components/editor.py:51`
- method `CodePreview.set_ai_generated` - `src/ui/components/editor.py:171`
- method `CodePreview.get_editor` - `src/ui/components/editor.py:175`
- method `CodeEditor.folding_manager` - `src/ui/components/editor.py:1419`
- method `CodeEditor._show_inline_overlay` - `src/ui/components/editor.py:2021`
- method `CodeEditor.toggle_word_wrap` - `src/ui/components/editor.py:2310`
- method `CodeEditor.set_word_wrap` - `src/ui/components/editor.py:2318`
- method `CodeEditor.is_word_wrap_enabled` - `src/ui/components/editor.py:2325`
- class `FindReplaceManager` - `src/ui/components/find_replace.py:186`
- method `FindReplaceManager.show_find` - `src/ui/components/find_replace.py:195`
- method `FindReplaceManager.find_next` - `src/ui/components/find_replace.py:383`
- method `FindReplaceManager.find_previous` - `src/ui/components/find_replace.py:400`
- method `LivePreviewPanel.is_showing` - `src/ui/components/live_preview_panel.py:151`
- method `PermissionCardRenderer.render_json` - `src/ui/components/permission/card_renderer.py:418`
- method `PermissionCardRenderer.handle_grant` - `src/ui/components/permission/card_renderer.py:439`
- method `PermissionCardRenderer.handle_deny` - `src/ui/components/permission/card_renderer.py:444`
- method `SidebarBridge.requestSettings` - `src/ui/components/sidebar_bridge.py:1288`
- method `SidebarBridge.openFileDialog` - `src/ui/components/sidebar_bridge.py:1303`
- method `SidebarBridge.openFolderDialog` - `src/ui/components/sidebar_bridge.py:1312`
- method `SidebarBridge.showInputDialog` - `src/ui/components/sidebar_bridge.py:1318`
- method `SidebarBridge.detectLanguage` - `src/ui/components/sidebar_bridge.py:1366`
- method `SidebarBridge.onSearchContent` - `src/ui/components/sidebar_bridge.py:1732`
- method `SidebarBridge.onAiAction` - `src/ui/components/sidebar_bridge.py:1742`
- method `SidebarBridge.onAiConfigChanged` - `src/ui/components/sidebar_bridge.py:1747`
- method `SidebarBridge.onGitRefresh` - `src/ui/components/sidebar_bridge.py:1791`
- method `SidebarBridge.onChatSelected` - `src/ui/components/sidebar_bridge.py:1797`
- method `SidebarBridge.onChatDeleteRequested` - `src/ui/components/sidebar_bridge.py:1802`
- method `SidebarBridge.onNewChat` - `src/ui/components/sidebar_bridge.py:1807`
- method `SidebarBridge.onChatRenamed` - `src/ui/components/sidebar_bridge.py:1812`
- method `SidebarBridge.getGitDiff` - `src/ui/components/sidebar_bridge.py:1828`
- method `SidebarBridge.getGitStagedDiff` - `src/ui/components/sidebar_bridge.py:1836`
- method `SidebarBridge.gitCommit` - `src/ui/components/sidebar_bridge.py:1854`
- method `SidebarBridge.getGitLog` - `src/ui/components/sidebar_bridge.py:1862`
- method `TerminalBridge.set_ready` - `src/ui/components/terminal_bridge.py:91`
- class `TerminalWebAPI` - `src/ui/components/terminal_bridge.py:319`
- method `TerminalWebAPI.execute_terminal_command` - `src/ui/components/terminal_bridge.py:328`
- method `TerminalWebAPI.get_terminal_content` - `src/ui/components/terminal_bridge.py:333`
- method `TerminalWebAPI.is_terminal_ready` - `src/ui/components/terminal_bridge.py:337`
- method `WebviewPanel.open_file_count` - `src/ui/components/webview_panel.py:1573`
- method `WebviewPanel.has_file` - `src/ui/components/webview_panel.py:1576`
- class `WindowsTerminalWidget` - `src/ui/components/windows_terminal.py:56`
- class `WinPTYTerminalWidget` - `src/ui/components/windows_terminal.py:658`
- method `XTermWidget.read_file_async` - `src/ui/components/xterm_terminal.py:1554`
- method `XTermWidget.execute_async` - `src/ui/components/xterm_terminal.py:1589`
- method `XTermWidget._ensure_embedded_mode` - `src/ui/components/xterm_terminal.py:1611`
- method `XTermWidget.is_embedded` - `src/ui/components/xterm_terminal.py:1624`
- method `XTermWidget.get_terminal_info` - `src/ui/components/xterm_terminal.py:1628`
- method `MemoryManagerBridge.loadRules` - `src/ui/dialogs/memory_manager.py:435`
- method `MemoryManagerBridge.saveRules` - `src/ui/dialogs/memory_manager.py:474`
- method `MemoryManagerBridge.semanticSearch` - `src/ui/dialogs/memory_manager.py:564`
- method `MemoryManagerBridge.exitSearchMode` - `src/ui/dialogs/memory_manager.py:643`
- method `MemoryManagerBridge.getGlobalMemories` - `src/ui/dialogs/memory_manager.py:758`
- method `MemoryManagerBridge.saveGlobalMemory` - `src/ui/dialogs/memory_manager.py:787`
- method `MemoryManagerBridge.deleteGlobalMemory` - `src/ui/dialogs/memory_manager.py:813`
- method `MemoryManagerBridge.promoteToGlobal` - `src/ui/dialogs/memory_manager.py:862`
- method `MemoryManagerBridge.getSetting` - `src/ui/dialogs/memory_manager.py:1897`
- function `reset_offer_session` - `src/ui/dialogs/subscription_offer.py:123`
- method `UpdateDialog.downloaded_path` - `src/ui/dialogs/update_dialog.py:342`
- method `EditStateManager.reject_all` - `src/ui/edit_state_manager.py:307`
- method `EditStateManager.pending_count` - `src/ui/edit_state_manager.py:329`
- method `EditStateManager.aggregate_status` - `src/ui/edit_state_manager.py:333`
- function `_has_svg` - `src/ui/icons.py:67`
- function `icon_label` - `src/ui/icons.py:104`
- function `expand_mcp_tokens` - `src/ui/mcp_mentions.py:190`
- method `NativeChatBridge.disconnect_all` - `src/ui/native_chat_bridge.py:674`
- function `render_failures` - `src/ui/render_health.py:96`
- function `_unused_legacy_filter` - `src/ui/slash_commands.py:154`
- function `build_skill_invocation` - `src/ui/slash_commands.py:210`
- method `SpinnerOverlay._on_fade_out_finished` - `src/ui/spinner_overlay.py:336`
- method `SpinnerOverlay.update_message` - `src/ui/spinner_overlay.py:358`
- function `_wrap_bold` - `src/ui/syntax_highlight.py:107`
- function `_wrap_bold_italic` - `src/ui/syntax_highlight.py:111`
- function `get_supported_languages` - `src/ui/syntax_highlight.py:734`
- function `normalize_table_block` - `src/ui/table_normalize.py:135`
- function `get_auto_compact_threshold` - `src/ui/tokens.py:984`
- function `filter_tools_for_agent` - `src/utils/agent_tools.py:115`
- method `AgentLifecycleManager.set_callbacks` - `src/utils/agent_tools.py:257`
- method `AgentLifecycleManager.stop_agent` - `src/utils/agent_tools.py:318`
- method `AgentLifecycleManager.get_task` - `src/utils/agent_tools.py:326`
- method `AgentLifecycleManager.get_active_tasks` - `src/utils/agent_tools.py:330`
- method `AgentLifecycleManager.get_all_results` - `src/utils/agent_tools.py:335`
- function `get_file_content` - `src/utils/git_utils.py:170`
- function `file_icon` - `src/utils/helpers.py:174`
- function `human_size` - `src/utils/helpers.py:181`
- function `_draw_files` - `src/utils/icons.py:274`
- function `_draw_ai` - `src/utils/icons.py:298`
- function `make_sprite_icon` - `src/utils/icons.py:1284`
- function `make_button_icon` - `src/utils/icons.py:1386`
- function `notify_task_complete` - `src/utils/notifications.py:166`
- method `TimeoutContext.from_task_description` - `src/utils/timeout_strategy.py:82`
- function `get_stream_timeout` - `src/utils/timeout_strategy.py:172`

### Orphan files (0 listed)

None found.

### Unimported modules (0 listed)

No other file imports these, names them in a dotted-path string, or lists them by path in a build spec. This is still **not** a claim that they are dead: a loader can build the name at runtime, and a module named after an ordinary word is mentioned everywhere. `named elsewhere` means the module's name occurs in other project text, which is where a dynamic reference would appear. `not named anywhere` is the subset also reported above as orphan files. Review before deleting.

None found.

## Scan notes

- Deep analysis covers Python (AST), JSON, HTML (tag balance), YAML, TOML, CSS (brace balance). JavaScript, TypeScript and Markdown are counted and listed but not parsed for errors.
- Dead-code tab is heuristic: a symbol is reported unused only when its name appears nowhere in ANY project text file (Python, JavaScript, HTML, config, build scripts) beyond its own definition lines. Test symbols and framework-invoked methods (Qt events, http.server handlers, QThread.run) are exempt, as are methods the framework looks up by name at runtime (visit_* in an ast.NodeVisitor subclass, handle_* in an HTMLParser subclass) and file-object protocol methods. A file is orphaned only when no other file imports it or mentions its module name. Names built at runtime by string concatenation can still hide real usage - review before deleting.
- Symbol list capped at 4000 for performance; showing 4000 of 5649. Problem files are always included.
