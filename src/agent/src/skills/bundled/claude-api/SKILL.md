---
name: claude-api
description: |-
  Reference for the Claude API / Anthropic SDK — model ids, pricing, params, streaming, tool use, MCP, agents, caching, token counting, model migration.
  TRIGGER — read BEFORE opening the target file; don't skip because it "looks like a one-liner" — whenever: the prompt names Claude/Anthropic in any form (Claude, Anthropic, Fable, Opus, Sonnet, Haiku, `anthropic`, `@anthropic-ai`, `claude-*`, `us.anthropic.*`, `[1m]`); the user asks about an LLM (pricing/model choice/limits/caching) — never answer from memory; OR the task is LLM-shaped with provider unstated (agent/MCP/tool-definition/multi-agent/RAG/LLM-judge/computer-use; generate/summarize/extract/classify/rewrite/converse over NL; debugging refusals/cutoffs/streaming/tool-calls/tokens).
  SKIP only when another provider is being worked on (overrides all triggers): OpenAI/GPT/Gemini/Llama/Mistral/Cohere/Ollama named in the query; OR `grep -rE 'openai|langchain_openai|google.generativeai|genai|mistralai|cohere|ollama'` over the project hits (run this grep FIRST if no provider named — don't Read the file).
license: Complete terms in LICENSE.txt
---

# claude-api

This skill helps you build LLM-powered applications with Claude. Choose the right surface based on your needs, detect the project language, then read the relevant language-specific documentation.

This skill's full method is large, so it ships as a reference file instead of being pasted into your context up front. The complete guide is in `references/full-guide.md` — **read it when you reach the step that needs it**. The Skill tool returns its path.

## What the guide covers

- Building LLM-Powered Applications with Claude
- Before You Start
- Output Requirement
- Defaults
- ⚠️ API Drift — Your Training Prior May Be Stale
- Subcommands
- Language Detection
- Language-Specific Feature Support
- Which Surface Should I Use?
- Building an Agent: Four Approaches
- Should I Build an Agent?
- Architecture
- Current Models (cached: 2026-06-24)
- Claude Fable 5 (`claude-fable-5`) — most capable widely released model
- Authentication (Quick Reference)
- Thinking & Effort (Quick Reference)
- Compaction (Quick Reference)
- Prompt Caching (Quick Reference)
- Fast Mode (Quick Reference)
- Task Budgets (Quick Reference)
- Provider Clients (Quick Reference)
- Amazon Bedrock
- Microsoft Foundry
- Google Cloud Vertex AI

## How to use it here

1. Read `references/full-guide.md` before you start producing output.
2. Follow its rules exactly rather than improvising a similar approach.
3. Build on real files with `Write`/`Edit`, then verify the result — in Live Preview for anything visual, by running it otherwise.
