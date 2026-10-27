import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_data  # noqa: E402

from market_assistant.agent.pipeline import Assistant  # noqa: E402
from market_assistant.core.settings import Settings  # noqa: E402
from market_assistant.db.database import Database  # noqa: E402


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate_data.build(tmp_path_factory.mktemp("data") / "market.db")


@pytest.fixture(scope="session")
def settings(db_path: Path) -> Settings:
    return Settings(db_path=db_path, llm_api_key="", rate_limit_per_minute=1000, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def db(settings: Settings) -> Database:
    return Database(settings.db_path, settings.query_timeout_s, settings.max_rows)


@pytest.fixture
def bot(settings: Settings) -> Assistant:
    return Assistant(settings)
