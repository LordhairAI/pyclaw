import os
from langgraph.store.postgres import PostgresStore, AsyncPostgresStore
from psycopg import Connection
from psycopg import connect
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv()

password = quote_plus(os.getenv("PG_CONFIG_PASSWORD"))
conn_string = f"postgresql://{os.getenv('PG_CONFIG_USERNAME')}:{password}@{os.getenv('PG_CONFIG_HOST')}:{os.getenv('PG_CONFIG_PORT')}/{os.getenv('PG_CONFIG_DATABASE')}?sslmode=disable"

with Connection.connect(conn_string) as conn:
    conn.autocommit = True
    store = PostgresStore(conn)
    store.setup()
