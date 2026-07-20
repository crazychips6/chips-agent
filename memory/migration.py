"""Memory Migration — 旧格式迁移到 Oracle 架构

将 MEMORY.md/USER.md/EPISODIC.md 迁移到 SQLite 存储。
"""

from __future__ import annotations

import datetime
import logging
import os
import re
from pathlib import Path

from memory.models import MemoryEntry, MemoryScope, MemoryType, generate_memory_id
from memory.storage import SQLiteStorageAdapter

logger = logging.getLogger("chips.memory.migration")

ENTRY_DELIMITER = "\n§\n"


def read_entries(path: str) -> list[str]:
    """读取旧格式的记忆条目。"""
    if not os.path.exists(path):
        return []
    try:
        raw = open(path, encoding="utf-8").read().strip()
    except OSError:
        return []
    if not raw:
        return []
    entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
    return [e for e in entries if e]


def map_memory_type(target: str) -> MemoryType:
    """将旧的目标类型映射到新的 MemoryType。"""
    type_map = {
        "memory": MemoryType.USER_FACT,
        "user": MemoryType.USER_PREFERENCE,
        "episodic": MemoryType.EPISODIC,
    }
    return type_map.get(target, MemoryType.USER_FACT)


def migrate_memory_files(memory_dir: str, user_id: str, 
                         db_path: str = ".chips/memory_oracle.db") -> int:
    """迁移旧的 MEMORY.md/USER.md 到新格式。
    
    返回迁移的条目数量。
    """
    storage = SQLiteStorageAdapter(db_path)
    migrated = 0
    
    for target in ["memory", "user", "episodic"]:
        old_path = os.path.join(memory_dir, f"{target.upper()}.md")
        if not os.path.exists(old_path):
            continue
        
        entries = read_entries(old_path)
        if not entries:
            continue
        
        logger.info("migrating %s: %d entries", target, len(entries))
        
        for content in entries:
            # 提取时间戳（如果是 episodic 格式）
            timestamp = None
            if target == "episodic":
                match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]", content)
                if match:
                    try:
                        timestamp = datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
                        content = content[match.end():].strip()
                    except ValueError:
                        pass
            
            # 创建新条目
            entry = MemoryEntry(
                id=generate_memory_id(),
                content=content,
                memory_type=map_memory_type(target),
                user_id=user_id,
                source="migration",
                confidence=1.0,
                importance=0.8 if target == "user" else 0.6,
            )
            
            if timestamp:
                entry.created_at = timestamp
                entry.updated_at = timestamp
            
            storage.save(entry)
            migrated += 1
    
    logger.info("migration complete: %d entries migrated", migrated)
    return migrated


def backup_memory_files(memory_dir: str) -> str | None:
    """备份旧的记忆文件。"""
    backup_dir = os.path.join(memory_dir, "backup_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    
    files_to_backup = ["MEMORY.md", "USER.md", "EPISODIC.md"]
    backed_up = False
    
    for filename in files_to_backup:
        src = os.path.join(memory_dir, filename)
        if os.path.exists(src):
            os.makedirs(backup_dir, exist_ok=True)
            dst = os.path.join(backup_dir, filename)
            with open(src, "r", encoding="utf-8") as f:
                content = f.read()
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
            backed_up = True
    
    if backed_up:
        logger.info("backup created: %s", backup_dir)
        return backup_dir
    return None
