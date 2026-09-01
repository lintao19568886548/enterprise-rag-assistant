import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_FILE_ENABLE", "False")
os.environ.setdefault("LOG_CONSOLE_LEVEL", "WARNING")
os.environ.setdefault("LANGGRAPH_CHECKPOINTER", "memory")
os.environ.setdefault("DATABASE_ENABLED", "True")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MILVUS_REQUIRED", "False")
