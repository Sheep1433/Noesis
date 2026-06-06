#!/usr/bin/env python3
"""
数据库初始化脚本

用法：
    python initialize_mysql.py                    # 执行 init_sql.sql
    python initialize_mysql.py --dry-run        # 仅打印 SQL 不执行

执行前请确保：
1. MySQL 服务已启动
2. 配置正确（见 config/env.py）
3. 用户有创建数据库和表的权限
"""

import argparse
import subprocess
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
SQL_FILE = PROJECT_ROOT / "sql" / "init_sql.sql"


def main():
    parser = argparse.ArgumentParser(description="初始化 MySQL 数据库")
    parser.add_argument("--dry-run", action="store_true", help="仅打印 SQL 不执行")
    args = parser.parse_args()

    # 检查 SQL 文件是否存在
    if not SQL_FILE.exists():
        print(f"错误: SQL 文件不存在: {SQL_FILE}", file=sys.stderr)
        sys.exit(1)

    # 读取 SQL 文件
    sql_content = SQL_FILE.read_text(encoding="utf-8")

    if args.dry_run:
        print("=" * 60)
        print("DRY-RUN 模式：仅打印 SQL")
        print("=" * 60)
        print(f"文件: {SQL_FILE}")
        print("=" * 60)
        print(sql_content)
        print("=" * 60)
        print("DRY-RUN 完成，未执行任何操作")
        return

    # 执行 SQL
    print(f"正在执行 SQL 文件: {SQL_FILE}")
    print("=" * 60)

    try:
        # 使用 mysql 命令行工具执行
        # 注意：实际使用时需要配置正确的连接参数
        result = subprocess.run(
            ["mysql", "-u", "root", "-p", "--default-character-set=utf8mb4"],
            input=sql_content,
            capture_output=True,
            text=True,
            check=True,
        )
        print("SQL 执行成功！")
        if result.stdout:
            print("输出:", result.stdout)

    except subprocess.CalledProcessError as e:
        print(f"SQL 执行失败: {e}", file=sys.stderr)
        print(f"错误输出: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("错误: 未找到 mysql 命令", file=sys.stderr)
        print("请确保 MySQL 客户端已安装并配置在 PATH 中", file=sys.stderr)
        print()
        print("或者手动执行 SQL 文件:")
        print(f"    mysql -u root -p --default-character-set=utf8mb4 < {SQL_FILE}")
        sys.exit(1)


if __name__ == "__main__":
    main()
