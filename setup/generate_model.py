import psycopg2
from psycopg2 import sql
from psycopg2.extras import DictCursor
import os
import glob
import json
import sys
import argparse
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
'''
1. 生成模型时检测表中是否有'created_at'、'updated_at'、'deleted_at'字段。
2. 如果字段存则'created_at'、'updated_at'字段增加默认值和更新值。
3. 'deleted_at'字段为软删除字段，查询数据时默认只查询'deleted_at' = null的数据。

'''
# 类型映射字典
TYPE_MAPPING = {
    'integer': 'Integer',
    'bigint': 'BigInteger',
    'smallint': 'SmallInteger',
    'character varying': 'String',
    'varchar': 'String',
    'text': 'Text',
    'timestamp without time zone': 'TIMESTAMP',
    'timestamp with time zone': 'TIMESTAMP',
    'boolean': 'Boolean',
    'numeric': 'Numeric',
    'real': 'Float',
    'double precision': 'Float',
    'json': 'JSON',
    'jsonb': 'JSONB',
    'date': 'Date',
    'time without time zone': 'Time',
    'time with time zone': 'Time',
    'uuid': 'UUID',
}

def snake_to_camel(name):
    """将蛇形命名转换为小驼峰命名"""
    parts = name.split('_')
    return parts[0].lower() + ''.join(x.title() for x in parts[1:])

def check_model_needs_update(file_path, table_info):
    """检查模型文件是否需要更新
    
    通过比较文件修改时间和表结构信息来决定是否需要更新模型文件
    如果文件不存在或表结构有变化，则需要更新
    
    Args:
        file_path: 模型文件路径
        table_info: 表结构信息
        
    Returns:
        bool: 是否需要更新
    """
    if not os.path.exists(file_path):
        return True
        
    # 这里可以实现更复杂的检查逻辑，例如解析现有模型文件并与表结构比较
    # 简单起见，我们假设如果文件存在就不需要更新
    # 在实际应用中，可以根据需要扩展这个函数
    return False

# 表结构缓存文件路径
SCHEMA_CACHE_FILE = 'app/db/db_schema_cache.json'

# 忽略文件列表，这些文件不会被删除
IGNORED_FILES = ['formattedDateTime.py','db_session.py','ragDocumentChunk.py']

def get_existing_models():
    """获取现有模型文件信息"""
    models_dir = 'app/db'
    existing_models = {}
    ignored_models = {}
    
    # 检查目录是否存在
    if os.path.exists(models_dir):
        # 获取所有模型文件
        for py_file in glob.glob(f"{models_dir}/*.py"):
            filename = os.path.basename(py_file)
            # 排除基础文件和忽略列表中的文件
            if filename not in ['__init__.py', 'base.py']:
                model_name = os.path.splitext(filename)[0]
                # 将小驼峰转换回蛇形命名，用于与数据库表名匹配
                table_name = ''.join(['_' + c.lower() if c.isupper() else c for c in model_name]).lstrip('_')
                
                # 检查是否在忽略列表中
                if filename in IGNORED_FILES:
                    ignored_models[table_name] = py_file
                    print(f"忽略文件: {filename}")
                else:
                    existing_models[table_name] = py_file
    else:
        print(f"目录不存在，将创建: {models_dir}")
        os.makedirs(models_dir, exist_ok=True)
    
    return existing_models, ignored_models

