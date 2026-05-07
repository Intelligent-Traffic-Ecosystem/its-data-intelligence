import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add src/ to path so shared models can be imported (can mport from src)#can access shared model
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.models import Base

config = context.config #alembic.ini config object, read from alembic.ini file in the migrations directory
fileConfig(config.config_file_name)

target_metadata = Base.metadata #told database structureto alembc


def run_migrations_online():
    #connecton create
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    #connecton open 
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()#apply step by step 001.002.003 


run_migrations_online() # when start program auto run ths
