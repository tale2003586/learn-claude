# Pending Memory


- [fact] app/config.py now defines SETTINGS = {'timeout': 30, 'retries': 3} and build_config() returns that dict. (source: `task:coding-65858352/llm`)

- [decision] Module-level constants DEFAULT_TIMEOUT and RETRY_COUNT removed; configuration values reside in SETTINGS dictionary. (source: `task:coding-65858352/llm`)

- [preference] Refactoring must not alter public API; tests.py validates describe() unchanged, no changes to tests allowed. (source: `task:coding-65858352/llm`)