def save_schema_to_cache(tables_data):
    """保存表结构信息到本地缓存文件"""
    try:
        with open(SCHEMA_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(tables_data, f, ensure_ascii=False, indent=2)
        print(f"表结构信息已保存到缓存文件: {SCHEMA_CACHE_FILE}")
        return True
    except Exception as e:
        print(f"保存表结构信息到缓存文件失败: {e}")
        return False

def load_schema_from_cache():
    """从本地缓存文件加载表结构信息"""
    try:
        if not os.path.exists(SCHEMA_CACHE_FILE):
            print(f"缓存文件不存在: {SCHEMA_CACHE_FILE}")
            return None
            
        with open(SCHEMA_CACHE_FILE, 'r', encoding='utf-8') as f:
            tables_data = json.load(f)
        
        print(f"已从缓存文件加载表结构信息: {SCHEMA_CACHE_FILE}")
        print(f"缓存包含 {len(tables_data)} 个表的结构信息")
        return tables_data
    except Exception as e:
        print(f"从缓存文件加载表结构信息失败: {e}")
        return None

def fetch_database_schema():
    """从数据库获取表结构信息"""
    tables_data = {}
    
    try:
        print(f"正在连接到PostgreSQL数据库 {os.getenv("PG_CONFIG_HOST")}...")
        
        conn = psycopg2.connect(
            host=os.getenv("PG_CONFIG_HOST"),
            port=os.getenv("PG_CONFIG_PORT"),
            dbname=os.getenv("PG_CONFIG_DATABASE"),
            user=os.getenv("PG_CONFIG_USERNAME"),
            password=os.getenv("PG_CONFIG_PASSWORD"),
            connect_timeout=10  # 添加连接超时
        )
        print("数据库连接成功！")
        
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取所有表名
            print("正在获取数据库表信息...")
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            tables = [row['table_name'] for row in cur.fetchall()]
            print(f"找到 {len(tables)} 个表")
            
            # 获取每个表的结构信息
            for table in tables:
                print(f"获取表结构: {table}")
                tables_data[table] = {'columns': [], 'primary_keys': []}
                
                # 获取表的所有列信息
                cur.execute("""
                    SELECT 
                        column_name, 
                        data_type, 
                        is_nullable, 
                        column_default,
                        character_maximum_length,
                        numeric_precision,
                        numeric_scale,
                        (SELECT pg_catalog.col_description(c.oid, cols.ordinal_position::int)
                         FROM pg_catalog.pg_class c
                         WHERE c.oid = (SELECT cols.table_name::regclass::oid)
                         AND c.relname = cols.table_name) as column_comment
                    FROM information_schema.columns cols
                    WHERE table_name = %s
                    ORDER BY ordinal_position
                """, (table,))
                tables_data[table]['columns'] = [dict(row) for row in cur.fetchall()]
                
                # 获取主键信息
                cur.execute("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    WHERE tc.table_name = %s
                    AND tc.constraint_type = 'PRIMARY KEY'
                """, (table,))
                tables_data[table]['primary_keys'] = [row[0] for row in cur.fetchall()]
        
        conn.close()
        return tables_data
    except psycopg2.OperationalError as e:
        print(f"数据库连接失败: {e}")
        print("\n请检查以下可能的问题:")
        print("1. 数据库服务器是否可访问")
        print("2. 网络连接是否正常")
        print("3. 数据库凭据是否正确")
        print("4. 防火墙是否阻止了连接")
        print("\n您可以使用 --offline 选项从缓存生成模型，或更新config.py中的PG_CONFIG配置并重试。")
        return None

def generate_models(offline=False, update_cache=False):
    """生成SQLAlchemy模型文件"""
    # 获取现有模型文件信息，包括忽略的模型
    existing_models, ignored_models = get_existing_models()
    
    # 获取表结构信息
    tables_data = None
    
    if offline:
        # 离线模式：从缓存加载表结构
        print("使用离线模式...")
        tables_data = load_schema_from_cache()
        if not tables_data:
            print("错误: 无法从缓存加载表结构信息。请先使用 --update-cache 选项更新缓存。")
            return
    else:
        # 在线模式：从数据库获取表结构
        tables_data = fetch_database_schema()
        if not tables_data:
            return
        
        # 如果指定了更新缓存，则保存表结构到缓存
        if update_cache:
            save_schema_to_cache(tables_data)
    
    models_dir = 'app/db'
    
    # 确保输出目录存在
    os.makedirs(models_dir, exist_ok=True)
    
    # 创建或更新__init__.py文件
    with open(f'{models_dir}/__init__.py', 'w') as f:
        f.write('# Auto-generated models package\n')
        #f.write(f'# Generated at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write('from sqlalchemy.orm import relationship\n')
        f.write('from .base import Base\n\n')
        
    # 创建或更新base.py文件
    with open(f'{models_dir}/base.py', 'w') as f:
        f.write('''from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
''')
    
    # 生成模型文件
    print("开始检查和更新模型文件...")
    
    # 跟踪已处理的表，用于后续删除多余模型
    processed_tables = set()
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    
    for table, table_info in tables_data.items():
        processed_tables.add(table)
        
        columns = table_info['columns']
        primary_keys = table_info['primary_keys']
        
        # 生成模型文件
        class_name = ''.join(x.title() for x in table.split('_'))
        file_name = f"app/db/{snake_to_camel(table)}.py"
        
        # 检查模型文件是否已存在且需要更新
        if table in existing_models:
            if check_model_needs_update(file_name, table_info):
                print(f"更新模型: {table}")
                updated_count += 1
            else:
                print(f"模型无需更新: {table}")
                unchanged_count += 1
                continue  # 跳过不需要更新的模型
        else:
            print(f"创建新模型: {table}")
            created_count += 1
        
        with open(file_name, 'w', encoding='utf-8') as f:
            # 写入文件头
            f.write(f'''# Auto-generated at {datetime.now().isoformat()}
from datetime import datetime
from sqlalchemy import ForeignKey, Column, Integer, BigInteger, SmallInteger, String, Text, TIMESTAMP, Boolean, Numeric, Float, Date, Time, JSON, text
from sqlalchemy.orm import relationship
from .base import Base

class {class_name}(Base):
    __tablename__ = '{table}'
    
''')
            # 写入各列
            for col in columns:
                col_name = col['column_name']
                data_type = col['data_type']
                is_nullable = col['is_nullable'] == 'YES'
                col_default = col['column_default']
                col_comment = col['column_comment']
                
                # 处理类型映射
                sqlalchemy_type = TYPE_MAPPING.get(data_type, 'String')
                
                # 处理字符串长度
                type_args = []
                if data_type in ('character varying', 'varchar') and col['character_maximum_length']:
                    type_args.append(str(col['character_maximum_length']))
                elif data_type == 'numeric' and col['numeric_precision']:
                    if col['numeric_scale']:
                        type_args.append(f"precision={col['numeric_precision']}, scale={col['numeric_scale']}")
                    else:
                        type_args.append(f"precision={col['numeric_precision']}")
                
                type_str = f"{sqlalchemy_type}({', '.join(type_args)})" if type_args else sqlalchemy_type
                
                # 处理列属性
                col_attrs = []
                if col_name in primary_keys:
                    col_attrs.append('primary_key=True')
                if not is_nullable:
                    col_attrs.append('nullable=False')
                if col_comment:
                    col_attrs.append(f"comment='{col_comment}'")

                # 特殊处理时间戳字段
                if col_name == 'created_at':
                    # 为 created_at 字段添加默认值
                    col_attrs.append("server_default=text('CURRENT_TIMESTAMP')")
                elif col_name == 'updated_at':
                    # 为 updated_at 字段添加默认值和更新值
                    col_attrs.append("server_default=text('CURRENT_TIMESTAMP')")
                    col_attrs.append("onupdate=text('CURRENT_TIMESTAMP')")
                elif col_name == 'deleted_at':
                    # deleted_at 字段保持默认为 NULL，查询时需要特殊处理
                    pass

                # 处理默认值
                elif col_default:
                    if 'nextval' in col_default:
                        col_attrs.append('server_default=text("{}")'.format(col_default))
                    else:
                        col_attrs.append('server_default="{}"'.format(col_default))
                
                # 写入列定义
                camel_name = snake_to_camel(col_name)
                f.write(f"    {camel_name} = Column('{col_name}', {type_str}")
                if col_attrs:
                    f.write(', ' + ', '.join(col_attrs))
                f.write(')\n\n')
            
            f.write('\n')
    
    # 检查并删除多余的模型文件，但忽略列表中的文件不删除
    removed_count = 0
    ignored_count = 0
    
    for table_name, model_file in existing_models.items():
        if table_name not in processed_tables:
            # 检查文件名是否在忽略列表中
            filename = os.path.basename(model_file)
            if filename in IGNORED_FILES:
                print(f"忽略多余模型(在忽略列表中): {table_name}")
                ignored_count += 1
            else:
                print(f"删除多余模型: {table_name}")
                os.remove(model_file)
                removed_count += 1
    
    print(f"\n模型更新完成！")
    print(f"- 创建新模型: {created_count} 个")
    print(f"- 更新现有模型: {updated_count} 个")
    print(f"- 删除多余模型: {removed_count} 个")
    print(f"- 忽略多余模型: {ignored_count} 个")
    print(f"- 保持不变: {unchanged_count} 个")

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='生成PostgreSQL数据库的SQLAlchemy模型文件')
    parser.add_argument('--offline', action='store_true', help='使用离线模式，从缓存文件加载表结构信息')
    parser.add_argument('--update-cache', action='store_true', help='更新表结构缓存文件')
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_arguments()
    generate_models(offline=args.offline, update_cache=args.update_cache)