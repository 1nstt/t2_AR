"""Conexión a PostGIS / pgRouting (contenedor `db` de compose.yaml).
Usa las variables PGHOST, PGPORT, PGDATABASE, PGUSER y PGPASSWORD."""
import os
from pathlib import Path

import pandas as pd
import psycopg
from sqlalchemy import create_engine

DIRECTORIO_BASE = Path(__file__).resolve().parent.parent
DIRECTORIO_SQL = DIRECTORIO_BASE / "sql" / "topologia"


def _dsn():
    return (f"host={os.environ.get('PGHOST', 'localhost')} port={os.environ.get('PGPORT', '5432')} "
            f"dbname={os.environ['PGDATABASE']} user={os.environ['PGUSER']} "
            f"password={os.environ['PGPASSWORD']}")


def motor():
    return create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(_dsn()))


def conexion():
    con = psycopg.connect(_dsn(), autocommit=True)
    con.add_notice_handler(lambda d: print(f"   [pg] {d.message_primary}"))
    return con


def ejecutar_sql(con, nombre_archivo):
    """Ejecuta un archivo de sql/topologia/ completo."""
    print(f"-> SQL: {nombre_archivo}")
    con.execute((DIRECTORIO_SQL / nombre_archivo).read_text(encoding="utf-8"))


def consulta(con, sql, params=None):
    cur = con.execute(sql, params)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
