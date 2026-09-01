import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_FILE_ENABLE", "False")
os.environ.setdefault("LOG_CONSOLE_LEVEL", "WARNING")
os.environ.setdefault("LANGGRAPH_CHECKPOINTER", "memory")
os.environ.setdefault("DATABASE_ENABLED", "True")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MILVUS_REQUIRED", "False")
os.environ.setdefault("MILVUS_URI", "http://127.0.0.1:19530")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-secret-value-not-a-real-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://example.invalid/v1")
os.environ.setdefault("MINERU_API_TOKEN", "test-secret-not-a-real-mineru-token")
