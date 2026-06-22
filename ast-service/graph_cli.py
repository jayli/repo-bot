import argparse

from db import connect_db, init_db
from graph import GraphConfig, create_driver, ensure_constraints, incr_sync_graph_from_sqlite, sync_graph_from_sqlite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync", "sync-incr"])
    parser.add_argument("--repo")
    args = parser.parse_args()

    config = GraphConfig.from_env()
    if not config.enabled:
        raise SystemExit("Neo4j is disabled")

    conn = connect_db()
    init_db(conn)
    driver = create_driver(config)
    try:
        ensure_constraints(driver, config.database)
        if args.command == "sync":
            repos = sync_graph_from_sqlite(conn, driver, config.database, repo=args.repo)
            print(f"Full sync complete: {len(repos)} repo(s) synced")
        elif args.command == "sync-incr":
            repos = incr_sync_graph_from_sqlite(conn, driver, config.database)
            if repos:
                print(f"Incremental sync complete: {len(repos)} repo(s) synced — {', '.join(repos)}")
            else:
                print("Incremental sync: no repos need updating")
    finally:
        if driver is not None:
            driver.close()
        conn.close()


if __name__ == "__main__":
    main()
