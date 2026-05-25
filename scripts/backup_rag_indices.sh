#!/usr/bin/env bash
# scripts/backup_rag_indices.sh — Backup / restore Chroma RAG indices.
#
# Backup:
#   bash scripts/backup_rag_indices.sh backup [./rag_indices] [./backups]
# Restore:
#   bash scripts/backup_rag_indices.sh restore <backup.tar.gz> [./rag_indices]
# List backups:
#   bash scripts/backup_rag_indices.sh list [./backups]
#
# Backup file 命名:rag_indices_YYYYMMDD_HHMMSS.tar.gz
# 內含整個 chroma.sqlite3 + collection 子目錄

set -e

MODE="${1:-backup}"
INDICES_DIR="${2:-./rag_indices}"
BACKUP_DIR="${3:-./backups}"

_ts() {
    date +%Y%m%d_%H%M%S
}

_validate_indices_dir() {
    if [ ! -d "$1" ]; then
        echo "❌ indices dir not found: $1"
        echo "   先跑 scripts/build_rag_indices.py --full-rebuild"
        exit 1
    fi
    if [ ! -f "$1/chroma.sqlite3" ]; then
        echo "⚠️  Warning:$1 沒有 chroma.sqlite3,可能不是 Chroma index dir"
    fi
}

case "$MODE" in
    backup)
        _validate_indices_dir "$INDICES_DIR"
        mkdir -p "$BACKUP_DIR"
        backup_file="$BACKUP_DIR/rag_indices_$(_ts).tar.gz"
        echo "Backing up $INDICES_DIR → $backup_file"
        tar czf "$backup_file" "$INDICES_DIR/"
        size=$(du -sh "$backup_file" | cut -f1)
        echo "✅ Backup complete · size=$size"
        echo
        echo "Restore command:"
        echo "  bash scripts/backup_rag_indices.sh restore $backup_file"
        ;;

    restore)
        if [ -z "$2" ] || [ ! -f "$2" ]; then
            echo "❌ Usage: restore <backup.tar.gz>"
            exit 1
        fi
        backup_file="$2"
        target="${3:-./rag_indices}"
        echo "Restoring $backup_file → $target"
        if [ -d "$target" ]; then
            echo "⚠️  $target exists. Move existing?"
            mv "$target" "${target}.before_restore_$(_ts)"
            echo "   Moved to ${target}.before_restore_*"
        fi
        tar xzf "$backup_file" -C "$(dirname "$target")"
        echo "✅ Restored"
        echo
        echo "驗證:python scripts/inspect_rag_retrieval.py"
        ;;

    list)
        list_dir="${2:-./backups}"
        if [ ! -d "$list_dir" ]; then
            echo "(no backups in $list_dir)"
            exit 0
        fi
        echo "Backups in $list_dir:"
        ls -lh "$list_dir"/rag_indices_*.tar.gz 2>/dev/null || echo "(none)"
        ;;

    *)
        echo "Unknown mode: $MODE"
        echo "Usage:"
        echo "  $0 backup [INDICES_DIR] [BACKUP_DIR]"
        echo "  $0 restore <backup.tar.gz> [TARGET_DIR]"
        echo "  $0 list [BACKUP_DIR]"
        exit 1
        ;;
esac
