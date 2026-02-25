import os
#from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.postgres import PostgresSaver
from dotenv import load_dotenv
from urllib.parse import quote_plus
load_dotenv()

password = quote_plus(os.getenv("PG_CONFIG_PASSWORD"))
conn_string = f"postgresql://{os.getenv('PG_CONFIG_USERNAME')}:{password}@{os.getenv('PG_CONFIG_HOST')}:{os.getenv('PG_CONFIG_PORT')}/{os.getenv('PG_CONFIG_DATABASE')}?sslmode=disable"
with PostgresSaver.from_conn_string(conn_string) as checkpointer:  
    checkpointer.setup()