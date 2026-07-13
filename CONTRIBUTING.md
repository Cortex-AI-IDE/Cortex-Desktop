# Contributing to Cortex OSS

Thanks for your interest in contributing to Cortex! This document covers how to contribute effectively.

## Code of Conduct

Be respectful. Be constructive. We're building something great together.

## How to Contribute

### Reporting Bugs

1. Check the [existing issues](https://github.com/CortexIDE/cortex-oss/issues) first
2. Use the Bug Report template
3. Include: steps to reproduce, expected behavior, actual behavior, environment details

### Suggesting Features

1. Check existing issues and discussions
2. Describe the problem your feature solves
3. Suggest a solution approach
4. Include mockups or examples if applicable

### Pull Requests

1. **Fork** the repository
2. **Create a branch**: `feature/your-feature` or `fix/your-bugfix`
3. **Write tests** for your changes
4. **Run existing tests**: `pytest tests/`
5. **Follow the code style**: PEP8, type hints where practical
6. **Write clear commit messages**
7. **Open a PR** against the `main` branch

### PR Checklist

- [ ] Tests pass (`pytest tests/`)
- [ ] New tests added for new functionality
- [ ] Documentation updated if needed
- [ ] No private/proprietary code included
- [ ] Code follows project conventions

## Development Setup

```bash
# Clone the repo
git clone https://github.com/CortexIDE/cortex-oss.git
cd cortex-oss

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/
```

## Project Structure

```
cortex_oss/
├── src/
│   ├── ai/                  # AI/LLM integrations
│   │   ├── providers/       # LLM provider implementations
│   │   ├── model_limits.py  # Model definitions & limits
│   │   ├── model_registry.py# Model registry
│   │   └── tool_executor.py # Tool execution engine
│   ├── core/                # Core modules
│   │   ├── embeddings.py    # Semantic embeddings
│   │   ├── semantic_search.py
│   │   ├── code_chunker.py  # Code parsing/chunking
│   │   └── ...
│   ├── ui/                  # Qt UI components
│   ├── utils/               # Utilities
│   ├── services/            # Services
│   ├── plugin/              # Plugin system
│   └── coordinator/         # Coordinator
├── plugins/                 # Bundled plugins
├── Docs/                    # Documentation
├── tests/                   # Test suite
└── README.md
```

## Coding Conventions

- **Python**: PEP8, type hints, docstrings
- **Imports**: Absolute imports preferred, grouped (stdlib → third-party → local)
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Comments**: Explain WHY, not WHAT. Code should be self-documenting.

## Questions?

Open a [Discussion](https://github.com/CortexIDE/cortex-oss/discussions) or reach out on [Twitter/X](https://x.com/CortexIDE).
