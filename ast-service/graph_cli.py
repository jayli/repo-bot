import argparse

from db import connect_db
from graph import GraphConfig, create_driver, ensure_constraints, sync_graph_from_sqlite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--repo")
    args = parser.parse_args()

    config = GraphConfig.from_env()
    if not config.enabled:
        raise SystemExit("Neo4j is disabled")

    conn = connect_db()
    driver = create_driver(config)
    try:
        ensure_constraints(driver, config.database)
        if args.command == "sync":
            sync_graph_from_sqlite(conn, driver, config.database, repo=args.repo)
    finally:
        if driver is not None:
            driver.close()
        conn.close()


if __name__ == "__main__":
    main()
