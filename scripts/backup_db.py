"""
Скрипт бэкапа БД (PostgreSQL или SQLite).
Использование:
  python backup_db.py                    # бэкап в backups/YYYY-MM-DD_HHMMSS.sql
  python backup_db.py --restore FILE     # восстановление из дампа
  python backup_db.py --list             # список бэкапов
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path(__file__).parent.parent / "backups"


def get_db_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def is_sqlite() -> bool:
    url = get_db_url()
    return url.startswith("sqlite:") or not url


def backup_sqlite() -> Path:
    """Бэкап SQLite БД."""
    from urllib.parse import urlparse
    url = get_db_url()
    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "")
    elif url.startswith("sqlite:"):
        db_path = url.replace("sqlite:", "")
    else:
        db_path = "bot/bot.db"

    db_file = Path(db_path)
    if not db_file.exists():
        print(f"❌ SQLite DB not found: {db_file}")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = BACKUP_DIR / f"benzin_{timestamp}.db"

    import shutil
    shutil.copy2(db_file, backup_path)
    size_mb = backup_path.stat().st_size / (1024 * 1024)
    print(f"✅ SQLite backup created: {backup_path} ({size_mb:.2f} MB)")
    return backup_path


def backup_pg() -> Path:
    """Бэкап PostgreSQL через pg_dump."""
    db_url = get_db_url()
    if not db_url:
        print("❌ DATABASE_URL not set")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = BACKUP_DIR / f"benzin_{timestamp}.sql"

    env = os.environ.copy()
    env["PGPASSWORD"] = db_url.split(":")[-1].split("@")[0]

    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-acl",
        "--clean",
        "--if-exists",
        db_url,
    ]

    try:
        with open(backup_path, "w") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
        if result.returncode != 0:
            print(f"❌ pg_dump failed: {result.stderr.decode()}")
            sys.exit(1)
    except FileNotFoundError:
        print("❌ pg_dump not found. Install: apt-get install postgresql-client")
        sys.exit(1)

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    print(f"✅ PG backup created: {backup_path} ({size_mb:.2f} MB)")
    return backup_path


def restore_sqlite(backup_path: Path):
    """Восстановление SQLite из бэкапа."""
    from urllib.parse import urlparse
    url = get_db_url()
    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "")
    elif url.startswith("sqlite:"):
        db_path = url.replace("sqlite:", "")
    else:
        db_path = "bot/bot.db"

    if not backup_path.exists():
        print(f"❌ Backup not found: {backup_path}")
        sys.exit(1)

    import shutil
    shutil.copy2(backup_path, db_path)
    print(f"✅ SQLite restored from: {backup_path}")


def restore_pg(backup_path: Path):
    """Восстановление PostgreSQL из дампа."""
    db_url = get_db_url()
    if not backup_path.exists():
        print(f"❌ Backup not found: {backup_path}")
        sys.exit(1)

    env = os.environ.copy()
    env["PGPASSWORD"] = db_url.split(":")[-1].split("@")[0]

    cmd = ["psql", db_url, "-f", str(backup_path)]
    result = subprocess.run(cmd, capture_output=True, env=env)
    if result.returncode != 0:
        print(f"❌ psql failed: {result.stderr.decode()}")
        sys.exit(1)
    print(f"✅ PG restored from: {backup_path}")


def list_backups():
    """Список всех бэкапов."""
    if not BACKUP_DIR.exists():
        print("No backups yet")
        return
    files = sorted(BACKUP_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("No backups yet")
        return
    print(f"Backups in {BACKUP_DIR}:")
    for f in files[:20]:
        size_mb = f.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {f.name}  ({size_mb:.2f} MB)  {mtime}")
    if len(files) > 20:
        print(f"  ... and {len(files) - 20} more")


def main():
    parser = argparse.ArgumentParser(description="DB backup/restore for Benzin Ryadom")
    parser.add_argument("--restore", type=str, help="Restore from backup file")
    parser.add_argument("--list", action="store_true", help="List all backups")
    args = parser.parse_args()

    if args.list:
        list_backups()
        return

    if args.restore:
        backup_path = Path(args.restore)
        if not backup_path.is_absolute():
            backup_path = BACKUP_DIR / backup_path
        if is_sqlite():
            restore_sqlite(backup_path)
        else:
            restore_pg(backup_path)
        return

    # Backup mode
    print("Creating backup...")
    if is_sqlite():
        backup_path = backup_sqlite()
    else:
        backup_path = backup_pg()

    print(f"\nTo restore: python backup_db.py --restore {backup_path.name}")


if __name__ == "__main__":
    main()
