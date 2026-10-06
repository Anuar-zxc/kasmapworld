# PostgreSQL 16 + PostGIS 3.4 + h3-pg + pgvector.
# postgis/postgis is built on the official postgres image, which ships with the
# apt.postgresql.org (PGDG) repository where h3-pg and pgvector are packaged.
FROM postgis/postgis:16-3.4

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      postgresql-16-h3 \
      postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*

COPY init/ /docker-entrypoint-initdb.d/
