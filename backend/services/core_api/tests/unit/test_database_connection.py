from src.infrastructure.database.connection import create_engine


def test_database_errors_are_logged_without_query_values() -> None:
    """Errors must not print the values sent with a query (emails, password hashes)."""
    engine = create_engine("postgresql+asyncpg://user:pw@127.0.0.1:1/none")
    assert engine.sync_engine.hide_parameters is True
