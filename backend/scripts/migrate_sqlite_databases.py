"""将 backend 目录中的历史 SQLite 对局数据合并到应用 MySQL 数据库。

默认只执行只读审计；显式传入 ``--migrate`` 才会写入 MySQL。
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select

from app.config import get_settings
from app.persistence.models import Base


BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLE_NAMES = [table.name for table in Base.metadata.sorted_tables]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def database_counts(engine: Any) -> dict[str, int]:
    inspector = inspect(engine)
    metadata = MetaData()
    counts: dict[str, int] = {}
    for table_name in TABLE_NAMES:
        if not inspector.has_table(table_name):
            continue
        table = Table(table_name, metadata, autoload_with=engine)
        with engine.connect() as connection:
            counts[table_name] = connection.execute(
                select(func.count()).select_from(table)
            ).scalar_one()
    return counts


def primary_keys(engine: Any) -> dict[str, set[tuple[Any, ...]]]:
    inspector = inspect(engine)
    metadata = MetaData()
    result: dict[str, set[tuple[Any, ...]]] = {}
    for table_name in TABLE_NAMES:
        if not inspector.has_table(table_name):
            continue
        table = Table(table_name, metadata, autoload_with=engine)
        pk_columns = list(table.primary_key.columns)
        if not pk_columns:
            continue
        with engine.connect() as connection:
            result[table_name] = {
                tuple(row)
                for row in connection.execute(select(*pk_columns))
            }
    return result


def sqlite_health(engine: Any) -> tuple[bool, int]:
    with engine.connect() as connection:
        integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
        foreign_key_errors = len(
            connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        )
    return integrity == "ok", foreign_key_errors


def revision(engine: Any) -> str:
    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        return "<无版本表>"
    metadata = MetaData()
    table = Table("alembic_version", metadata, autoload_with=engine)
    with engine.connect() as connection:
        value = connection.execute(select(table.c.version_num)).scalar_one_or_none()
    return str(value or "<空>")


def audit(sqlite_files: list[Path], mysql_engine: Any) -> None:
    mysql_counts = database_counts(mysql_engine)
    mysql_keys = primary_keys(mysql_engine)
    print(f"MySQL 版本: {revision(mysql_engine)}")
    print("MySQL 行数: " + ", ".join(f"{k}={v}" for k, v in mysql_counts.items()))

    seen_source_keys: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    for sqlite_file in sqlite_files:
        engine = create_engine(f"sqlite:///{sqlite_file.as_posix()}")
        counts = database_counts(engine)
        keys = primary_keys(engine)
        integrity_ok, foreign_key_errors = sqlite_health(engine)
        mysql_collisions = sum(
            len(table_keys & mysql_keys.get(table_name, set()))
            for table_name, table_keys in keys.items()
        )
        source_collisions = sum(
            len(table_keys & seen_source_keys[table_name])
            for table_name, table_keys in keys.items()
        )
        print(
            f"{sqlite_file.name}: 版本={revision(engine)}; "
            f"总行数={sum(counts.values())}; games={counts.get('games', 0)}; "
            f"完整性={'正常' if integrity_ok else '异常'}; 外键错误={foreign_key_errors}; "
            f"与MySQL主键冲突={mysql_collisions}; SQLite间主键冲突={source_collisions}"
        )
        for table_name, table_keys in keys.items():
            seen_source_keys[table_name].update(table_keys)
        engine.dispose()


def backup_mysql(mysql_engine: Any) -> Path:
    """在没有 mysqldump 客户端时生成可直接恢复的 SQL 数据库备份。"""

    backup_dir = BACKEND_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    database_name = mysql_engine.url.database or "werewolf"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{database_name}-before-sqlite-merge-{timestamp}.sql"

    inspector = inspect(mysql_engine)
    table_names = inspector.get_table_names()
    raw_connection = mysql_engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        with backup_path.open("w", encoding="utf-8", newline="\n") as output:
            output.write("SET NAMES utf8mb4;\n")
            output.write("SET FOREIGN_KEY_CHECKS=0;\n\n")
            for table_name in table_names:
                quoted_table = f"`{table_name.replace('`', '``')}`"
                cursor.execute(f"SHOW CREATE TABLE {quoted_table}")
                create_sql = cursor.fetchone()[1]
                output.write(f"DROP TABLE IF EXISTS {quoted_table};\n")
                output.write(f"{create_sql};\n\n")

            for table_name in table_names:
                quoted_table = f"`{table_name.replace('`', '``')}`"
                cursor.execute(f"SELECT * FROM {quoted_table}")
                columns = [item[0] for item in cursor.description]
                quoted_columns = ", ".join(
                    f"`{column.replace('`', '``')}`" for column in columns
                )
                placeholders = ", ".join(["%s"] * len(columns))
                insert_sql = (
                    f"INSERT INTO {quoted_table} ({quoted_columns}) "
                    f"VALUES ({placeholders})"
                )
                while rows := cursor.fetchmany(500):
                    for row in rows:
                        output.write(cursor.mogrify(insert_sql, row))
                        output.write(";\n")
                output.write("\n")
            output.write("SET FOREIGN_KEY_CHECKS=1;\n")
    finally:
        raw_connection.close()
    return backup_path


def prepared_row(source_row: Any, target_table: Table) -> dict[str, Any]:
    source = dict(source_row._mapping)
    prepared = {column.name: source[column.name] for column in target_table.columns if column.name in source}
    if target_table.name == "votes" and "abstained" not in prepared:
        prepared["abstained"] = source.get("target_player_id") is None

    missing_required = [
        column.name
        for column in target_table.columns
        if column.name not in prepared
        and not column.nullable
        and column.server_default is None
        and not column.autoincrement
    ]
    if missing_required:
        raise RuntimeError(
            f"{target_table.name} 缺少必填字段: {', '.join(missing_required)}"
        )
    return prepared


def migrate(sqlite_files: list[Path], mysql_engine: Any) -> tuple[int, int]:
    """在单个 MySQL 事务中合并数据；已存在的主键视为重复数据并跳过。"""

    target_metadata = MetaData()
    target_tables = {
        table_name: Table(table_name, target_metadata, autoload_with=mysql_engine)
        for table_name in TABLE_NAMES
    }
    known_keys = primary_keys(mysql_engine)
    inserted = 0
    skipped = 0

    with mysql_engine.begin() as mysql_connection:
        for sqlite_file in sqlite_files:
            sqlite_engine = create_engine(f"sqlite:///{sqlite_file.as_posix()}")
            integrity_ok, foreign_key_errors = sqlite_health(sqlite_engine)
            if not integrity_ok or foreign_key_errors:
                sqlite_engine.dispose()
                raise RuntimeError(
                    f"{sqlite_file.name} 完整性检查失败："
                    f"integrity={integrity_ok}, foreign_key_errors={foreign_key_errors}"
                )

            source_inspector = inspect(sqlite_engine)
            source_metadata = MetaData()
            with sqlite_engine.connect() as sqlite_connection:
                file_inserted = 0
                file_skipped = 0
                for table_name in TABLE_NAMES:
                    if not source_inspector.has_table(table_name):
                        continue
                    source_table = Table(
                        table_name, source_metadata, autoload_with=sqlite_engine
                    )
                    target_table = target_tables[table_name]
                    pk_names = [column.name for column in target_table.primary_key.columns]
                    batch: list[dict[str, Any]] = []
                    for source_row in sqlite_connection.execute(select(source_table)):
                        row = prepared_row(source_row, target_table)
                        key = tuple(row[name] for name in pk_names)
                        if key in known_keys.setdefault(table_name, set()):
                            skipped += 1
                            file_skipped += 1
                            continue
                        known_keys[table_name].add(key)
                        batch.append(row)
                        if len(batch) == 500:
                            mysql_connection.execute(target_table.insert(), batch)
                            inserted += len(batch)
                            file_inserted += len(batch)
                            batch.clear()
                    if batch:
                        mysql_connection.execute(target_table.insert(), batch)
                        inserted += len(batch)
                        file_inserted += len(batch)
                print(
                    f"{sqlite_file.name}: 写入={file_inserted}, "
                    f"重复跳过={file_skipped}"
                )
            sqlite_engine.dispose()
    return inserted, skipped


def verify(sqlite_files: list[Path], mysql_engine: Any) -> None:
    inspector = inspect(mysql_engine)
    mysql_keys = primary_keys(mysql_engine)
    missing_source_rows = 0
    for sqlite_file in sqlite_files:
        sqlite_engine = create_engine(f"sqlite:///{sqlite_file.as_posix()}")
        for table_name, keys in primary_keys(sqlite_engine).items():
            missing_source_rows += len(keys - mysql_keys.get(table_name, set()))
        sqlite_engine.dispose()

    vote_columns = {column["name"] for column in inspector.get_columns("votes")}
    orphan_rows = 0
    with mysql_engine.connect() as connection:
        for table_name in TABLE_NAMES:
            for foreign_key in inspector.get_foreign_keys(table_name):
                local_columns = foreign_key["constrained_columns"]
                remote_columns = foreign_key["referred_columns"]
                remote_table = foreign_key["referred_table"]
                if len(local_columns) != 1 or len(remote_columns) != 1:
                    raise RuntimeError(f"暂不支持复合外键校验: {table_name}")
                local = local_columns[0]
                remote = remote_columns[0]
                query = (
                    f"SELECT COUNT(*) FROM `{table_name}` AS child "
                    f"LEFT JOIN `{remote_table}` AS parent "
                    f"ON child.`{local}` = parent.`{remote}` "
                    f"WHERE child.`{local}` IS NOT NULL "
                    f"AND parent.`{remote}` IS NULL"
                )
                orphan_rows += connection.exec_driver_sql(query).scalar_one()
        invalid_votes = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM votes WHERE NOT ("
            "(abstained = 1 AND target_player_id IS NULL) OR "
            "(abstained = 0 AND target_player_id IS NOT NULL))"
        ).scalar_one()

    print(f"MySQL 版本: {revision(mysql_engine)}")
    print(f"SQLite 中尚未进入 MySQL 的主键行数: {missing_source_rows}")
    print(f"MySQL 外键孤儿行数: {orphan_rows}")
    print(f"投票弃票约束异常行数: {invalid_votes}")
    print(f"votes.abstained 字段存在: {'abstained' in vote_columns}")
    if (
        revision(mysql_engine) != "e7a9c4d2f681"
        or missing_source_rows
        or orphan_rows
        or invalid_votes
        or "abstained" not in vote_columns
    ):
        raise SystemExit("迁移核验失败。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate", action="store_true", help="执行 SQLite 到 MySQL 合并")
    parser.add_argument("--backup", action="store_true", help="备份当前 MySQL 数据库")
    parser.add_argument("--verify", action="store_true", help="核验结构和迁移完整性")
    args = parser.parse_args()
    sqlite_files = sorted(BACKEND_DIR.glob("*.db"))
    if not sqlite_files:
        raise SystemExit("backend 目录中没有找到 SQLite 数据库。")

    settings = get_settings()
    mysql_engine = create_engine(settings.database_url)
    if mysql_engine.dialect.name != "mysql":
        raise SystemExit(f"DATABASE_URL 不是 MySQL：{mysql_engine.dialect.name}")
    if args.backup:
        print(f"MySQL 备份已生成: {backup_mysql(mysql_engine)}")
    elif args.migrate:
        inserted, skipped = migrate(sqlite_files, mysql_engine)
        print(f"迁移完成：写入={inserted}, 重复跳过={skipped}")
    elif args.verify:
        verify(sqlite_files, mysql_engine)
    else:
        audit(sqlite_files, mysql_engine)
    mysql_engine.dispose()


if __name__ == "__main__":
    main()
